"""Worker integration checks without constructing or accessing a Tk window."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

import gui


class GuiWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory(prefix=".worker-test-", dir=Path(gui.__file__).parent)
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        project = root / "Game"
        project.mkdir()
        (project / "main.lua").write_text("", encoding="utf-8")
        self.request = gui.ExportRequest(
            project_dir=project, icon_png=None, output_dir=root / "out",
            release_dir=root / "out" / "Game_Builds",
            love_output=root / "out" / "Game_Builds" / "Source" / "Game.love",
            game_name="Game", platforms=("Windows", "Mac", "Linux"),
        )
        self.events = []
        self.ui = SimpleNamespace(
            _queue_ui_event=lambda callback, *args: self.events.append((callback, args)),
            _set_status=Mock(), _finish_export=Mock(),
            _update_build_result=Mock(), _set_progress=Mock(),
            _write_release_readme=gui.LoveExporterApp._write_release_readme,
            _export_platform=Mock(),
        )

    def test_one_platform_failure_does_not_cancel_other_builds_or_call_tk(self):
        self.ui._export_platform.side_effect = [
            self.request.release_dir / "Windows" / "Game_Windows.rar",
            RuntimeError("Missing Mac template"),
            self.request.release_dir / "Linux" / "Game_Linux_x86_64.tar.gz",
        ]
        with patch.object(gui.traceback, "print_exc"):
            gui.LoveExporterApp._process_exports(self.ui, self.request)
        self.assertEqual(self.ui._export_platform.call_count, 3)
        self.ui._set_status.assert_not_called()
        self.ui._update_build_result.assert_not_called()
        self.ui._set_progress.assert_not_called()
        self.ui._finish_export.assert_not_called()
        callback, args = self.events[-1]
        self.assertIs(callback, self.ui._finish_export)
        _, _, artifacts, failures, archive_error = args
        self.assertIsNone(archive_error)
        self.assertEqual([failure.platform for failure in failures], ["Mac"])
        self.assertIn(self.request.release_dir / "Windows" / "Game" / "Game.exe", artifacts)
        self.assertIn(self.request.release_dir / "Linux" / "Game_Linux_x86_64.tar.gz", artifacts)
        self.assertIn("[FAILED] macOS", (self.request.release_dir / "BUILD-REPORT.txt").read_text(encoding="utf-8"))

    def test_archive_failure_always_queues_completion_to_unlock_the_ui(self):
        with patch.object(gui.exporter, "create_love_file", side_effect=OSError("No disk space")), \
             patch.object(gui.traceback, "print_exc"):
            gui.LoveExporterApp._process_exports(self.ui, self.request)
        self.ui._export_platform.assert_not_called()
        callback, args = self.events[-1]
        self.assertIs(callback, self.ui._finish_export)
        self.assertEqual(args[-1], "No disk space")
        self.assertIn("No platform build was completed", (self.request.release_dir / "BUILD-REPORT.txt").read_text(encoding="utf-8"))

    def test_all_platform_failures_do_not_report_the_source_archive_as_a_successful_build(self):
        self.ui._export_platform.side_effect = RuntimeError("Template missing")
        with patch.object(gui.traceback, "print_exc"):
            gui.LoveExporterApp._process_exports(self.ui, self.request)
        report = (self.request.release_dir / "BUILD-REPORT.txt").read_text(encoding="utf-8")
        self.assertIn("No platform build was completed", report)
        self.assertIn("[EXPORTED] .love archive", report)
        self.assertEqual(report.count("[FAILED]"), 3)

    def test_platform_functions_use_separate_release_folders(self):
        license_file = self.request.project_dir / "LICENSE.txt"
        license_file.write_text("User-provided license terms.", encoding="utf-8")
        request = replace(self.request, developer_name="My Studio", game_license_file=license_file)
        for name, function, folder in (
            ("Windows", "export_windows", "Windows"),
            ("Mac", "export_mac", "macOS"),
            ("Linux", "export_linux", "Linux"),
            ("Web", "export_web", "Web"),
        ):
            with self.subTest(platform=name), patch.object(gui.exporter, function) as export:
                gui.LoveExporterApp._export_platform(name, request, request.love_output)
                self.assertIn(self.request.release_dir / folder, export.call_args.args)
                self.assertEqual(export.call_args.args[0], self.request.love_output)
                self.assertEqual(export.call_args.kwargs["developer_name"], "My Studio")
                self.assertEqual(export.call_args.kwargs["game_license_file"], license_file)


if __name__ == "__main__":
    unittest.main()
