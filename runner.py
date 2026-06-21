#!/usr/bin/env python3
"""Interactive runner for dawid-tools — navigate and run tools without leaving the terminal."""

import importlib
import importlib.metadata
import json
import os
import re
import select
import subprocess
import sys
import tempfile
import termios
import threading
import time
import tty
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

console = Console()
REPO_ROOT = Path(__file__).parent

STATE_TOOLS = "tools"
STATE_SCENARIOS = "scenarios"
STATE_PARAMS = "params"
STATE_FILEPICK = "filepick"
STATE_PATHPASTE = "pathpaste"
STATE_RUNNING = "running"

HISTORY_FILE = Path(tempfile.gettempdir()) / "dawid-tools-history.json"
PASTE_ENTRY = "\x00paste"
SEP_ENTRY = "\x00sep"


def load_history() -> list[str]:
    try:
        with open(HISTORY_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_to_history(path: str) -> None:
    path = str(Path(path).resolve())
    history = [p for p in load_history() if p != path]
    history.insert(0, path)
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(history[:5], f, indent=2)
    except Exception:
        pass

ANSI_RE = re.compile(r'\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def _strip_ansi(s: str) -> str:
    return ANSI_RE.sub('', s)

PRIDE_COLORS = [
    (228, 3,   3),
    (255, 140, 0),
    (255, 237, 0),
    (0,   128, 38),
    (0,   77,  255),
    (117, 7,   135),
]


def _pride_color(t: float) -> tuple[int, int, int]:
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
    return PRIDE_COLORS[int(t % 1.0 * len(PRIDE_COLORS)) % len(PRIDE_COLORS)]


class RainbowPanel:
    def __init__(self, renderable, title: str = "", subtitle: str = "", padding: tuple = (1, 2)):
        self.renderable = renderable
        self.title = title
        self.subtitle = subtitle
        self.pad_v, self.pad_h = padding

    def __rich_console__(self, console: Console, options) -> None:
        width = max(options.max_width // 2, 80)
        left_pad = (options.max_width - width) // 2
        pad = Segment(" " * left_pad) if left_pad > 0 else None
        phase = time.time() * 0.4

        inner_w = width - 2 - self.pad_h * 2
        content_lines = console.render_lines(self.renderable, options.update(width=inner_w), pad=True)
        height = 2 + 2 * self.pad_v + len(content_lines)
        top_pad = max(0, (console.size.height - height) // 2)
        P = 2 * (width - 1) + 2 * (height - 1)

        def bc(ch: str, x: int, y: int, bold: bool = False, dim: bool = False) -> Segment:
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
            if pad:
                yield pad
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

        for _ in range(top_pad):
            yield Segment.line()

        row = 0
        yield from h_border(row, "╭", "─", "╮", self.title)
        row += 1

        for _ in range(self.pad_v):
            if pad:
                yield pad
            yield bc("│", 0, row)
            yield Segment(" " * (width - 2))
            yield bc("│", width - 1, row)
            yield Segment.line()
            row += 1

        for line in content_lines:
            if pad:
                yield pad
            yield bc("│", 0, row)
            yield Segment(" " * self.pad_h)
            yield from line
            yield Segment(" " * self.pad_h)
            yield bc("│", width - 1, row)
            yield Segment.line()
            row += 1

        for _ in range(self.pad_v):
            if pad:
                yield pad
            yield bc("│", 0, row)
            yield Segment(" " * (width - 2))
            yield bc("│", width - 1, row)
            yield Segment.line()
            row += 1

        yield from h_border(row, "╰", "─", "╯", self.subtitle)


def _check_installed(req_path: str) -> bool:
    try:
        with open(req_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                name = re.split(r"[>=<!;\s\[]", line)[0].strip()
                if name:
                    importlib.metadata.version(name)
        return True
    except (importlib.metadata.PackageNotFoundError, FileNotFoundError):
        return False


def discover_tools() -> list[dict]:
    tools = []
    for p in sorted(REPO_ROOT.rglob("tool.json")):
        with open(p) as f:
            data = json.load(f)
        data["_dir"] = str(p.parent)
        if "requirements" in data:
            req_path = str((p.parent / data["requirements"]).resolve())
            data["_req_path"] = req_path
            data["_installed"] = _check_installed(req_path)
            data.setdefault("scenarios", []).append({
                "name": "install",
                "description": "Install Python dependencies into the active environment",
                "command": [sys.executable, "-m", "pip", "install", "-r", req_path],
                "params": [],
            })
        else:
            data["_installed"] = None  # no requirements — always ready
        tools.append(data)
    return tools


def read_key(fd: int) -> str | None:
    if not select.select([sys.stdin], [], [], 0.05)[0]:
        return None
    raw = os.read(fd, 1).decode("utf-8", errors="replace")
    if raw == "\x1b" and select.select([sys.stdin], [], [], 0.05)[0]:
        raw += os.read(fd, 4).decode("utf-8", errors="replace")
    return raw


class Runner:
    def __init__(self, tools: list[dict]) -> None:
        self.tools = tools
        self.state = STATE_TOOLS
        self.tool_idx = 0
        self.scenario_idx = 0
        self.current_tool: dict | None = None
        self.current_scenario: dict | None = None
        self.params: dict[str, str] = {}
        self.param_idx = 0
        self.current_input = ""
        self.output_lines: list[str] = []
        self.run_status = ""  # "running" | "done" | "error"
        self.run_returncode: int | None = None
        self.run_progress: tuple[int, int] | None = None
        self.run_current: str = ""
        self.run_renderer: object | None = None
        self.last_ran_at: datetime | None = None
        self.file_pick_list: list[str] = []
        self.file_pick_idx = 0
        self.path_input: str = ""

    def _enter_file_pick(self) -> None:
        scenario = self.current_scenario
        tool_dir = Path(self.current_tool["_dir"])
        pattern = scenario.get("pattern", "*.json")
        exclude = set(scenario.get("exclude", []))
        config_files = sorted(
            str(f) for f in tool_dir.glob(pattern) if f.name not in exclude
        )
        history = load_history()

        entries: list[str] = [PASTE_ENTRY]
        if history:
            entries.append(SEP_ENTRY)
            entries.extend(history)
        if config_files:
            entries.append(SEP_ENTRY)
            entries.extend(config_files)

        self.file_pick_list = entries
        self.file_pick_idx = 0
        self.state = STATE_FILEPICK

    def _next_pickable(self, idx: int, direction: int) -> int:
        entries = self.file_pick_list
        n = len(entries)
        idx = (idx + direction) % n
        while entries[idx] == SEP_ENTRY:
            idx = (idx + direction) % n
        return idx

    def _run_json_form(self) -> None:
        self.state = STATE_RUNNING
        self.output_lines = []
        self.run_status = "running"
        self.run_returncode = None

        def _worker() -> None:
            try:
                scenario = self.current_scenario
                out_key = scenario.get("output_key", "path")
                out_path = self.params.get(out_key, "config.json")
                if not os.path.isabs(out_path):
                    out_path = os.path.join(self.current_tool["_dir"], out_path)

                config: dict = {}
                for p in scenario.get("params", []):
                    field = p.get("field")
                    if not field:
                        continue
                    val = self.params.get(p["key"], p.get("default", ""))
                    ptype = p.get("type", "str")
                    if ptype == "int":
                        config[field] = int(val) if val.strip() else 0
                    elif ptype == "int?":
                        config[field] = int(val) if val.strip() else None
                    elif ptype == "bool":
                        config[field] = val.strip().lower() in ("true", "1", "yes")
                    elif ptype == "str?":
                        config[field] = val.strip() or None
                    elif ptype == "list?":
                        stripped = val.strip()
                        if not stripped:
                            config[field] = None
                        elif os.sep in stripped or stripped.endswith((".txt", ".csv")):
                            config[field] = stripped  # file path — keep as string
                        else:
                            config[field] = [s.strip() for s in stripped.split(",") if s.strip()]
                    else:
                        config[field] = val

                with open(out_path, "w") as f:
                    json.dump(config, f, indent=2)

                self.output_lines.append(f"Written: {out_path}")
                self.run_status = "done"
                self.run_returncode = 0
            except Exception as e:
                self.output_lines.append(f"Error: {e}")
                self.run_status = "error"
                self.run_returncode = -1

        threading.Thread(target=_worker, daemon=True).start()

    def _enter_edit_form(self, path: str) -> None:
        try:
            with open(path) as f:
                existing = json.load(f)
        except Exception:
            existing = {}

        scenario = self.current_scenario
        output_key = scenario.get("output_key", "path")
        params = scenario.get("params", [])

        self.params = {}
        for p in params:
            key = p["key"]
            field = p.get("field")
            if key == output_key:
                self.params[key] = path
            elif field and field in existing:
                val = existing[field]
                if val is None:
                    self.params[key] = ""
                elif isinstance(val, bool):
                    self.params[key] = "true" if val else "false"
                elif isinstance(val, list):
                    self.params[key] = ", ".join(str(v) for v in val)
                else:
                    self.params[key] = str(val)
            else:
                self.params[key] = p.get("default", "")

        # Skip the output_key param — path is already set from the picker
        start = 1 if params and params[0]["key"] == output_key else 0
        self.param_idx = start
        self.current_input = self.params.get(params[start]["key"], "") if start < len(params) else ""
        self.state = STATE_PARAMS

    def _run_python_call(self) -> None:
        scenario = self.current_scenario
        tool_dir = self.current_tool["_dir"]
        if tool_dir not in sys.path:
            sys.path.insert(0, tool_dir)

        mod = importlib.import_module(scenario["module"])
        renderer_cls = getattr(mod, scenario["renderer"])

        config_path = self.params.get("path", "")
        if not os.path.isabs(config_path):
            config_path = os.path.join(tool_dir, config_path)
        try:
            with open(config_path) as f:
                config = json.load(f)
        except Exception as e:
            self.output_lines = [f"Could not load config: {e}"]
            self.run_status = "error"
            self.run_returncode = -1
            self.state = STATE_RUNNING
            return

        renderer = renderer_cls()
        self.run_renderer = renderer
        self.state = STATE_RUNNING

        threading.Thread(
            target=renderer.run,
            kwargs={"config": config, "base_dir": tool_dir},
            daemon=True,
        ).start()

    def start_run(self) -> None:
        self.last_ran_at = datetime.now()
        if self.current_scenario.get("type") in ("json-form", "edit-json-form"):
            self._run_json_form()
            return
        if "module" in self.current_scenario:
            self._run_python_call()
            return

        cmd, cwd = self.build_command()
        self.state = STATE_RUNNING
        self.output_lines = []
        self.run_status = "running"
        self.run_returncode = None

        def _worker() -> None:
            try:
                proc = subprocess.Popen(
                    cmd, cwd=cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
                for line in proc.stdout:
                    self.output_lines.append(_strip_ansi(line.rstrip()))
                proc.wait()
                self.run_returncode = proc.returncode
                self.run_status = "done" if proc.returncode == 0 else "error"
            except Exception as e:
                self.output_lines.append(f"Error: {e}")
                self.run_status = "error"
                self.run_returncode = -1

        threading.Thread(target=_worker, daemon=True).start()

    def render(self) -> RainbowPanel:
        body = Text()
        phase = time.time() * 0.3

        if self.state == STATE_TOOLS:
            title = "dawid-tools"
            footer = "↑↓ navigate   Enter select   q quit"
            for i, t in enumerate(self.tools):
                sel = i == self.tool_idx
                installed = t.get("_installed")
                if sel:
                    r, g, b = _pride_color(phase)
                    body.append("▶ ", style=Style(color=f"rgb({r},{g},{b})", bold=True))
                    body.append(t["name"], style="bold white")
                else:
                    body.append("  ")
                    body.append(t["name"], style="dim")
                if installed is True:
                    body.append("  ✓ ready", style="green")
                elif installed is False:
                    body.append("  needs install", style="yellow dim")
                body.append("\n")
                body.append(f"    {t.get('description', '')}\n", style="dim")

        elif self.state == STATE_SCENARIOS:
            tool = self.current_tool
            title = tool["name"]
            footer = "↑↓ navigate   Enter select   Esc back   q quit"
            if tool.get("_installed") is False:
                body.append("  ⚠  not installed — run ", style="yellow")
                body.append("install", style="bold yellow")
                body.append(" first\n\n", style="yellow")
            for i, s in enumerate(tool["scenarios"]):
                sel = i == self.scenario_idx
                is_install = s["name"] == "install"
                if sel:
                    r, g, b = _pride_color(phase)
                    body.append("▶ ", style=Style(color=f"rgb({r},{g},{b})", bold=True))
                    body.append(s["name"], style="bold white")
                else:
                    body.append("  ")
                    body.append(s["name"], style="dim")
                if is_install and tool.get("_installed") is True:
                    body.append("  ✓", style="green")
                body.append("\n")
                body.append(f"    {s.get('description', '')}\n", style="dim")

        elif self.state == STATE_FILEPICK:
            scenario = self.current_scenario
            title = f"{self.current_tool['name']} › {scenario['name']}"
            footer = "↑↓ navigate   Enter select   Esc back   q quit"
            has_real = any(e not in (PASTE_ENTRY, SEP_ENTRY) for e in self.file_pick_list)
            if not has_real and len(self.file_pick_list) == 1:
                body.append("  No config files found.\n", style="dim")
                body.append("  Create one with ", style="dim")
                body.append("create-config", style="bold white dim")
                body.append(" first.\n", style="dim")
                body.append("\n")
            for i, entry in enumerate(self.file_pick_list):
                sel = i == self.file_pick_idx
                if entry == SEP_ENTRY:
                    body.append("  " + "─" * 30 + "\n", style="dim")
                elif entry == PASTE_ENTRY:
                    if sel:
                        r, g, b = _pride_color(phase)
                        body.append("▶ ", style=Style(color=f"rgb({r},{g},{b})", bold=True))
                        body.append("Paste path to config…\n", style="bold white")
                    else:
                        body.append("  ")
                        body.append("Paste path to config…\n", style="dim")
                else:
                    name = Path(entry).name
                    if sel:
                        r, g, b = _pride_color(phase)
                        body.append("▶ ", style=Style(color=f"rgb({r},{g},{b})", bold=True))
                        body.append(name + "\n", style="bold white")
                        body.append(f"   {entry}\n", style="dim")
                    else:
                        body.append("  ")
                        body.append(name + "\n", style="dim")

        elif self.state == STATE_PATHPASTE:
            scenario = self.current_scenario
            title = f"{self.current_tool['name']} › {scenario['name']}"
            footer = "Enter confirm   Esc back"
            r, g, b = _pride_color(phase)
            color = f"rgb({r},{g},{b})"
            body.append("  Path to config file:\n", style="bold")
            body.append("  " + self.path_input + "█\n", style=Style(color=color, bold=True))

        elif self.state == STATE_RUNNING:
            title = f"{self.current_tool['name']} › {self.current_scenario['name']}"
            if self.run_renderer is not None:
                footer = self.run_renderer.footer
                body = self.run_renderer
            else:
                # Generic fallback for subprocess / json-form runs
                for line in self.output_lines[-30:]:
                    body.append(line + "\n", style="dim")
                body.append("\n")
                if self.run_status == "running":
                    footer = "running…"
                    r, g, b = _pride_color(phase)
                    body.append("  ● running…\n", style=Style(color=f"rgb({r},{g},{b})", bold=True))
                elif self.run_status == "done":
                    footer = "r re-run   Enter / Esc back"
                    body.append("  ✓ done\n", style="bold green")
                    if self.last_ran_at:
                        ts = self.last_ran_at.strftime("  ran at %H:%M:%S on %Y-%m-%d\n")
                        n = len(ts)
                        for i, ch in enumerate(ts):
                            r2, g2, b2 = _pride_color(phase + i / n * 0.5)
                            body.append(ch, style=Style(color=f"rgb({r2},{g2},{b2})"))
                else:
                    footer = "r re-run   Enter / Esc back"
                    body.append(f"  ✗ exited with code {self.run_returncode}\n", style="bold red")
                    if self.last_ran_at:
                        ts = self.last_ran_at.strftime("  ran at %H:%M:%S on %Y-%m-%d\n")
                        n = len(ts)
                        for i, ch in enumerate(ts):
                            r2, g2, b2 = _pride_color(phase + i / n * 0.5)
                            body.append(ch, style=Style(color=f"rgb({r2},{g2},{b2})"))

        elif self.state == STATE_PARAMS:
            scenario = self.current_scenario
            title = f"{self.current_tool['name']} › {scenario['name']}"
            footer = "Enter confirm   Esc back"
            for i, p in enumerate(scenario.get("params", [])):
                label = p.get("prompt", p["key"])
                if i < self.param_idx:
                    body.append(f"  {label}: ", style="dim")
                    body.append(self.params.get(p["key"], "") + "\n", style="green")
                elif i == self.param_idx:
                    r, g, b = _pride_color(phase)
                    color = f"rgb({r},{g},{b})"
                    body.append(f"  {label}: ", style="bold")
                    body.append(self.current_input + "█\n", style=Style(color=color, bold=True))
                else:
                    body.append(f"  {label}: ", style="dim")
                    body.append(p.get("default", "") + "\n", style="dim")

        return RainbowPanel(body, title=title, subtitle=footer, padding=(1, 2))

    def handle_key(self, key: str) -> str:
        """Return 'quit' or '' to continue."""
        if key == "\x03":
            return "quit"

        if self.state == STATE_TOOLS:
            if key in ("q", "Q"):
                return "quit"
            elif key == "\x1b[A":
                self.tool_idx = (self.tool_idx - 1) % len(self.tools)
            elif key == "\x1b[B":
                self.tool_idx = (self.tool_idx + 1) % len(self.tools)
            elif key in ("\r", "\n"):
                self.current_tool = self.tools[self.tool_idx]
                self.scenario_idx = 0
                self.state = STATE_SCENARIOS

        elif self.state == STATE_SCENARIOS:
            scenarios = self.current_tool["scenarios"]
            if key == "\x1b":
                self.state = STATE_TOOLS
            elif key in ("q", "Q"):
                return "quit"
            elif key == "\x1b[A":
                self.scenario_idx = (self.scenario_idx - 1) % len(scenarios)
            elif key == "\x1b[B":
                self.scenario_idx = (self.scenario_idx + 1) % len(scenarios)
            elif key in ("\r", "\n"):
                self.current_scenario = scenarios[self.scenario_idx]
                stype = self.current_scenario.get("type", "command")
                params = self.current_scenario.get("params", [])
                if stype in ("pick-file", "edit-json-form"):
                    self._enter_file_pick()
                elif params:
                    self.params = {p["key"]: p.get("default", "") for p in params}
                    self.param_idx = 0
                    self.current_input = self.params.get(params[0]["key"], "")
                    self.state = STATE_PARAMS
                else:
                    self.start_run()

        elif self.state == STATE_PARAMS:
            params = self.current_scenario.get("params", [])
            if key == "\x1b":
                self.state = STATE_SCENARIOS
            elif key in ("\r", "\n"):
                self.params[params[self.param_idx]["key"]] = self.current_input
                self.param_idx += 1
                if self.param_idx >= len(params):
                    self.start_run()
                else:
                    self.current_input = self.params.get(params[self.param_idx]["key"], "")
            elif key == "\x7f":
                self.current_input = self.current_input[:-1]
            elif len(key) == 1 and key.isprintable():
                self.current_input += key

        elif self.state == STATE_FILEPICK:
            if key == "\x1b":
                self.state = STATE_SCENARIOS
            elif key in ("q", "Q"):
                return "quit"
            elif key == "\x1b[A" and self.file_pick_list:
                self.file_pick_idx = self._next_pickable(self.file_pick_idx, -1)
            elif key == "\x1b[B" and self.file_pick_list:
                self.file_pick_idx = self._next_pickable(self.file_pick_idx, 1)
            elif key in ("\r", "\n") and self.file_pick_list:
                picked = self.file_pick_list[self.file_pick_idx]
                if picked == PASTE_ENTRY:
                    self.path_input = ""
                    self.state = STATE_PATHPASTE
                elif picked != SEP_ENTRY:
                    if self.current_scenario.get("type") == "edit-json-form":
                        self._enter_edit_form(picked)
                    else:
                        save_to_history(picked)
                        self.params = {"path": picked}
                        self.start_run()

        elif self.state == STATE_PATHPASTE:
            if key == "\x1b":
                self.state = STATE_FILEPICK
            elif key in ("\r", "\n"):
                path = self.path_input.strip()
                if path:
                    save_to_history(path)
                    self.params = {"path": path}
                    self.start_run()
            elif key == "\x7f":
                self.path_input = self.path_input[:-1]
            elif len(key) == 1 and (key.isprintable()):
                self.path_input += key

        elif self.state == STATE_RUNNING:
            is_done = self.run_renderer.done if self.run_renderer else (self.run_status != "running")
            if is_done:
                if key in ("q", "Q"):
                    return "quit"
                elif key in ("r", "R"):
                    self.run_renderer = None
                    self.start_run()
                elif key in ("\r", "\n", " ", "\x1b"):
                    self.run_renderer = None
                    self.back_to_scenarios()

        return ""

    def build_command(self) -> tuple[list[str], str]:
        cmd = []
        for part in self.current_scenario["command"]:
            if part in ("python3", "python"):
                part = sys.executable
            for k, v in self.params.items():
                part = part.replace(f"{{{k}}}", v)
            cmd.append(part)
        return cmd, self.current_tool["_dir"]

    def back_to_scenarios(self) -> None:
        self.state = STATE_SCENARIOS
        self.param_idx = 0
        self.current_input = ""
        if "_req_path" in self.current_tool:
            self.current_tool["_installed"] = _check_installed(self.current_tool["_req_path"])


def run_tui(runner: Runner) -> None:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        with Live(runner.render(), refresh_per_second=20, console=console, screen=True) as live:
            while True:
                key = read_key(fd)
                if key is not None:
                    if runner.handle_key(key) == "quit":
                        break
                live.update(runner.render())
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        console.show_cursor(True)


def main() -> None:
    tools = discover_tools()
    if not tools:
        console.print("[red]No tools found. Add tool.json files to tool directories.[/]")
        sys.exit(1)

    run_tui(Runner(tools))


if __name__ == "__main__":
    main()
