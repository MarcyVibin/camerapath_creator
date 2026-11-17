"""Textual front-ends: CamtrackApp (full pipeline) and BlenderImportApp (existing solve -> .blend)."""
import os
import re
from pathlib import Path
from typing import Callable

# The classic Windows console supports 24-bit colour but is auto-detected as 16-colour (navy/ANSI look).
# Textual reads this once at import time, so it must be set before the textual imports below.
os.environ.setdefault("TEXTUAL_COLOR_SYSTEM", "truecolor")

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Checkbox, Footer, Header, Input, Label, ProgressBar, RichLog

from camtrack.config import Settings
from camtrack.pipeline import STEPS

STEP_NAMES = [name for name, _ in STEPS]
COUNTER = re.compile(r"\[(\d+)/(\d+)\]")  # COLMAP per-item progress, e.g. "Processed file [12/466]"
VIDEO_TYPES = [("Video", "*.mp4 *.mov *.mkv *.avi *.m4v"), ("All files", "*.*")]


class _ToolApp(App):
    """Shared look and plumbing: path inputs with Browse buttons, a Run button, status line and log."""
    BINDINGS = [("q", "quit", "Quit"), ("ctrl+q", "quit", "Quit")]
    ENABLE_COMMAND_PALETTE = False
    # Only box-drawing borders: the classic Windows console font lacks Textual's default "tall"
    # half-block border glyphs (they render as boxed '?').
    CSS = """
    Screen { padding: 0 1; }
    Horizontal { height: auto; }
    Input, Checkbox, Button, Button:hover, Button.-active {
        border: round $panel-lighten-2; height: 3; }
    Input:focus, Checkbox:focus, Button:focus { border: round $accent; }
    Button { min-width: 12; margin-left: 1; }
    Input { width: 1fr; }
    #status { margin: 0 1; }
    ProgressBar { margin-bottom: 1; padding: 0 1; width: 1fr; }
    ProgressBar Bar { width: 1fr; }
    RichLog { border: round $primary; height: 1fr; width: 100%; }
    """
    PICKERS: dict[str, str] = {}   # input id -> "video" | "folder" | "blend"; each gets a Browse button

    def path_row(self, input_id: str, placeholder: str) -> Horizontal:
        return Horizontal(Input(placeholder=placeholder, id=input_id),
                          Button("Browse…", id=f"browse_{input_id}", classes="browse"))

    def path(self, input_id: str) -> Path | None:
        value = self.query_one(f"#{input_id}", Input).value.strip().strip('"')
        return Path(value) if value else None

    # -- file picker: tkinter dialog in a thread worker (own hidden Tk root, created and destroyed there)
    @on(Button.Pressed, ".browse")
    def browse(self, event: Button.Pressed) -> None:
        self._pick(event.button.id.removeprefix("browse_"))

    @work(thread=True, exclusive=True, group="browse")
    def _pick(self, input_id: str) -> None:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            kind = self.PICKERS[input_id]
            if kind == "folder":
                path = filedialog.askdirectory(parent=root, title="Select COLMAP model or workspace folder")
            elif kind == "blend":
                path = filedialog.asksaveasfilename(parent=root, title="Save .blend as", defaultextension=".blend",
                                                    filetypes=[("Blender", "*.blend")])
            else:
                path = filedialog.askopenfilename(parent=root, title="Select video", filetypes=VIDEO_TYPES)
        finally:
            root.destroy()
        if path:
            self.call_from_thread(setattr, self.query_one(f"#{input_id}", Input), "value", path)

    # -- running a job in the background, streaming its log
    def begin(self, job: Callable[[Callable[[str], None]], str]) -> None:
        """Run job(log) -> result message in a worker thread; Run stays disabled until it ends."""
        self.query_one("#run", Button).disabled = True
        self.query_one("#log", RichLog).clear()
        self._run(job)

    @work(thread=True, exclusive=True, group="run")
    def _run(self, job: Callable[[Callable[[str], None]], str]) -> None:
        try:
            message = job(lambda line: self.call_from_thread(self._on_line, line))
        except Exception as exc:  # noqa: BLE001 - surface any failure in the UI
            self.call_from_thread(self._done, f"FAILED: {type(exc).__name__}: {exc}", True)
        else:
            self.call_from_thread(self._done, message, False)

    def _on_line(self, line: str) -> None:
        self.query_one("#log", RichLog).write(line)
        if line.startswith("== "):
            self.query_one("#status", Label).update(f"Step: {line[3:].strip()}")

    def _done(self, message: str, failed: bool) -> None:
        self.query_one("#log", RichLog).write(message)
        self.query_one("#status", Label).update("Failed" if failed else "Done")
        self.query_one("#run", Button).disabled = False
        self.notify(message.splitlines()[0], severity="error" if failed else "information", timeout=15)


class CamtrackApp(_ToolApp):
    TITLE = "camtrack"
    PICKERS = {"video": "video"}

    def compose(self) -> ComposeResult:
        yield Header()
        yield self.path_row("video", "Video path")
        with Horizontal():
            yield Input(placeholder="Focal length mm, 36 mm-equiv. (blank = auto)", id="focal")
            yield Checkbox("Force redo", id="force")
            yield Button("Run", id="run", variant="success")
        yield Label("Idle", id="status")
        yield ProgressBar(total=len(STEP_NAMES), show_eta=False, id="bar")
        yield RichLog(id="log", wrap=True, markup=False)
        yield Footer()

    @on(Button.Pressed, "#run")
    def start(self) -> None:
        video = self.path("video")
        if not video or not video.is_file():
            self.notify(f"Video not found: {video or '(empty)'}", severity="error")
            return
        focal = self.query_one("#focal", Input).value.strip()
        try:
            focal_mm = float(focal) if focal else None
        except ValueError:
            self.notify("Focal length must be a number", severity="error")
            return
        settings = Settings(focal_mm=focal_mm, force=self.query_one("#force", Checkbox).value)
        self.query_one("#bar", ProgressBar).update(progress=0)
        self._step = 0

        def job(log):
            from camtrack.pipeline import run
            result = run(video, settings, log)
            stats = ", ".join(f"{k}={v}" for k, v in result.get("stats", {}).items())
            return f"DONE: {result['blend']}\n{stats}"
        self.begin(job)

    def _on_line(self, line: str) -> None:
        super()._on_line(line)
        bar = self.query_one("#bar", ProgressBar)
        if line.startswith("== "):
            name = line[3:].split()[0]
            self._step = STEP_NAMES.index(name) + ("skipping" in line)
            self.query_one("#status", Label).update(
                f"Step {min(self._step + 1, len(STEP_NAMES))}/{len(STEP_NAMES)}: {line[3:].strip()}")
            bar.update(progress=max(bar.progress, self._step))
        elif m := COUNTER.search(line):
            i, n = int(m[1]), int(m[2])
            if n:  # never move backwards: some steps print several counters
                bar.update(progress=max(bar.progress, self._step + min(i / n, 1)))

    def _done(self, message: str, failed: bool) -> None:
        super()._done(message, failed)
        if not failed:
            self.query_one("#bar", ProgressBar).update(progress=len(STEP_NAMES))


class BlenderImportApp(_ToolApp):
    TITLE = "camtrack: Blender import"
    PICKERS = {"model": "folder", "video": "video", "output": "blend"}

    def compose(self) -> ComposeResult:
        yield Header()
        yield self.path_row("model", "COLMAP model folder (camtrack workspace, TXT or binary model)")
        yield self.path_row("video", "Video path")
        with Horizontal():
            yield Input(placeholder="Output .blend (blank = <video>_camera.blend)", id="output")
            yield Button("Browse…", id="browse_output", classes="browse")
            yield Button("Run", id="run", variant="success")
        yield Label("Idle", id="status")
        yield RichLog(id="log", wrap=True, markup=False)
        yield Footer()

    @on(Button.Pressed, "#run")
    def start(self) -> None:
        model, video = self.path("model"), self.path("video")
        if not model or not model.is_dir():
            self.notify(f"Model folder not found: {model or '(empty)'}", severity="error")
            return
        if not video or not video.is_file():
            self.notify(f"Video not found: {video or '(empty)'}", severity="error")
            return
        settings = Settings(output=self.path("output"))
        self.query_one("#status", Label).update("Importing into Blender…")

        def job(log):
            from camtrack.pipeline import blender_only
            return f"DONE: {blender_only(model, video, settings, log)}"
        self.begin(job)


def main() -> None:
    CamtrackApp().run()


def main_blender_import() -> None:
    BlenderImportApp().run()


if __name__ == "__main__":
    main()
