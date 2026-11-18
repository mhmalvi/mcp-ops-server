"""Render PNG screenshots with headless Chromium (Playwright; install it separately).

python scripts/screenshot.py terminal <capture.txt> <out.png> "<window title>"
python scripts/screenshot.py page <report.html> <out.png>
"""

from __future__ import annotations

import sys
import tempfile
from html import escape
from pathlib import Path

from playwright.sync_api import sync_playwright

TERMINAL_CSS = """
body{margin:0;background:#0b0e13;padding:28px;font-family:'DejaVu Sans Mono',Menlo,monospace}
.win{background:#11151c;border:1px solid #232a35;border-radius:10px;box-shadow:0 20px 50px #0008;width:1240px}
.bar{display:flex;align-items:center;gap:8px;padding:10px 14px;border-bottom:1px solid #232a35;color:#8b95a5;font-size:12px}
.dot{width:12px;height:12px;border-radius:50%}.r{background:#ff5f57}.y{background:#febc2e}.g{background:#28c840}
.t{margin-left:10px}pre{margin:0;padding:16px 18px;color:#d6dde8;font-size:13px;line-height:1.55;white-space:pre-wrap;overflow-wrap:anywhere}
.cmd{color:#7ee787}.err{color:#ff7b72}.cmt{color:#8b95a5}.tool{color:#79c0ff}.ai{color:#f2cc60}
"""


def _colour(line: str) -> str:
    """Wrap one capture line in a span chosen by its prefix."""
    text = escape(line)
    for prefix, cls in (("$ ", "cmd"), ("ERR", "err"), ("# ", "cmt"), ("-> ", "tool"), ("claude:", "ai")):
        if line.startswith(prefix):
            return f'<span class="{cls}">{text}</span>'
    return text


def terminal_html(capture: Path, title: str) -> str:
    """Build a terminal-window page around a plain-text capture."""
    body = "\n".join(_colour(line) for line in capture.read_text(encoding="utf-8").splitlines())
    return (f"<!doctype html><meta charset='utf-8'><style>{TERMINAL_CSS}</style><div class='win'><div class='bar'>"
            f"<span class='dot r'></span><span class='dot y'></span><span class='dot g'></span>"
            f"<span class='t'>{escape(title)}</span></div><pre>{body}</pre></div>")


def shoot(html_path: Path, out: Path, width: int, selector: str | None) -> None:
    """Open a local HTML file and save a PNG (full page, or one element)."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": width, "height": 900}, device_scale_factor=2)
        page.goto(html_path.resolve().as_uri())
        target = page.locator(selector) if selector else page
        target.screenshot(path=str(out), **({} if selector else {"full_page": True}))
        browser.close()


def main() -> None:
    """CLI entry point."""
    mode, src, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    if mode == "terminal":
        tmp = Path(tempfile.mkdtemp()) / "terminal.html"
        tmp.write_text(terminal_html(src, sys.argv[4] if len(sys.argv) > 4 else src.name), encoding="utf-8")
        shoot(tmp, out, 1300, ".win")
    else:
        shoot(src, out, 1440, None)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
