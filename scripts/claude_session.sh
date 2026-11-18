#!/usr/bin/env bash
# Real Claude Code session driving the MCP server through --mcp-config (headless `claude -p`).
# Two runs: (1) investigate + preview a change, (2) operator approval -> confirmed write.
# Writes docs/claude-code-session.md and docs/claude-code-audit.jsonl. Uses a fresh SYNTHETIC db.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
"$REPO/.venv/bin/python" -m ops_server.seed "$WORK/demo.db" >/dev/null
cat > "$WORK/mcp.json" <<JSON
{"mcpServers": {"ops": {"type": "stdio", "command": "$REPO/.venv/bin/mcp-ops-server", "args": [],
  "env": {"OPS_DB": "$WORK/demo.db", "OPS_AUDIT_LOG": "$WORK/audit.jsonl", "OPS_ACTOR": "claude-code"}}}}
JSON
MODEL="${MODEL:-claude-haiku-5-5}"
run() {  # run <outfile> <prompt>
  (cd "$WORK" && env -u ANTHROPIC_API_KEY claude -p --mcp-config mcp.json --strict-mcp-config \
     --allowedTools "mcp__ops__*" --tools "" --model "$MODEL" --output-format stream-json --verbose \
     --no-session-persistence --setting-sources project --disable-slash-commands "$2" < /dev/null > "$1")
}
P1="Use the ops MCP server (synthetic demo data). Find the most urgent open ticket, read it plus the customer and the order it references, and summarise the situation in under 100 words. Then propose moving it to pending by calling update_ticket_status WITHOUT confirm, and show me the preview. Do not confirm the change."
run "$WORK/s1.jsonl" "$P1"
TICKET="$(python3 -c "import json,sys;[print(json.loads(l)['message']['content'][0]['input']['ticket_id']) for l in open(sys.argv[1]) if '\"update_ticket_status\"' in l and '\"tool_use\"' in l]" "$WORK/s1.jsonl" | head -1)"
P2="I am the operator and I approve this change: move ticket $TICKET to pending with the note 'Order shows shipped; asked the customer to check with their reception desk.' Get a fresh preview first (no confirm), then apply it with confirm=true and the confirmation_token from that preview. Report what was written."
run "$WORK/s2.jsonl" "$P2"
OUT="$REPO/docs/claude-code-session.md"
{
  echo "# Claude Code session using mcp-ops-server"
  echo
  echo "Recorded $(date -u '+%Y-%m-%d %H:%M UTC') with \`claude -p --mcp-config\` (model \`$MODEL\`), on a fresh SYNTHETIC database."
  echo "Only the \`mcp__ops__*\` tools were available (\`--tools \"\"\` removes Claude Code's built-in tools)."
  echo
  "$REPO/.venv/bin/python" "$REPO/scripts/format_session.py" "Run 1: investigate and preview" "$P1" "$WORK/s1.jsonl"
  "$REPO/.venv/bin/python" "$REPO/scripts/format_session.py" "Run 2: operator approves, Claude confirms" "$P2" "$WORK/s2.jsonl"
  echo "## Database after the session"
  echo
  echo '```'
  "$REPO/.venv/bin/python" -c "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);print(c.execute('select id,status,updated_at from tickets where id=?',(int(sys.argv[2]),)).fetchall());print(c.execute('select ticket_id,author,body from ticket_notes').fetchall())" "$WORK/demo.db" "$TICKET"
  echo '```'
} > "$OUT"
cp "$WORK/audit.jsonl" "$REPO/docs/claude-code-audit.jsonl"
echo "wrote $OUT (work dir $WORK)"
