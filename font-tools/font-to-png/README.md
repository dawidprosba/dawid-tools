# 🖋️ font_file_to_png

> Export every glyph in a `.ttf` or `.otf` font to individual PNG files with a transparent background.

Works with any TTF/OTF font. SMuFL fonts are fully supported — SMuFL glyphs in the Private Use Area (U+E000–U+F8FF) are read directly from the font's own cmap with no external `glyphnames.json` needed.

---

> [!WARNING]
> ## ⚠️ DISCLAIMER — READ BEFORE USE
>
> **This tool was 100% vibe coded.**
>
> It was built through iterative prompting and AI-assisted development. No formal testing, no guarantees of correctness, no warranty of any kind — express or implied.
>
> The author **does not take any responsibility** and **cannot be held liable** for any damage, data loss, incorrect output, corrupted files, broken pipelines, wasted time, existential crises, or any other consequences arising from the use or misuse of this tool.
>
> **Use it at your own risk. You have been warned.**

---

## 🚀 Installation

Requires Python 3.10+.

```bash
git clone <repo-url>
cd font_utils/font-tools/font-to-png
make install
```

This creates a `.venv` virtualenv and installs all dependencies (`fonttools`, `Pillow`, `rich`) automatically.

---

## 🛠️ How to use

### With a config file (recommended)

A config file keeps your settings reusable and version-controllable.

#### 1. Generate a template

```bash
make create-config CONFIG=my_config.json
```

#### 2. Fill in your settings

| Key | Default | Description |
|-----|---------|-------------|
| `font_path` | `""` | Path to a `.ttf` or `.otf` font file |
| `out` | `"glyphs"` | Output directory for PNG files |
| `size` | `null` | Fixed square canvas size in px — `null` uses dynamic sizing |
| `padding` | `20` | Padding in px around each glyph (dynamic sizing only) |
| `uniform_scale` | `false` | Render all glyphs at the same font size on a shared canvas |
| `px_per_em` | `200` | Font size in px when `uniform_scale` is enabled |
| `canvas` | `400` | Shared canvas size in px when `uniform_scale` is enabled |
| `whitelist` | `null` | List of glyph names to render, or a path to a `.txt` file — `null` renders all glyphs |

Example filled config:

```json
{
  "font_path": "~/fonts/example.otf",
  "out": "./output",
  "uniform_scale": true,
  "px_per_em": 200,
  "canvas": 400,
  "whitelist": ["noteheadBlack", "noteheadHalf", "augmentationDot"]
}
```

#### 3. Run

```bash
make run-config CONFIG=my_config.json
```

---

### Via CLI flags

Pass everything directly on the command line:

```bash
python font_file_to_png.py <font_path> [options]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--out` | `glyphs/` | Output directory for PNG files |
| `--size` | _dynamic_ | Fixed square canvas size in px |
| `--padding` | `20` | Padding in px around each glyph (dynamic sizing only) |
| `--uniform-scale` | off | Render all glyphs at the same font size on a shared canvas |
| `--px-per-em` | `200` | Font size in px for `--uniform-scale` mode |
| `--canvas` | `400` | Shared canvas size in px for `--uniform-scale` mode |
| `--whitelist` | _none_ | Restrict output to specific glyphs (see [Whitelist](#-whitelist)) |
| `--config` | _none_ | Load all parameters from a JSON config file |
| `--new-config` | _none_ | Write a blank JSON config template to the given path and exit |

---

### Via Makefile

| Target | Description |
|--------|-------------|
| `make run` | Run with `FONT`, `OUT`, and optional `ARGS` |
| `make run-config` | Run using a JSON config file (`CONFIG`) |
| `make create-config` | Generate a blank JSON config template at `CONFIG` |

| Variable | Default | Description |
|----------|---------|-------------|
| `FONT` | `~/fonts/example.otf` | Font path for `make run` |
| `OUT` | `./output` | Output directory for `make run` |
| `ARGS` | _(empty)_ | Extra CLI flags for `make run` |
| `CONFIG` | `config.json` | Config file for `make run-config` / `make create-config` |

---

## 🖼️ Canvas Modes

### Dynamic sizing (default)

Each glyph is cropped to its own bounding box with uniform padding. PNG dimensions vary between glyphs.

```bash
python font_file_to_png.py example.otf --out ./glyphs --padding 20
```

### Fixed canvas (`--size`)

All glyphs are rendered onto the same square canvas with the glyph centered. Useful when you need uniform image dimensions.

```bash
python font_file_to_png.py example.otf --out ./glyphs --size 256
```

### Uniform scale (`--uniform-scale`)

All glyphs are rendered at the same font size on a shared canvas, preserving relative proportions. A notehead and a staff line will appear at the same scale relative to each other as they do inside the font's em-square.

```bash
python font_file_to_png.py example.otf --out ./glyphs --uniform-scale --px-per-em 200 --canvas 400
```

---

## 📝 Whitelist

Restrict rendering to a specific set of glyph names. Glyphs not in the whitelist are silently skipped. Omit entirely to render all glyphs.

### In a config file

As a list:
```json
{ "whitelist": ["noteheadBlack", "noteheadHalf", "augmentationDot"] }
```

As a path to a `.txt` file (one name per line):
```json
{ "whitelist": "my_glyphs.txt" }
```

Set to `null` to render everything:
```json
{ "whitelist": null }
```

### Via CLI

Comma-separated inline:
```bash
python font_file_to_png.py example.otf --whitelist noteheadBlack,noteheadHalf,augmentationDot
```

From a text file:
```bash
python font_file_to_png.py example.otf --whitelist my_glyphs.txt
```

The script detects automatically whether the value is a file path or an inline list.

---

## 📦 Output

- One PNG per glyph written to the output directory
- Files named after the glyph, with non-alphanumeric characters replaced by `_` (e.g. `noteheadBlack.png`)
- **RGBA** images with transparent background
- Glyphs with empty bounding boxes (whitespace, unmapped codepoints) are skipped silently

---

## 💡 Examples

### Config file

First generate a blank template:

```bash
make create-config CONFIG=example.json
```

Open `example.json`, fill in at least `font_path` and `out`, then run:

```bash
make run-config CONFIG=example.json
```

### CLI — export all glyphs

To export every glyph with each PNG sized to fit its own bounding box:

```bash
python font_file_to_png.py example.otf --out ./glyphs
```

### CLI — fixed canvas size

To get uniform 256×256 images with each glyph centered:

```bash
python font_file_to_png.py example.otf --out ./glyphs --size 256
```

### CLI — preserve relative proportions

To render all glyphs at the same font size so they stay proportional to each other:

```bash
python font_file_to_png.py example.otf --out ./glyphs --uniform-scale --canvas 400 --px-per-em 200
```

### CLI — export specific glyphs only

To render only a subset of glyphs, pass their names as a comma-separated list:

```bash
python font_file_to_png.py example.otf --out ./glyphs --whitelist noteheadBlack,noteheadHalf
```

---

## 📚 Dependencies

| Package | Purpose |
|---------|---------|
| [`fonttools`](https://github.com/fonttools/fonttools) | Read font cmap and glyph data |
| [`Pillow`](https://python-pillow.org) | Render glyphs to PNG |
| [`rich`](https://github.com/Textualize/rich) | Terminal UI, animated progress bar, colored output |
