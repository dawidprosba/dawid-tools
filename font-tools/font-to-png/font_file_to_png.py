#!/usr/bin/env python3
"""
ttf_to_png.py — Export every glyph in a .ttf/.otf font to its own PNG.

Usage:
    python3 ttf_to_png.py Bravura.ttf --out glyphs/ --size 256

Requires:
    pip install fonttools Pillow

Notes:
- Works with any TTF/OTF, including SMuFL fonts like Bravura. SMuFL glyphs
  live in the Private Use Area (U+E000–U+F8FF) and are read straight from
  the font's own cmap, so no external glyphnames.json is needed.
- Each PNG is named after its glyph name (e.g. "noteheadBlack.png", "A.png").
- Images are sized dynamically to each glyph's own bounding box (with a
  small uniform padding), so glyph dimensions vary — pass --size to set a
  fixed canvas instead.
"""

import argparse
import json
import os
import re
import sys
import termios
import tty
import time
from rich.console import Console, Group
from rich.live import Live
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn, MofNCompleteColumn
from rich.segment import Segment
from rich.text import Text
from rich.style import Style

console = Console()


PRIDE_COLORS = [
    (228, 3,   3),    # red
    (255, 140, 0),    # orange
    (255, 237, 0),    # yellow
    (0,   128, 38),   # green
    (0,   77,  255),  # blue
    (117, 7,   135),  # violet
]


def _pride_color(t: float) -> tuple[int, int, int]:
    """Smooth gradient — used by the progress bar."""
    t = t % 1.0
    n = len(PRIDE_COLORS)
    idx = t * n
    lo = int(idx) % n
    hi = (lo + 1) % n
    f = idx - int(idx)
    r = int(PRIDE_COLORS[lo][0] * (1 - f) + PRIDE_COLORS[hi][0] * f)
    g = int(PRIDE_COLORS[lo][1] * (1 - f) + PRIDE_COLORS[hi][1] * f)
    b = int(PRIDE_COLORS[lo][2] * (1 - f) + PRIDE_COLORS[hi][2] * f)
    return r, g, b


def _pride_stripe(t: float) -> tuple[int, int, int]:
    """Hard stripes — used by the border."""
    return PRIDE_COLORS[int(t % 1.0 * len(PRIDE_COLORS)) % len(PRIDE_COLORS)]


class RainbowBar(BarColumn):
    def render(self, task):
        width = self.bar_width or 40
        filled = int(width * task.completed / (task.total or 1))
        phase = time.time() * 0.4
        bar = Text()
        for i in range(filled):
            r, g, b = _pride_color(i / width + phase)
            bar.append("█", style=Style(color=f"rgb({r},{g},{b})"))
        bar.append("░" * (width - filled), style=Style(color="grey30"))
        return bar
class RainbowPanel:
    def __init__(self, renderable, title: str = "", subtitle: str = "", padding: tuple = (1, 2)):
        self.renderable = renderable
        self.title = title
        self.subtitle = subtitle
        self.pad_v, self.pad_h = padding

    def __rich_console__(self, console: Console, options) -> None:
        width = options.max_width
        phase = time.time() * 0.4

        # Pre-render content so we know the total height before drawing any borders.
        inner_w = width - 2 - self.pad_h * 2
        content_lines = console.render_lines(self.renderable, options.update(width=inner_w), pad=True)
        height = 2 + 2 * self.pad_v + len(content_lines)
        P = 2 * (width - 1) + 2 * (height - 1)  # total perimeter length

        def bc(ch: str, x: int, y: int, bold: bool = False, dim: bool = False) -> Segment:
            # Map (x, y) to a clockwise perimeter position:
            # top → right ↓ → bottom ← → left ↑
            if y == 0:
                perim = x
            elif x == width - 1:
                perim = (width - 1) + y
            elif y == height - 1:
                perim = 2 * (width - 1) + (height - 1) - x
            else:
                perim = 2 * (width - 1) + 2 * (height - 1) - y
            r, g, b = _pride_stripe(perim / P - phase)
            return Segment(ch, Style(color=f"rgb({r},{g},{b})", bold=bold, dim=dim))

        def h_border(y: int, left: str, mid: str, right: str, label: str = ""):
            is_top = (y == 0)
            inner = width - 2
            yield bc(left, 0, y)
            if label:
                label_str = f" {label} "
                llen = len(label_str)
                ln = (inner - llen) // 2
                rn = inner - llen - ln
                for i in range(ln):
                    yield bc(mid, i + 1, y)
                for i, char in enumerate(label_str):
                    yield bc(char, ln + 1 + i, y, bold=is_top, dim=not is_top)
                for i in range(rn):
                    yield bc(mid, ln + llen + i + 1, y)
            else:
                for i in range(inner):
                    yield bc(mid, i + 1, y)
            yield bc(right, width - 1, y)
            yield Segment.line()

        row = 0
        yield from h_border(row, "╭", "─", "╮", self.title)
        row += 1

        for _ in range(self.pad_v):
            yield bc("│", 0, row)
            yield Segment(" " * (width - 2))
            yield bc("│", width - 1, row)
            yield Segment.line()
            row += 1

        for line in content_lines:
            yield bc("│", 0, row)
            yield Segment(" " * self.pad_h)
            yield from line
            yield Segment(" " * self.pad_h)
            yield bc("│", width - 1, row)
            yield Segment.line()
            row += 1

        for _ in range(self.pad_v):
            yield bc("│", 0, row)
            yield Segment(" " * (width - 2))
            yield bc("│", width - 1, row)
            yield Segment.line()
            row += 1

        yield from h_border(row, "╰", "─", "╯", self.subtitle)


from fontTools.ttLib import TTFont
from fontTools.pens.boundsPen import BoundsPen
from PIL import Image, ImageDraw, ImageFont


def safe_filename(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9_.-]', '_', name)


def get_cmap_glyphs(font: TTFont):
    """Return list of (glyph_name, codepoint) for every mapped char."""
    cmap = font.getBestCmap()
    return [(glyph_name, codepoint) for codepoint, glyph_name in cmap.items()]


def render_glyph(font_path, glyph_name, codepoint, out_dir, padding=20, fixed_size=None, upm=1000,
                  uniform_scale=False, px_per_em=200, global_canvas=None):
    char = chr(codepoint)

    if uniform_scale:
        # Same font size (px_per_em) for every glyph -> consistent scale,
        # so a notehead and a staff line stay proportioned to each other
        # the same way they are inside the actual font's em-square.
        img_font = ImageFont.truetype(font_path, size=px_per_em)
    else:
        img_font = ImageFont.truetype(font_path, size=int(upm * 0.75))

    # Measure bounding box for this single character
    tmp_img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
    tmp_draw = ImageDraw.Draw(tmp_img)
    bbox = tmp_draw.textbbox((0, 0), char, font=img_font)

    if bbox is None or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
        return False  # empty/whitespace glyph, skip

    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]

    if uniform_scale:
        # Fixed canvas shared by ALL glyphs. Anchor every glyph at the
        # same baseline-centered point so relative size/position between
        # glyphs (e.g. notehead vs staff line) is preserved.
        canvas_w = canvas_h = global_canvas
        draw_x = (canvas_w / 2) - (bbox[0] + width / 2)
        draw_y = (canvas_h / 2) - (bbox[1] + height / 2)
    elif fixed_size:
        canvas_w = canvas_h = fixed_size
        draw_x = (canvas_w - width) / 2 - bbox[0]
        draw_y = (canvas_h - height) / 2 - bbox[1]
    else:
        canvas_w = width + padding * 2
        canvas_h = height + padding * 2
        draw_x = (canvas_w - width) / 2 - bbox[0]
        draw_y = (canvas_h - height) / 2 - bbox[1]

    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.text((draw_x, draw_y), char, font=img_font, fill=(0, 0, 0, 255))

    out_path = os.path.join(out_dir, safe_filename(glyph_name) + ".png")
    img.save(out_path)
    return True


def _resolve_whitelist(value) -> set[str] | None:
    if not value:
        return None
    if isinstance(value, list):
        return {name.strip() for name in value if name.strip()}
    if os.path.isfile(value):
        with open(value) as f:
            return {line.strip() for line in f if line.strip()}
    return {name.strip() for name in value.split(",") if name.strip()}


def run_export(config: dict, on_progress=None, on_log=None, on_current=None, base_dir=None) -> int:
    """Export glyphs defined in config. Returns the count of PNGs written.

    on_progress(current, total) — called before each glyph
    on_current(glyph_name)      — called with the name of the glyph being rendered
    on_log(msg)                 — called for skipped/error glyphs and final summary
    base_dir                    — directory used to resolve relative paths in config
    """
    def _resolve(path: str) -> str:
        path = os.path.expanduser(path)
        if base_dir and not os.path.isabs(path):
            return os.path.join(base_dir, path)
        return path

    font_path = _resolve(config.get("font_path", ""))
    if not font_path:
        raise ValueError("font_path is required in config")

    out_dir = _resolve(config.get("out", "glyphs"))
    size          = config.get("size")
    padding       = config.get("padding", 20)
    uniform_scale = config.get("uniform_scale", False)
    px_per_em     = config.get("px_per_em", 200)
    canvas        = config.get("canvas", 400)

    os.makedirs(out_dir, exist_ok=True)

    font     = TTFont(font_path)
    upm      = font["head"].unitsPerEm if "head" in font else 1000
    glyphs   = get_cmap_glyphs(font)
    wl       = _resolve_whitelist(config.get("whitelist"))
    if wl:
        glyphs = [(n, cp) for n, cp in glyphs if n in wl]

    total = len(glyphs)
    count = 0
    for i, (glyph_name, codepoint) in enumerate(glyphs):
        if on_progress:
            on_progress(i, total)
        if on_current:
            on_current(glyph_name)
        try:
            ok = render_glyph(
                font_path, glyph_name, codepoint, out_dir,
                padding=padding, fixed_size=size, upm=upm,
                uniform_scale=uniform_scale, px_per_em=px_per_em,
                global_canvas=canvas,
            )
            if ok:
                count += 1
        except Exception as e:
            if on_log:
                on_log(f"skipped {glyph_name} (U+{codepoint:04X}): {e}")

    if on_progress:
        on_progress(total, total)
    if on_log:
        on_log(f"Exported {count} / {total} glyphs → {out_dir}/")
    return count


class ExportRenderer:
    """Rich renderable that owns the export UI inside the runner's rainbow panel."""

    def __init__(self) -> None:
        self.total = 0
        self.current = 0
        self.current_name = ""
        self.logs: list[str] = []
        self.done = False
        self.success = False

    @property
    def footer(self) -> str:
        return "Enter / Esc to return" if self.done else "running…"

    def __rich_console__(self, console: Console, options) -> None:
        phase = time.time() * 0.3
        t = Text()

        # Info line
        t.append(f"  Found {self.total} glyphs to render\n\n", style="bold cyan")

        # Rainbow progress bar
        if self.total > 0:
            bar_width = 40
            filled = int(bar_width * self.current / self.total)
            bar = Text()
            for i in range(filled):
                r, g, b = _pride_color(i / bar_width + phase)
                bar.append("█", style=Style(color=f"rgb({r},{g},{b})"))
            bar.append("░" * (bar_width - filled), style=Style(color="grey30"))
            t.append("  ")
            t.append_text(bar)
            t.append(f"  {self.current}/{self.total}\n", style="dim")

        # Current glyph
        if self.current_name and not self.done:
            t.append(f"  ↳ {self.current_name}\n", style="dim cyan")

        t.append("\n")

        # Logs (skips, errors, final summary)
        for line in self.logs[-20:]:
            t.append(f"  {line}\n", style="dim")

        if self.logs:
            t.append("\n")

        # Status
        if self.done:
            t.append("  ✓ done\n" if self.success else "  ✗ failed\n",
                     style="bold green" if self.success else "bold red")
        else:
            r, g, b = _pride_color(phase)
            t.append("  ● running…\n", style=Style(color=f"rgb({r},{g},{b})", bold=True))

        yield from console.render(t, options)

    def run(self, config: dict, base_dir: str | None = None) -> None:
        """Called in a background thread by the runner."""
        try:
            run_export(
                config,
                on_progress=lambda c, t: setattr(self, "current", c) or setattr(self, "total", t),
                on_current=lambda n: setattr(self, "current_name", n),
                on_log=lambda m: self.logs.append(m),
                base_dir=base_dir,
            )
            self.success = True
        except Exception as e:
            self.logs.append(f"Error: {e}")
            self.success = False
        finally:
            self.done = True


CONFIG_TEMPLATE = {
    "font_path": "",
    "out": "glyphs",
    "size": None,
    "padding": 20,
    "uniform_scale": False,
    "px_per_em": 200,
    "canvas": 400,
    "whitelist": None,
}


def main():
    parser = argparse.ArgumentParser(description="Export every glyph in a font to PNG.")
    parser.add_argument("font_path", nargs="?", help="Path to .ttf or .otf font file")
    parser.add_argument("--out", default="glyphs", help="Output directory (default: glyphs/)")
    parser.add_argument("--size", type=int, default=None,
                         help="Fixed square canvas size in px (default: dynamic, per-glyph bounding box + padding)")
    parser.add_argument("--padding", type=int, default=20,
                         help="Padding in px around each glyph when using dynamic sizing (default: 20)")
    parser.add_argument("--uniform-scale", action="store_true",
                         help="Render all glyphs at the SAME font size on the SAME canvas.")
    parser.add_argument("--px-per-em", type=int, default=200,
                         help="Font size in px used for every glyph in --uniform-scale mode (default: 200)")
    parser.add_argument("--canvas", type=int, default=400,
                         help="Shared canvas size in px for --uniform-scale mode (default: 400)")
    parser.add_argument("--whitelist", default=None,
                         help="Glyph names to render: a path to a .txt file (one per line) "
                              "or a comma-separated list of names.")
    parser.add_argument("--config", default=None,
                         help="Path to a JSON config file. All parameters are read from it.")
    parser.add_argument("--new-config", metavar="PATH",
                         help="Write a blank JSON config template to PATH and exit.")
    args = parser.parse_args()

    if args.new_config:
        with open(args.new_config, "w") as f:
            json.dump(CONFIG_TEMPLATE, f, indent=2)
        console.print(f"[bold green]Config template written to[/] [cyan]{args.new_config}[/]")
        return

    if args.config:
        with open(args.config) as f:
            cfg = json.load(f)
        args.font_path     = cfg.get("font_path", "")
        args.out           = cfg.get("out", CONFIG_TEMPLATE["out"])
        args.size          = cfg.get("size", CONFIG_TEMPLATE["size"])
        args.padding       = cfg.get("padding", CONFIG_TEMPLATE["padding"])
        args.uniform_scale = cfg.get("uniform_scale", CONFIG_TEMPLATE["uniform_scale"])
        args.px_per_em     = cfg.get("px_per_em", CONFIG_TEMPLATE["px_per_em"])
        args.canvas        = cfg.get("canvas", CONFIG_TEMPLATE["canvas"])
        args.whitelist     = cfg.get("whitelist", CONFIG_TEMPLATE["whitelist"])

    if not args.font_path:
        parser.error("font_path is required (as a positional argument or via --config)")

    os.makedirs(args.out, exist_ok=True)

    whitelist = _resolve_whitelist(args.whitelist)

    args.font_path = os.path.expanduser(args.font_path)
    font = TTFont(args.font_path)
    upm = font["head"].unitsPerEm if "head" in font else 1000
    glyphs = get_cmap_glyphs(font)
    if whitelist is not None:
        glyphs = [(name, cp) for name, cp in glyphs if name in whitelist]

    count = 0
    header = Text.from_markup(
        f"[bold cyan]Found {len(glyphs)} glyphs to render[/]"
    )
    current = Text()
    logs = Text()
    status = Text()
    progress = Progress(
        TextColumn("[bold]{task.description}"),
        RainbowBar(bar_width=40),
        MofNCompleteColumn(),
        TextColumn("•"),
        TimeRemainingColumn(),
        console=console,
    )
    panel = RainbowPanel(
        Group(header, progress, current, logs, status),
        title="font → png",
        subtitle="Ctrl+C to cancel",
        padding=(1, 2),
    )
    fd, old_tty = (None, None)
    if sys.stdin.isatty():
        fd = sys.stdin.fileno()
        old_tty = termios.tcgetattr(fd)
        tty.setcbreak(fd)

    try:
        with Live(panel, refresh_per_second=20, console=console, screen=True):
            task = progress.add_task("Rendering glyphs", total=len(glyphs))
            try:
                for glyph_name, codepoint in glyphs:
                    current.truncate(0)
                    current.append(f"  ↳ {glyph_name}", style="dim cyan")
                    try:
                        ok = render_glyph(
                            args.font_path, glyph_name, codepoint, args.out,
                            padding=args.padding, fixed_size=args.size, upm=upm,
                            uniform_scale=args.uniform_scale, px_per_em=args.px_per_em,
                            global_canvas=args.canvas
                        )
                        if ok:
                            count += 1
                    except Exception as e:
                        logs.append(f"  skipped {glyph_name} (U+{codepoint:04X}): {e}\n", style="yellow")
                    progress.advance(task)
                current.truncate(0)
                status.append("Done. ", style="bold green")
                status.append(f"Wrote {count} PNGs to {args.out}/", style="cyan")
            except KeyboardInterrupt:
                status.append("Cancelled. ", style="bold red")
                status.append(f"Wrote {count} PNGs before stopping.", style="dim")
    finally:
        if fd is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_tty)

    # Alternate screen closes on Live exit — print the final panel state to the normal terminal.
    console.print(panel)


if __name__ == "__main__":
    main()
