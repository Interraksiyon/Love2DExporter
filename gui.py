"""Configure exports on Tk's main thread; do the slow work on a worker.

Reading order: _build_ui() makes the form; export() validates and snapshots it;
_process_exports() builds files; the queue + after() deliver results back to Tk.
No widget or StringVar is accessed by the worker. exporter.py handles packaging,
and build_notes.py handles developer credits and license documents.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
import subprocess
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from tkinter import TclError, filedialog, messagebox
import sys
import traceback
import webbrowser

import customtkinter as ctk

import exporter
from build_notes import normalize_developer_name, validate_license_path
from branding import APP_NAME, APP_VERSION, CREATOR_CREDIT, REPOSITORY_URL
from export_report import build_outcomes, format_build_report, summarize_export
from ui_components import BuildResultRow, Colors

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def resource_path(relative_path: str | os.PathLike[str]) -> str:
    """Return a bundled resource path in development and PyInstaller builds."""
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base_path / relative_path)


def release_directory(output_dir: Path, game_name: str) -> Path:
    """Return the clean, per-game release root under the selected output."""
    return output_dir / f"{game_name}_Builds"


class WebPreviewHandler(SimpleHTTPRequestHandler):
    """Serve a web export locally with suitable WASM and cache headers."""

    extensions_map = {
        **SimpleHTTPRequestHandler.extensions_map,
        ".wasm": "application/wasm",
    }

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        """Keep routine local-server requests out of the GUI's console."""


@dataclass(frozen=True)
class ExportRequest:
    """Plain values captured before starting the worker, not live Tk variables.

    New metadata fields have defaults so callers/tests using the original
    request fields remain compatible. None means auto-detect a project license.
    """

    project_dir: Path
    icon_png: Path | None
    output_dir: Path
    release_dir: Path
    love_output: Path
    game_name: str
    platforms: tuple[str, ...]
    developer_name: str = ""
    game_license_file: Path | None = None


@dataclass(frozen=True)
class ExportFailure:
    """A single platform failure captured without aborting other platforms."""

    platform: str
    message: str


class LoveExporterApp(ctk.CTk):
    """Collect options and run the selected LÖVE export targets."""

    def __init__(self) -> None:
        super().__init__()

        self.title(APP_NAME)
        self.geometry("1100x820")
        self.minsize(900, 660)
        self.configure(fg_color=Colors.BACKGROUND)

        self.project_path = ctk.StringVar()
        self.developer_name = ctk.StringVar()
        self.game_license_path = ctk.StringVar()
        self.icon_path = ctk.StringVar()
        self.output_path = ctk.StringVar()
        self.platforms = {
            "Windows": ctk.BooleanVar(value=True),
            "Mac": ctk.BooleanVar(value=False),
            "Linux": ctk.BooleanVar(value=False),
            "Web": ctk.BooleanVar(value=False),
        }
        self._web_server: ThreadingHTTPServer | None = None
        self._web_server_thread: Thread | None = None
        self._web_server_directory: Path | None = None
        self._web_server_url: str | None = None
        self._export_thread: Thread | None = None
        self._export_in_progress = False
        self._last_release_dir: Path | None = None
        self._last_web_build: Path | None = None
        self._active_request: ExportRequest | None = None
        self._last_report = ""
        self._document_window: ctk.CTkToplevel | None = None
        self._is_closing = False
        self._ui_events: Queue[tuple[Callable[..., None], tuple[object, ...]]] = Queue()
        self._config_controls: list[object] = []

        self._build_ui()
        for variable in self.platforms.values():
            variable.trace_add("write", self._refresh_platform_choices)
        self._refresh_platform_choices()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._ui_timer = self.after(50, self._drain_ui_events)

    def _build_ui(self) -> None:
        """Two balanced cards: configuration on the left, live results on the right."""
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, padx=24, pady=(24, 18), sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            header, text="LE", width=56, height=56, corner_radius=16,
            fg_color=Colors.ACCENT, text_color=Colors.DARK_TEXT,
            font=ctk.CTkFont(size=22, weight="bold"),
        ).grid(row=0, column=0, rowspan=2, padx=(0, 16))
        ctk.CTkLabel(header, text=APP_NAME, text_color=Colors.TEXT,
                     font=ctk.CTkFont(size=28, weight="bold"), anchor="w").grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(
            header, text="Your game. Every platform. Ready to share.",
            text_color=Colors.MUTED, font=ctk.CTkFont(size=13), anchor="w",
        ).grid(row=1, column=1, sticky="ew")
        ctk.CTkLabel(header, text=f"DESKTOP TOOL  /  v{APP_VERSION}",
                     text_color=Colors.MUTED, font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=2, rowspan=2, padx=(12, 0))

        workspace = ctk.CTkFrame(self, fg_color="transparent")
        workspace.grid(row=1, column=0, padx=24, pady=(0, 12), sticky="nsew")
        workspace.grid_columnconfigure(0, weight=3, minsize=450)
        workspace.grid_columnconfigure(1, weight=2, minsize=330)
        workspace.grid_rowconfigure(0, weight=1)

        card = ctk.CTkFrame(workspace, fg_color=Colors.PANEL, corner_radius=16,
                           border_width=1, border_color=Colors.BORDER)
        card.grid(row=0, column=0, padx=(0, 10), sticky="nsew")
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)
        heading = ctk.CTkFrame(card, fg_color="transparent")
        heading.grid(row=0, column=0, padx=22, pady=(18, 12), sticky="ew")
        ctk.CTkLabel(heading, text="BUILD SETTINGS", text_color=Colors.ACCENT,
                     font=ctk.CTkFont(size=12, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(heading, text="Choose your project and make it yours.",
                     text_color=Colors.MUTED, font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(3, 0))

        # Only the form scrolls. Targets and Export stay visible at small sizes.
        form = ctk.CTkScrollableFrame(card, fg_color="transparent", corner_radius=0,
                                     scrollbar_button_color=Colors.BORDER,
                                     scrollbar_button_hover_color=Colors.MUTED)
        form.grid(row=1, column=0, padx=6, pady=(0, 8), sticky="nsew")
        form.grid_columnconfigure(0, weight=1)
        self._form = form
        self._section_label(form, 0, "01  /  PROJECT & OUTPUT")
        self._add_path_row(form, row=1, label="Project folder", variable=self.project_path,
                           browse_command=self.choose_project_folder, placeholder="Folder containing main.lua")
        self._add_path_row(form, row=3, label="Output folder", variable=self.output_path,
                           browse_command=self.choose_output_folder, placeholder="Choose a folder outside your project")
        ctk.CTkLabel(form, text="Each game gets an organized Game_Builds folder.",
                     font=ctk.CTkFont(size=11), text_color=Colors.MUTED, anchor="w").grid(row=5, column=0, columnspan=2, padx=16, pady=(0, 16), sticky="w")

        self._section_label(form, 6, "02  /  BRANDING & GAME TERMS")
        ctk.CTkLabel(form, text="Developer / Studio", text_color=Colors.TEXT,
                     font=ctk.CTkFont(size=13, weight="bold"), anchor="w").grid(row=7, column=0, columnspan=2, padx=16, pady=(0, 6), sticky="w")
        self.developer_entry = ctk.CTkEntry(form, textvariable=self.developer_name,
                                           placeholder_text="Your name or studio (optional)", height=38,
                                           fg_color=Colors.BACKGROUND, border_color=Colors.BORDER,
                                           text_color=Colors.TEXT)
        self.developer_entry.grid(row=8, column=0, columnspan=2, padx=16, pady=(0, 14), sticky="ew")
        self._config_controls.append(self.developer_entry)
        self._add_path_row(form, row=9, label="Custom icon · optional", variable=self.icon_path,
                           browse_command=self.choose_icon, placeholder="PNG image, or keep the default icon")
        self._add_path_row(form, row=11, label="Game license · optional", variable=self.game_license_path,
                           browse_command=self.choose_game_license, placeholder="Auto-detect project LICENSE / COPYING")
        ctk.CTkLabel(form, text="Your terms are copied unchanged. Credit is not a signing certificate.",
                     font=ctk.CTkFont(size=11), text_color=Colors.MUTED, wraplength=400,
                     justify="left", anchor="w").grid(row=13, column=0, columnspan=2, padx=16, pady=(0, 10), sticky="ew")

        targets = ctk.CTkFrame(card, fg_color=Colors.RAISED, corner_radius=10)
        targets.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="ew")
        targets.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="targets")
        ctk.CTkLabel(targets, text="03  /  EXPORT TARGETS", text_color=Colors.MUTED,
                     font=ctk.CTkFont(size=11, weight="bold")).grid(row=0, column=0, columnspan=4, padx=14, pady=(12, 10), sticky="w")
        for column, (name, variable) in enumerate(self.platforms.items()):
            checkbox = ctk.CTkCheckBox(
                targets, text="macOS" if name == "Mac" else name, variable=variable,
                checkbox_width=19, checkbox_height=19, width=84,
                font=ctk.CTkFont(size=12), fg_color=Colors.ACCENT,
                hover_color=Colors.ACCENT_HOVER, border_color=Colors.MUTED,
                checkmark_color=Colors.DARK_TEXT, text_color=Colors.TEXT,
            )
            checkbox.grid(row=1, column=column, padx=8, pady=(0, 12), sticky="w")
            self._config_controls.append(checkbox)
        self.selection_label = ctk.CTkLabel(card, text="", text_color=Colors.MUTED,
                                           font=ctk.CTkFont(size=11), anchor="w")
        self.selection_label.grid(row=3, column=0, padx=22, pady=(0, 8), sticky="ew")
        self.export_button = ctk.CTkButton(
            card, text="Export builds", command=self.export, height=46, corner_radius=10,
            font=ctk.CTkFont(size=15, weight="bold"), fg_color=Colors.ACCENT,
            hover_color=Colors.ACCENT_HOVER, text_color=Colors.DARK_TEXT,
        )
        self.export_button.grid(row=4, column=0, padx=20, pady=(0, 20), sticky="ew")

        results = ctk.CTkFrame(workspace, fg_color=Colors.PANEL, corner_radius=16,
                              border_width=1, border_color=Colors.BORDER)
        results.grid(row=0, column=1, padx=(10, 0), sticky="nsew")
        results.grid_columnconfigure(0, weight=1)
        results.grid_rowconfigure(3, weight=1)
        ctk.CTkLabel(results, text="BUILD CENTER", text_color=Colors.ACCENT,
                     font=ctk.CTkFont(size=12, weight="bold")).grid(row=0, column=0, padx=20, pady=(18, 12), sticky="w")

        overview = ctk.CTkFrame(results, fg_color="transparent")
        overview.grid(row=1, column=0, padx=20, pady=(0, 14), sticky="ew")
        overview.grid_columnconfigure(0, weight=1)
        self.result_title = ctk.CTkLabel(overview, text="Ready when you are", text_color=Colors.TEXT,
                                        font=ctk.CTkFont(size=21, weight="bold"), anchor="w",
                                        justify="left", wraplength=340)
        self.result_title.grid(row=0, column=0, sticky="ew")
        self.result_description = ctk.CTkLabel(
            overview, text="Select your targets. Progress, packages, and any issues will appear here.",
            text_color=Colors.MUTED, font=ctk.CTkFont(size=12), wraplength=340, justify="left", anchor="w",
        )
        self.result_description.grid(row=1, column=0, pady=(4, 0), sticky="ew")
        overview.bind("<Configure>", lambda event: self._resize_overview(event.width))

        progress = ctk.CTkFrame(results, fg_color="transparent")
        progress.grid(row=2, column=0, padx=20, pady=(0, 12), sticky="ew")
        progress.grid_columnconfigure(0, weight=1)
        self.status_label = ctk.CTkLabel(progress, text="Waiting for your project", text_color=Colors.MUTED,
                                        font=ctk.CTkFont(size=11), anchor="w", wraplength=230, justify="left")
        self.status_label.grid(row=0, column=0, sticky="ew")
        self.stage_label = ctk.CTkLabel(progress, text="0 stages", text_color=Colors.MUTED, font=ctk.CTkFont(size=11), width=68)
        self.stage_label.grid(row=0, column=1, padx=(4, 0), sticky="e")
        self.progress_bar = ctk.CTkProgressBar(progress, height=5, fg_color=Colors.BORDER, progress_color=Colors.ACCENT)
        self.progress_bar.grid(row=1, column=0, columnspan=2, pady=(6, 0), sticky="ew")
        self.progress_bar.set(0)

        rows = ctk.CTkScrollableFrame(results, fg_color="transparent", corner_radius=0,
                                     scrollbar_button_color=Colors.BORDER,
                                     scrollbar_button_hover_color=Colors.MUTED)
        rows.grid(row=3, column=0, padx=6, pady=(0, 10), sticky="nsew")
        rows.grid_columnconfigure(0, weight=1)
        self._results_rows: dict[str, BuildResultRow] = {}
        for row_index, platform in enumerate(("Source", *self.platforms)):
            row = BuildResultRow(rows, platform, self._open_artifact_folder)
            row.grid(row=row_index, column=0, padx=8, pady=(0, 8), sticky="ew")
            self._results_rows[platform] = row

        actions = ctk.CTkFrame(results, fg_color="transparent")
        actions.grid(row=4, column=0, padx=20, pady=(0, 18), sticky="ew")
        actions.grid_columnconfigure((0, 1), weight=1, uniform="actions")
        secondary = dict(height=32, corner_radius=8, fg_color="transparent",
                         border_width=1, border_color=Colors.BORDER,
                         hover_color=Colors.BORDER, text_color=Colors.TEXT, width=100)
        self.details_button = ctk.CTkButton(actions, text="View details", command=self.show_build_details, state="disabled", **secondary)
        self.details_button.grid(row=0, column=0, padx=(0, 4), pady=(0, 8), sticky="ew")
        self.copy_report_button = ctk.CTkButton(actions, text="Copy report", command=self.copy_build_report, state="disabled", **secondary)
        self.copy_report_button.grid(row=0, column=1, padx=(4, 0), pady=(0, 8), sticky="ew")
        self.open_builds_button = ctk.CTkButton(actions, text="Open builds folder", command=self.open_builds, state="disabled", **secondary)
        self.open_builds_button.grid(row=1, column=0, columnspan=2, pady=(0, 8), sticky="ew")
        self.test_web_button = ctk.CTkButton(actions, text="Preview Web", command=self.test_web_build, state="disabled", **secondary)
        self.test_web_button.grid(row=2, column=0, padx=(0, 4), sticky="ew")
        self.stop_web_button = ctk.CTkButton(actions, text="Stop preview", command=self.stop_web_server, state="disabled", **secondary)
        self.stop_web_button.grid(row=2, column=1, padx=(4, 0), sticky="ew")

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, padx=26, pady=(0, 14), sticky="ew")
        footer.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(footer, text=CREATOR_CREDIT, text_color=Colors.MUTED,
                     font=ctk.CTkFont(size=11), anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkButton(footer, text="About / MIT license", width=140, height=26, fg_color="transparent",
                      hover_color=Colors.RAISED, text_color=Colors.MUTED, font=ctk.CTkFont(size=11),
                      command=self.show_about).grid(row=0, column=1, sticky="e")

    @staticmethod
    def _section_label(parent, row: int, text: str) -> None:
        ctk.CTkLabel(parent, text=text, text_color=Colors.ACCENT,
                     font=ctk.CTkFont(size=11, weight="bold"), anchor="w").grid(
                         row=row, column=0, columnspan=2, padx=16, pady=(4, 12), sticky="w")

    def _resize_overview(self, width: int) -> None:
        self.result_title.configure(wraplength=max(240, width))
        self.result_description.configure(wraplength=max(240, width))

    def _refresh_platform_choices(self, *_args) -> None:
        """Keep the next-run selection separate from the last run's results."""
        count = sum(bool(variable.get()) for variable in self.platforms.values())
        self.selection_label.configure(text=f"{count} platform{'s' if count != 1 else ''} selected  ·  Desktop runtime included")
        if not self._export_in_progress and not self._last_report:
            for platform, variable in self.platforms.items():
                self._results_rows[platform].set_result("ready" if variable.get() else "not_selected")

    def open_builds(self) -> None:
        """Open the completed release folder, including its playable Windows EXE."""
        directory = self._last_release_dir
        if directory is None or not directory.is_dir():
            messagebox.showerror("Build folder missing", "Export a game first.")
            return
        self._open_directory(directory)

    def _open_artifact_folder(self, artifact: Path) -> None:
        """Open a package's folder rather than launching an executable blindly."""
        self._open_directory(artifact if artifact.is_dir() else artifact.parent)

    def _open_directory(self, directory: Path) -> None:
        if not directory.is_dir():
            messagebox.showerror("Folder missing", "This output folder was moved or deleted.", parent=self)
            return
        try:
            if os.name == "nt":
                os.startfile(directory)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(directory)])
        except OSError as error:
            messagebox.showerror("Could not open builds", str(error))

    def _show_document(self, title: str, contents: str) -> None:
        """Use one reusable, scrollable, non-modal viewer for reports and notices."""
        if self._document_window is not None and self._document_window.winfo_exists():
            self._document_window.destroy()
        window = ctk.CTkToplevel(self)
        self._document_window = window
        window.title(f"{title} — {APP_NAME}")
        window.geometry("740x570")
        window.minsize(520, 380)
        window.configure(fg_color=Colors.BACKGROUND)
        window.grid_columnconfigure(0, weight=1)
        window.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(window, text=title, text_color=Colors.TEXT,
                     font=ctk.CTkFont(size=22, weight="bold")).grid(row=0, column=0, padx=22, pady=(20, 12), sticky="w")
        text = ctk.CTkTextbox(window, wrap="word", fg_color=Colors.PANEL,
                              text_color=Colors.TEXT, font=ctk.CTkFont(size=12), corner_radius=10)
        text.grid(row=1, column=0, padx=20, pady=(0, 14), sticky="nsew")
        text.insert("1.0", contents)
        text.configure(state="disabled")
        ctk.CTkButton(window, text="Close", width=100, height=32, fg_color=Colors.RAISED,
                      hover_color=Colors.BORDER, command=window.destroy).grid(row=2, column=0, padx=20, pady=(0, 20), sticky="e")
        # No grab_set / nested mainloop: exports and the main window stay usable.
        window.after(100, window.lift)

    def show_build_details(self) -> None:
        if self._last_report:
            self._show_document("Build details", self._last_report)

    def copy_build_report(self) -> None:
        """Copy only when asked; a local error may contain a developer's paths."""
        if self._last_report:
            self.clipboard_clear()
            self.clipboard_append(self._last_report)
            self._set_status("Build report copied to clipboard")

    def show_about(self) -> None:
        license_file = Path(resource_path("LICENSE"))
        license_text = license_file.read_text(encoding="utf-8") if license_file.is_file() else "See the repository's LICENSE file."
        notices_file = Path(resource_path("THIRD_PARTY_NOTICES.md"))
        notices = notices_file.read_text(encoding="utf-8") if notices_file.is_file() else "Bundled runtimes retain their original third-party licenses."
        python_notices = Path(resource_path("third-party/python"))
        if python_notices.is_dir():
            for file in sorted(python_notices.rglob("*")):
                if file.is_file() and file.suffix != ".json":
                    notices += f"\n\n--- {file.relative_to(python_notices)} ---\n"
                    notices += file.read_text(encoding="utf-8", errors="replace")
        self._show_document("About / License", (
            f"{APP_NAME} {APP_VERSION}\n{CREATOR_CREDIT}\n{REPOSITORY_URL}\n\n"
            "The exporter is MIT licensed. Your exported games keep their own terms.\n"
            "No game source is uploaded by this desktop tool. Web builds may let npm\n"
            "download the pinned love.js package; local previews bind only to 127.0.0.1.\n\n"
            f"{license_text}\n\n{notices}"
        ))

    def _add_path_row(
        self,
        parent: ctk.CTkFrame | ctk.CTkScrollableFrame,
        *,
        row: int,
        label: str,
        variable: ctk.StringVar,
        browse_command: Callable[[], None],
        placeholder: str,
    ) -> None:
        """Reuse the same label + entry + Browse layout for each file/folder."""
        ctk.CTkLabel(
            parent,
            text=label,
            font=ctk.CTkFont(size=13, weight="bold"), text_color=Colors.TEXT,
        ).grid(row=row, column=0, columnspan=2, padx=16, pady=(0, 6), sticky="w")

        entry = ctk.CTkEntry(
            parent,
            textvariable=variable,
            placeholder_text=placeholder,
            height=38,
            fg_color=Colors.BACKGROUND, border_color=Colors.BORDER, text_color=Colors.TEXT,
        )
        entry.grid(row=row + 1, column=0, padx=(16, 8), pady=(0, 14), sticky="ew")

        browse_button = ctk.CTkButton(
            parent,
            text="Browse",
            command=browse_command,
            width=78,
            height=38,
            fg_color=Colors.RAISED, hover_color=Colors.BORDER, border_width=1,
            border_color=Colors.BORDER, text_color=Colors.TEXT,
        )
        browse_button.grid(row=row + 1, column=1, padx=(0, 16), pady=(0, 14))
        self._config_controls.extend((entry, browse_button))

    def choose_project_folder(self) -> None:
        path = filedialog.askdirectory(title="Select LÖVE project folder")
        if path:
            self.project_path.set(path)

    def choose_icon(self) -> None:
        path = filedialog.askopenfilename(
            title="Select custom icon",
            filetypes=[("PNG image", "*.png")],
        )
        if path:
            self.icon_path.set(path)

    def choose_output_folder(self) -> None:
        path = filedialog.askdirectory(title="Select output folder")
        if path:
            self.output_path.set(path)

    def choose_game_license(self) -> None:
        """Select a developer-authored text license, not a built-in license choice."""
        path = filedialog.askopenfilename(
            title="Select your game's license text",
            filetypes=[("Text / Markdown", "*.txt *.md *.rst"), ("All files (including LICENSE)", "*")],
        )
        if path:
            self.game_license_path.set(path)

    def test_web_build(self) -> None:
        """Host the generated web folder locally instead of using file:// URLs."""
        web_directory = self._selected_web_build_directory()
        if web_directory is None:
            return

        if self._web_server_is_running():
            if self._web_server_directory == web_directory:
                webbrowser.open_new_tab(self._web_server_url or "")
                return
            self.stop_web_server()
        elif self._web_server is not None:
            self.stop_web_server()

        handler = partial(WebPreviewHandler, directory=str(web_directory))
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        except OSError as error:
            messagebox.showerror("Web server failed", str(error))
            return

        thread = Thread(
            target=server.serve_forever,
            kwargs={"poll_interval": 0.2},
            daemon=True,
        )
        self._web_server = server
        self._web_server_thread = thread
        self._web_server_directory = web_directory
        self._web_server_url = f"http://127.0.0.1:{server.server_port}/"
        thread.start()
        self.stop_web_button.configure(state="normal")

        webbrowser.open_new_tab(self._web_server_url)
        messagebox.showinfo(
            "Web test server",
            "The web build is available at:\n"
            f"{self._web_server_url}\n\n"
            "Keep this window open while testing. The browser's Click to start "
            "overlay is required for audio.",
        )

    def stop_web_server(self) -> None:
        """Shut down the local web preview server and release its resources."""
        server = self._web_server
        thread = self._web_server_thread
        self._web_server = None
        self._web_server_thread = None
        self._web_server_directory = None
        self._web_server_url = None

        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread.is_alive():
            thread.join(timeout=1)

        if hasattr(self, "stop_web_button"):
            self.stop_web_button.configure(state="disabled")

    def _web_server_is_running(self) -> bool:
        return (
            self._web_server is not None
            and self._web_server_thread is not None
            and self._web_server_thread.is_alive()
        )

    def _selected_web_build_directory(self) -> Path | None:
        # Preview the last completed Web build even if the next-run form changes.
        if self._last_web_build is not None and (self._last_web_build / "index.html").is_file():
            return self._last_web_build
        project_text = self.project_path.get().strip()
        output_text = self.output_path.get().strip()
        if not project_text or not output_text:
            messagebox.showerror(
                "Web build required",
                "Select the project and output folders before testing a Web build.",
            )
            return None

        project_dir = Path(project_text).expanduser()
        if not (project_dir / "main.lua").is_file():
            messagebox.showerror(
                "Invalid project",
                "The selected project folder must contain a main.lua file.",
            )
            return None
        game_name = project_dir.resolve().name
        output_dir = Path(output_text).expanduser().resolve()
        web_directory = (
            release_directory(output_dir, game_name)
            / "Web"
            / f"{game_name}_web"
        )
        if not (web_directory / "index.html").is_file():
            messagebox.showerror(
                "Web build not found",
                "Export the Web platform first, then try the local test server again.",
            )
            return None
        return web_directory

    def _on_close(self) -> None:
        """Close immediately; export workers are daemons and must not block exit."""
        self._is_closing = True
        if getattr(self, "_ui_timer", None):
            self.after_cancel(self._ui_timer)
        self.stop_web_server()
        self.destroy()

    def export(self) -> None:
        """Validate the UI, snapshot settings, then start a daemon worker."""
        if self._export_in_progress:
            return

        project_text = self.project_path.get().strip()
        output_text = self.output_path.get().strip()
        icon_text = self.icon_path.get().strip()
        license_text = self.game_license_path.get().strip()
        try:
            developer = normalize_developer_name(self.developer_name.get())
            game_license = validate_license_path(license_text) if license_text else None
        except (OSError, ValueError) as error:
            messagebox.showerror("Invalid developer / license information", str(error))
            return
        selected_platforms = tuple(
            name
            for name, variable in self.platforms.items()
            if variable.get() == 1
        )

        if not project_text:
            messagebox.showerror("Project required", "Select a LÖVE project folder first.")
            return
        if not output_text:
            messagebox.showerror("Output required", "Select an output folder first.")
            return
        if not selected_platforms:
            messagebox.showerror(
                "Platform required", "Select at least one platform to export."
            )
            return

        try:
            project_dir = exporter.validate_project_directory(project_text)
        except (OSError, ValueError) as error:
            messagebox.showerror("Invalid project", str(error))
            return

        output_dir = Path(output_text).expanduser().resolve()
        if output_dir.is_relative_to(project_dir):
            messagebox.showerror(
                "Invalid output folder",
                "Choose an output folder outside the LÖVE project folder.",
            )
            return

        icon_png: Path | None = None
        if icon_text:
            icon_png = Path(icon_text).expanduser().resolve()
            if not icon_png.is_file() or icon_png.suffix.lower() != ".png":
                messagebox.showerror(
                    "Invalid icon",
                    "Select an existing PNG file for the custom icon.",
                )
                return

        game_name = project_dir.name
        build_root = release_directory(output_dir, game_name)
        request = ExportRequest(
            project_dir=project_dir,
            icon_png=icon_png,
            output_dir=output_dir,
            release_dir=build_root,
            love_output=build_root / "Source" / f"{game_name}.love",
            game_name=game_name,
            platforms=selected_platforms,
            developer_name=developer,
            game_license_file=game_license,
        )

        print("LÖVE Exporter settings")
        print(f"Project folder: {request.project_dir}")
        print(f"Developer / Studio: {request.developer_name or 'Not specified'}")
        print(f"Game license: {request.game_license_file or 'Auto-detect project LICENSE/COPYING'}")
        print(f"Custom icon: {request.icon_png or 'Default'}")
        print(f"Output folder: {request.output_dir}")
        print(f"Build folder: {request.release_dir}")
        print(f"Platforms: {', '.join(request.platforms)}")

        # A build can rewrite a folder currently being served by the preview.
        if self._web_server is not None:
            self.stop_web_server()

        self._export_in_progress = True
        self._active_request = request
        self._last_web_build = None
        self._last_report = ""
        self.result_title.configure(text=f"Building {game_name}", text_color=Colors.TEXT)
        self.result_description.configure(text="Keep this window open. Each target is built independently, so one failure won't cancel the others.")
        self.progress_bar.configure(progress_color=Colors.ACCENT)
        self._set_progress(0, len(request.platforms) + 1)
        for platform, row in self._results_rows.items():
            row.set_result("queued" if platform == "Source" or platform in request.platforms else "not_selected")
        self._set_export_busy(True)
        self._set_status("Preparing export…")
        self._export_thread = Thread(
            target=self._process_exports,
            args=(request,),
            daemon=True,
            name="love-exporter",
        )
        try:
            self._export_thread.start()
        except RuntimeError as error:
            # Even a failure to start the worker must restore the interface.
            self._finish_export(game_name, build_root, (), (), str(error))

    def _process_exports(self, request: ExportRequest) -> None:
        """Create one game archive, then package each selected target independently.

        All paths, credit text and license choices come from the frozen request.
        The only UI-related work here is putting callbacks into a thread-safe
        queue. The main thread drains that queue through after(), even on errors.
        """
        artifacts: list[Path] = []
        failures: list[ExportFailure] = []
        archive_error: str | None = None
        total_stages = len(request.platforms) + 1

        try:
            self._queue_ui_event(self._set_status, "Creating .love archive…")
            self._queue_ui_event(self._update_build_result, "Source", "building")
            try:
                self._write_release_readme(request)
                love_file = exporter.create_love_file(
                    request.project_dir,
                    request.love_output,
                )
            except Exception as error:
                archive_error = str(error) or type(error).__name__
                self._queue_ui_event(self._update_build_result, "Source", "failed", None, archive_error)
                print("Creating the .love archive failed:")
                traceback.print_exc()
                return

            artifacts.append(love_file)
            self._queue_ui_event(self._update_build_result, "Source", "done", love_file)
            self._queue_ui_event(self._set_progress, 1, total_stages)
            for index, platform in enumerate(request.platforms, start=2):
                self._queue_ui_event(self._set_status, f"Exporting {platform}…")
                self._queue_ui_event(self._update_build_result, platform, "building")
                try:
                    artifact = self._export_platform(platform, request, love_file)
                    artifacts.append(artifact)
                    self._queue_ui_event(self._update_build_result, platform, "done", artifact)
                    if platform == "Windows":
                        artifacts.append(artifact.parent / request.game_name / f"{request.game_name}.exe")
                except Exception as error:
                    message = str(error) or type(error).__name__
                    failures.append(ExportFailure(platform, message))
                    self._queue_ui_event(self._update_build_result, platform, "failed", None, message)
                    print(f"{platform} export failed; continuing with other platforms:")
                    traceback.print_exc()
                    self._queue_ui_event(
                        self._set_status,
                        f"{platform} could not be exported; continuing…",
                    )
                finally:
                    # Stage progress counts attempted tasks, never invented time estimates.
                    self._queue_ui_event(self._set_progress, index, total_stages)
        except Exception as error:
            # This is a safety net for unexpected worker failures. Individual
            # platform errors above intentionally do not cancel other builds.
            archive_error = str(error) or type(error).__name__
            print("Export worker failed:")
            traceback.print_exc()
        finally:
            # Save a readable run report with local output, not in the public repo.
            try:
                request.release_dir.mkdir(parents=True, exist_ok=True)
                outcomes = build_outcomes(request.platforms, tuple(artifacts),
                                          {failure.platform: failure.message for failure in failures}, archive_error)
                report = format_build_report(request.game_name, request.developer_name,
                                             request.release_dir, outcomes, archive_error)
                (request.release_dir / "BUILD-REPORT.txt").write_text(report, encoding="utf-8")
            except OSError as error:
                archive_error = archive_error or f"Could not save the build report: {error}"
            # Completion runs on the main thread and always releases the UI.
            self._queue_ui_event(
                self._finish_export,
                request.game_name,
                request.release_dir,
                tuple(artifacts),
                tuple(failures),
                archive_error,
            )

    @staticmethod
    def _write_release_readme(request: ExportRequest) -> None:
        """Create a stable index for the orderly per-platform release tree."""
        request.release_dir.mkdir(parents=True, exist_ok=True)
        (request.release_dir / "README.txt").write_text(
            f"{request.game_name} - exported builds\n"
            f"Developer / Studio: {request.developer_name or 'Not specified'}\n\n"
            "FOLDER GUIDE\n"
            "Source/  - the generated .love archive\n"
            "Windows/ - a playable game folder (.exe + DLLs) and a RAR/ZIP to share\n"
            "macOS/   - a complete .app ZIP\n"
            "Linux/   - a complete x86_64 runtime tar.gz\n"
            "Web/     - the files to upload to an HTTP(S) web host\n\n"
            "Each packaged target contains CREDITS.txt and licenses/. The game\n"
            "license is separate from LÖVE's original runtime/framework notices.\n"
            "If no game license was selected/found, GAME-LICENSE.txt says so;\n"
            "the exporter does not invent license terms.\n\n"
            "QUICK START\n"
            "Windows: keep the EXE with all DLLs. Share the whole folder/archive.\n"
            "macOS: extract the ZIP on a Mac; see README-macOS.txt for signing.\n"
            "Linux: extract on x86_64 Linux and run AppRun.\n"
            "Web: host the entire Web folder via HTTP(S), not file://.\n\n"
            "Only selected targets are created. Desktop packages include LÖVE;\n"
            "Source/*.love is the original game archive and needs a separate runtime.\n",
            encoding="utf-8",
        )

    @staticmethod
    def _export_platform(
        platform: str,
        request: ExportRequest,
        love_file: Path,
    ) -> Path:
        """Export one selected target without touching Tkinter state."""
        platform_directory = {
            "Windows": "Windows",
            "Mac": "macOS",
            "Linux": "Linux",
            "Web": "Web",
        }.get(platform)
        if platform_directory is None:
            raise ValueError(f"Unsupported export platform: {platform}")
        destination = request.release_dir / platform_directory
        metadata = {
            "developer_name": request.developer_name,
            "game_license_file": request.game_license_file,
        }

        if platform == "Windows":
            return exporter.export_windows(
                love_file,
                request.icon_png,
                destination,
                request.game_name,
                **metadata,
            )
        if platform == "Mac":
            return exporter.export_mac(
                love_file,
                request.icon_png,
                destination,
                request.game_name,
                **metadata,
            )
        if platform == "Linux":
            return exporter.export_linux(
                love_file,
                destination,
                request.game_name,
                **metadata,
            )
        if platform == "Web":
            return exporter.export_web(
                love_file,
                destination,
                request.game_name,
                **metadata,
            )

    def _queue_ui_event(self, callback: Callable[..., None], *args: object) -> None:
        """Queue a UI update for the main thread's ``after`` callback."""
        self._ui_events.put((callback, args))

    def _drain_ui_events(self) -> None:
        """Run queued callbacks from Tk's main loop, never from a worker."""
        if self._is_closing:
            return

        while True:
            try:
                callback, args = self._ui_events.get_nowait()
            except Empty:
                break

            try:
                callback(*args)
            except TclError:
                if self._is_closing:
                    return
                print("A UI update failed:")
                traceback.print_exc()
            except Exception:
                print("A UI update failed:")
                traceback.print_exc()

        if not self._is_closing:
            try:
                self._ui_timer = self.after(50, self._drain_ui_events)
            except (RuntimeError, TclError):
                pass

    def _set_status(self, message: str) -> None:
        if not self._is_closing:
            self.status_label.configure(text=message)

    def _set_progress(self, completed: int, total: int) -> None:
        """Coarse, honest progress: archive + requested platform tasks."""
        if not self._is_closing:
            completed = max(0, min(completed, total))
            self.progress_bar.set(completed / total if total else 0)
            self.stage_label.configure(text=f"{completed} / {total}")

    def _update_build_result(self, platform: str, status: str,
                             artifact: Path | None = None, error: str = "") -> None:
        if not self._is_closing:
            self._results_rows[platform].set_result(status, artifact, error)

    def _set_export_busy(self, busy: bool) -> None:
        """Restore every control on success, partial failure, or worker failure."""
        state = "disabled" if busy else "normal"
        for control in self._config_controls:
            control.configure(state=state)
        self.export_button.configure(state=state, text="Exporting builds…" if busy else "Export builds")
        self.test_web_button.configure(state="normal" if not busy and self._last_web_build else "disabled")
        self.open_builds_button.configure(state="normal" if not busy and self._last_release_dir else "disabled")
        report_state = "normal" if not busy and self._last_report else "disabled"
        self.details_button.configure(state=report_state)
        self.copy_report_button.configure(state=report_state)
        self.stop_web_button.configure(state="normal" if not busy and self._web_server_is_running() else "disabled")

    def _finish_export(
        self,
        game_name: str,
        release_dir: Path,
        artifacts: tuple[Path, ...],
        failures: tuple[ExportFailure, ...],
        archive_error: str | None,
    ) -> None:
        """Leave a persistent, actionable result instead of a long modal popup."""
        self._export_in_progress = False
        self._export_thread = None
        if self._is_closing:
            return

        request = self._active_request
        platforms = request.platforms if request else tuple(failure.platform for failure in failures)
        outcomes = build_outcomes(platforms, artifacts,
                                  {failure.platform: failure.message for failure in failures}, archive_error)
        summary = summarize_export(outcomes, archive_error)
        self._last_report = format_build_report(game_name, request.developer_name if request else "",
                                               release_dir, outcomes, archive_error)
        self._last_release_dir = release_dir if artifacts or release_dir.is_dir() else None
        self._last_web_build = next((outcome.artifact for outcome in outcomes
                                     if outcome.platform == "Web" and outcome.status == "done"), None)
        for outcome in outcomes:
            self._update_build_result(outcome.platform, outcome.status, outcome.artifact, outcome.error)
        color = {"success": Colors.SUCCESS, "partial": Colors.WARNING, "failed": Colors.ERROR}[summary.state]
        self.result_title.configure(text=summary.title, text_color=color)
        self.result_description.configure(text=summary.detail)
        self.progress_bar.configure(progress_color=color)
        self._set_progress(sum(outcome.status in {"done", "failed"} for outcome in outcomes), len(outcomes))
        self._set_status({"success": "Finished • packages ready to share",
                          "partial": "Finished • view Details for warnings",
                          "failed": "Failed • view Details to resolve"}[summary.state])
        self._set_export_busy(False)

if __name__ == "__main__":
    app = LoveExporterApp()
    app.mainloop()
