#!/usr/bin/env python3
"""Capture the curses interface as a PNG.

The script runs the app in a detached tmux session, captures the screen with
its ANSI sequences (``tmux capture-pane -e``), turns the rendering into
faithful HTML (256 colors, Unicode blocks) and rasterizes it with headless
Chromium.

Usage:
    python3 tools/screenshot.py --out docs/screenshot.png
    python3 tools/screenshot.py --out docs/demo.png --app-args "--demo --theme dub"
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Largeur d'un caractère (px) et hauteur de ligne pour Noto Sans Mono à 18 px.
CELL_W = 10.8
CELL_H = 18.0
FONT_PX = 18.0
PADDING_X = 18
PADDING_Y = 16
CHROME_H = 40
MARGIN = 26

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_ESC_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Palette xterm 256 -> #rrggbb.
_BASE16 = [
    (0, 0, 0), (128, 0, 0), (0, 128, 0), (128, 128, 0),
    (0, 0, 128), (128, 0, 128), (0, 128, 128), (192, 192, 192),
    (128, 128, 128), (255, 0, 0), (0, 255, 0), (255, 255, 0),
    (0, 0, 255), (255, 0, 255), (0, 255, 255), (255, 255, 255),
]
_CUBE = (0, 95, 135, 175, 215, 255)


def xterm_hex(index: int) -> str:
    i = max(0, min(255, int(index)))
    if i < 16:
        r, g, b = _BASE16[i]
    elif i < 232:
        i -= 16
        r, g, b = _CUBE[i // 36], _CUBE[(i // 6) % 6], _CUBE[i % 6]
    else:
        level = 8 + (i - 232) * 10
        r = g = b = level
    return f"#{r:02x}{g:02x}{b:02x}"


class Style:
    __slots__ = ("fg", "bg", "bold", "dim", "italic", "underline", "reverse")

    def __init__(self) -> None:
        self.fg: int | None = None
        self.bg: int | None = None
        self.bold = False
        self.dim = False
        self.italic = False
        self.underline = False
        self.reverse = False

    def reset(self) -> None:
        self.__init__()

    def key(self) -> tuple:
        return (self.fg, self.bg, self.bold, self.dim, self.italic,
                self.underline, self.reverse)

    def css(self) -> str:
        fg, bg = self.fg, self.bg
        if self.reverse:
            fg, bg = bg, fg
        props = []
        if fg is not None:
            props.append(f"color:{xterm_hex(fg)}")
        if bg is not None:
            props.append(f"background-color:{xterm_hex(bg)}")
        if self.bold:
            props.append("font-weight:700")
        if self.italic:
            props.append("font-style:italic")
        if self.dim:
            props.append("opacity:0.55")
        if self.underline:
            props.append("text-decoration:underline")
        return ";".join(props)


def _apply_sgr(style: Style, params: str) -> None:
    codes = [int(p) if p else 0 for p in params.split(";")] if params else [0]
    i = 0
    while i < len(codes):
        code = codes[i]
        if code == 0:
            style.reset()
        elif code == 1:
            style.bold = True
        elif code == 2:
            style.dim = True
        elif code == 3:
            style.italic = True
        elif code == 4:
            style.underline = True
        elif code == 7:
            style.reverse = True
        elif code == 21:
            style.bold = False
        elif code == 22:
            style.bold = False
            style.dim = False
        elif code == 23:
            style.italic = False
        elif code == 24:
            style.underline = False
        elif code == 27:
            style.reverse = False
        elif 30 <= code <= 37:
            style.fg = code - 30
        elif code == 39:
            style.fg = None
        elif 40 <= code <= 47:
            style.bg = code - 40
        elif code == 49:
            style.bg = None
        elif 90 <= code <= 97:
            style.fg = code - 90 + 8
        elif 100 <= code <= 107:
            style.bg = code - 100 + 8
        elif code in (38, 48):
            target = "fg" if code == 38 else "bg"
            if i + 1 < len(codes):
                mode = codes[i + 1]
                if mode == 5 and i + 2 < len(codes):
                    setattr(style, target, codes[i + 2])
                    i += 2
                elif mode == 2 and i + 4 < len(codes):
                    setattr(style, target, _rgb_to_xterm(codes[i + 2], codes[i + 3], codes[i + 4]))
                    i += 4
        i += 1


def _rgb_to_xterm(r: int, g: int, b: int) -> int:
    def nearest(colors):
        return min(range(len(colors)), key=lambda k: (colors[k][0] - r) ** 2 + (colors[k][1] - g) ** 2 + (colors[k][2] - b) ** 2)

    base = nearest(_BASE16)
    cube_index = nearest([(cr, cg, cb) for cr in _CUBE for cg in _CUBE for cb in _CUBE])
    ci, cr, cg, cb = cube_index, _CUBE[cube_index // 36], _CUBE[(cube_index // 6) % 6], _CUBE[cube_index % 6]
    cube = 16 + ci
    dist_base = (_BASE16[base][0] - r) ** 2 + (_BASE16[base][1] - g) ** 2 + (_BASE16[base][2] - b) ** 2
    dist_cube = (cr - r) ** 2 + (cg - g) ** 2 + (cb - b) ** 2
    return base if dist_base <= dist_cube else cube


def ansi_to_html(ansi: str) -> str:
    output: list[str] = []
    style = Style()
    for raw_line in ansi.split("\n"):
        line = raw_line.rstrip("\r")
        spans: list[tuple[str, Style]] = []
        buffer: list[str] = []
        display_style = Style()
        display_style.fg, display_style.bg = style.fg, style.bg
        display_style.bold, display_style.dim = style.bold, style.dim
        display_style.italic, display_style.underline, display_style.reverse = (
            style.italic, style.underline, style.reverse)
        pos = 0
        while pos < len(line):
            if line[pos] == "\x1b":
                match = _SGR_RE.match(line, pos)
                if match:
                    if buffer:
                        spans.append(("".join(buffer), _copy(display_style)))
                        buffer = []
                    _apply_sgr(style, match.group(1))
                    display_style = _copy(style)
                    pos = match.end()
                    continue
                esc = _ESC_RE.match(line, pos)
                pos = esc.end() if esc else pos + 1
                continue
            buffer.append(line[pos])
            pos += 1
        if buffer:
            spans.append(("".join(buffer), _copy(display_style)))
        if not spans:
            spans = [("", _copy(display_style))]
        parts = []
        for text, span_style in spans:
            escaped = html.escape(text)
            css = span_style.css()
            parts.append(f'<span style="{css}">{escaped}</span>' if css else escaped)
        output.append("".join(parts))
    return "\n".join(output)


def _copy(style: Style) -> Style:
    clone = Style()
    clone.fg, clone.bg = style.fg, style.bg
    clone.bold, clone.dim = style.bold, style.dim
    clone.italic, clone.underline = style.italic, style.underline
    clone.reverse = style.reverse
    return clone


def build_html(ansi: str, cols: int, rows: int, title: str) -> str:
    body = ansi_to_html(ansi)
    px_w = round(cols * CELL_W) + 2 * PADDING_X
    px_h = round(rows * CELL_H) + 2 * PADDING_Y
    return f"""<!doctype html>
<html lang="fr"><head><meta charset="utf-8">
<style>
  html, body {{ margin:0; padding:0; background:#070a08; }}
  .term {{ margin:{MARGIN}px; border-radius:12px; overflow:hidden;
           border:1px solid #26332b; background:#0b0f0d;
           box-shadow:0 24px 80px rgba(0,0,0,.65); width:max-content; }}
  .chrome {{ height:{CHROME_H}px; display:flex; align-items:center; gap:8px;
             padding:0 14px; background:#111713; border-bottom:1px solid #26332b; }}
  .dot {{ width:11px; height:11px; border-radius:50%; }}
  .title {{ margin-left:8px; color:#8fa596; font:12px "Noto Sans Mono", monospace; }}
  pre {{ margin:0; padding:{PADDING_Y}px {PADDING_X}px; color:#d8e2da;
         font-family:"Noto Sans Mono","DejaVu Sans Mono",monospace;
         font-size:{FONT_PX}px; line-height:1.0; letter-spacing:0;
         width:{px_w}px; box-sizing:border-box; }}
</style></head>
<body><div class="term">
  <div class="chrome">
    <span class="dot" style="background:#e0605a"></span>
    <span class="dot" style="background:#e0b34f"></span>
    <span class="dot" style="background:#69b46a"></span>
    <span class="title">{html.escape(title)}</span>
  </div>
  <pre>{body}</pre>
</div></body></html>"""


def capture_ansi(app_args: str, cols: int, rows: int, wait: float) -> str:
    session = "radiocli-shot"
    subprocess.run(["tmux", "-L", "grs", "kill-server"], capture_output=True)
    command = (
        f"cd {ROOT} && ./radiocli {app_args} "
        f"--duration {int(wait) + 20}"
    )
    subprocess.run(
        ["tmux", "-L", "grs", "new-session", "-d", "-s", session,
         "-x", str(cols), "-y", str(rows), command],
        check=True,
    )
    try:
        time.sleep(wait)
        result = subprocess.run(
            ["tmux", "-L", "grs", "capture-pane", "-p", "-e", "-t", session],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.rstrip("\n")
    finally:
        subprocess.run(["tmux", "-L", "grs", "kill-server"], capture_output=True)


def render_png(html_path: Path, png_path: Path, cols: int, rows: int) -> None:
    chromium = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome-stable")
    if chromium is None:
        raise SystemExit("chromium not found: HTML kept but no PNG generated")
    width = round(cols * CELL_W) + 2 * PADDING_X + 2 * MARGIN + 8
    height = round(rows * CELL_H) + 2 * PADDING_Y + CHROME_H + 2 * MARGIN + 8
    profile = tempfile.mkdtemp(prefix="grs-chrome-")
    args = [
        chromium,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--user-data-dir={profile}",
        "--force-device-scale-factor=1",
        f"--window-size={width},{height}",
        "--virtual-time-budget=2500",
        f"--screenshot={png_path}",
        html_path.as_uri(),
    ]
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0 or not png_path.exists():
        raise SystemExit(f"chromium failed: {result.stderr[-800:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="docs/screenshot.png")
    parser.add_argument("--app-args", default="--no-audio", help="arguments passed to radiocli")
    parser.add_argument("--size", default="120x40", help="terminal width x height")
    parser.add_argument("--wait", type=float, default=10.0, help="wait before capture (seconds)")
    parser.add_argument(
        "--title", default="radioCLI — webradio player",
    )
    args = parser.parse_args()

    cols, rows = (int(part) for part in args.size.lower().split("x"))
    ansi = capture_ansi(args.app_args, cols, rows, args.wait)
    png_path = (ROOT / args.out).resolve()
    png_path.parent.mkdir(parents=True, exist_ok=True)
    html_path = Path(tempfile.mkstemp(prefix="grs-shot-", suffix=".html")[1])
    html_path.write_text(build_html(ansi, cols, rows, args.title), encoding="utf-8")
    render_png(html_path, png_path, cols, rows)
    print(f"PNG: {png_path}")
    print(f"HTML: {html_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
