"""Real Tk checks; skip cleanly if the host has no graphical Tcl/Tk runtime.

These create a hidden test-owned window, not a user's existing application.
Run with the full Python used by build_windows.bat for GUI coverage on Windows.
"""

from pathlib import Path
import tempfile
from tkinter import TclError
import unittest
from unittest.mock import patch

import gui


class LayoutTests(unittest.TestCase):
    def setUp(self):
        try:
            self.app = gui.LoveExporterApp()
        except TclError as error:
            self.skipTest(f"Graphical Tcl/Tk runtime unavailable: {error}")
        self.app.attributes("-alpha", 0)
        self.addCleanup(self.close_test_window)
        self.app.update()

    def close_test_window(self):
        # CustomTkinter has process-wide DPI timers. Cancel test-root callbacks
        # before constructing another root in the same unittest process.
        for timer in self.app.tk.call("after", "info"):
            self.app.after_cancel(timer)
        self.app._on_close()

    def test_actions_stay_visible_at_minimum_and_default_sizes(self):
        for size in ("900x660", "1100x820", "1360x900"):
            with self.subTest(size=size):
                self.app.geometry(size)
                self.app.update()
                width, height = self.app.winfo_width(), self.app.winfo_height()
                for button in (self.app.export_button, self.app.details_button,
                               self.app.open_builds_button, self.app.test_web_button):
                    x = button.winfo_rootx() - self.app.winfo_rootx()
                    y = button.winfo_rooty() - self.app.winfo_rooty()
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x + button.winfo_width(), width)
                    self.assertLessEqual(y + button.winfo_height(), height)

    def test_busy_state_restores_configuration_controls(self):
        self.app.developer_name.set("Örnek Studio")
        self.app._set_export_busy(True)
        self.assertTrue(all(control.cget("state") == "disabled" for control in self.app._config_controls))
        self.app._set_export_busy(False)
        self.assertTrue(all(control.cget("state") == "normal" for control in self.app._config_controls))
        self.assertEqual(self.app.developer_entry.get(), "Örnek Studio")
        self.assertEqual(self.app.test_web_button.cget("state"), "disabled")

    def test_finish_uses_persistent_results_not_a_modal_and_unlocks_ui(self):
        with tempfile.TemporaryDirectory(prefix=".gui-test-", dir=Path(gui.__file__).parent) as temporary:
            root = Path(temporary)
            request = gui.ExportRequest(root / "Game", None, root, root, root / "Source" / "Game.love",
                                        "Game", ("Windows", "Mac"), "Game Studio")
            self.app._active_request = request
            self.app._export_in_progress = True
            artifacts = (request.love_output, root / "Windows" / "Game_Windows.zip")
            with patch.object(gui.messagebox, "showinfo") as info, patch.object(gui.messagebox, "showwarning") as warning:
                self.app._finish_export("Game", root, artifacts, (gui.ExportFailure("Mac", "Template missing"),), None)
            info.assert_not_called()
            warning.assert_not_called()
            self.assertEqual(self.app._results_rows["Windows"].status, "done")
            self.assertEqual(self.app._results_rows["Mac"].status, "failed")
            self.assertEqual(self.app.export_button.cget("state"), "normal")
            self.assertEqual(self.app.details_button.cget("state"), "normal")
            self.assertIn("Template missing", self.app._last_report)
            self.assertEqual(self.app.result_title.cget("text"), "Some builds need attention")


if __name__ == "__main__":
    unittest.main()
