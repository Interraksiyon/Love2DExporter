"""Small reusable widgets and colors for the desktop workspace.

These widgets only render state supplied on Tk's main thread. They never start
exports, inspect templates, or run subprocesses. No custom bitmap assets needed.
"""

from collections.abc import Callable
from pathlib import Path

import customtkinter as ctk


class Colors:
    BACKGROUND = "#0C121A"
    PANEL = "#131D29"
    RAISED = "#192635"
    BORDER = "#29394B"
    TEXT = "#EAF2FA"
    MUTED = "#9CAFC3"
    ACCENT = "#5DE0C2"
    ACCENT_HOVER = "#87EDD5"
    DARK_TEXT = "#0C2924"
    SUCCESS = "#7BE0AF"
    WARNING = "#F5C779"
    ERROR = "#FF9C9C"


STAGE_HINTS = {
    "Source": "Game files, with main.lua at the archive root",
    "Windows": "Playable EXE + DLLs • RAR / ZIP",
    "Mac": "Complete app bundle • ZIP • signing on Mac",
    "Linux": "x86_64 runtime • TAR.GZ • run AppRun",
    "Web": "HTML / WASM • Node.js required to build",
}
STAGE_TITLES = {"Source": ".love archive", "Windows": "Windows", "Mac": "macOS", "Linux": "Linux", "Web": "Web"}
STATE_STYLE = {
    "ready": ("READY", Colors.MUTED),
    "not_selected": ("OFF", Colors.MUTED),
    "queued": ("WAITING", Colors.MUTED),
    "building": ("BUILDING", Colors.ACCENT),
    "done": ("EXPORTED", Colors.SUCCESS),
    "failed": ("FAILED", Colors.ERROR),
    "not_built": ("NOT BUILT", Colors.WARNING),
}


class BuildResultRow(ctk.CTkFrame):
    """A stable status row with an Open-folder action, not a giant path popup."""

    def __init__(self, master, platform: str, open_path: Callable[[Path], None]):
        super().__init__(master, fg_color=Colors.RAISED, corner_radius=10, border_width=1, border_color=Colors.BORDER)
        self.platform = platform
        self.artifact: Path | None = None
        self.status = "ready"
        self.grid_columnconfigure(0, weight=1)
        self.title_label = ctk.CTkLabel(self, text=STAGE_TITLES[platform], font=ctk.CTkFont(size=14, weight="bold"), text_color=Colors.TEXT, anchor="w")
        self.title_label.grid(row=0, column=0, padx=14, pady=(11, 0), sticky="ew")
        self.state_label = ctk.CTkLabel(self, text="READY", font=ctk.CTkFont(size=10, weight="bold"), text_color=Colors.MUTED, width=76, anchor="e")
        self.state_label.grid(row=0, column=1, padx=(0, 14), pady=(11, 0), sticky="e")
        self.description_label = ctk.CTkLabel(self, text=STAGE_HINTS[platform], font=ctk.CTkFont(size=11), text_color=Colors.MUTED, anchor="w", justify="left", wraplength=260)
        self.description_label.grid(row=1, column=0, columnspan=2, padx=14, pady=(0, 9), sticky="ew")
        self.open_button = ctk.CTkButton(self, text="Open folder", width=94, height=25, fg_color="transparent", border_width=1, border_color=Colors.BORDER, hover_color=Colors.BORDER, text_color=Colors.TEXT, command=lambda: open_path(self.artifact) if self.artifact else None)
        self.open_button.grid(row=2, column=0, padx=14, pady=(0, 10), sticky="w")
        self.open_button.grid_remove()
        self.bind("<Configure>", self._resize_text)

    def _resize_text(self, event) -> None:
        self.description_label.configure(wraplength=max(160, event.width - 30))

    def set_result(self, status: str, artifact: Path | None = None, error: str = "") -> None:
        """Update the row; full errors live in the copyable Details report."""
        label, color = STATE_STYLE[status]
        self.status, self.artifact = status, artifact
        self.state_label.configure(text=label, text_color=color)
        self.configure(border_color=color if status == "building" else Colors.BORDER)
        description = artifact.name if artifact else STAGE_HINTS[self.platform]
        if status == "failed":
            description = "Could not export — view Details for the full error."
        elif status == "not_built":
            description = "An earlier stage could not finish."
        elif status == "not_selected":
            description = "Not selected for this export."
        self.description_label.configure(text=description)
        if status == "done" and artifact:
            self.open_button.grid()
        else:
            self.open_button.grid_remove()
