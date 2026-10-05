"""Final-state regression tests: partial success is not full success."""

from pathlib import Path
import unittest

from branding import CREATOR_CREDIT
from export_report import build_outcomes, format_build_report, summarize_export


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.root = Path("release")
        self.love = self.root / "Source" / "Game.love"
        self.windows = self.root / "Windows" / "Game_Windows.rar"
        self.mac = self.root / "macOS" / "Game_mac.zip"

    def test_source_alone_does_not_make_a_platform_export_successful(self):
        rows = build_outcomes(("Windows", "Mac"), (self.love,), {"Windows": "DLL missing", "Mac": "Template missing"}, None)
        result = summarize_export(rows)
        self.assertEqual((result.state, result.succeeded, result.requested), ("failed", 0, 2))

    def test_partial_success_keeps_the_completed_artifact(self):
        rows = build_outcomes(("Windows", "Mac"), (self.love, self.windows), {"Mac": "Template missing"}, None)
        self.assertEqual(summarize_export(rows).state, "partial")
        self.assertEqual(rows[1].artifact, self.windows)
        self.assertIsNone(rows[2].artifact)

    def test_success_requires_every_requested_target(self):
        rows = build_outcomes(("Windows", "Mac"), (self.love, self.windows, self.mac), {}, None)
        self.assertEqual(summarize_export(rows).state, "success")
        self.assertEqual(summarize_export(rows, "Could not save report").state, "partial")

    def test_archive_failure_marks_targets_not_built(self):
        rows = build_outcomes(("Windows", "Web"), (), {}, "No disk space")
        self.assertEqual([row.status for row in rows], ["failed", "not_built", "not_built"])
        self.assertEqual(summarize_export(rows, "No disk space").state, "failed")

    def test_report_uses_relative_paths_and_separates_exporter_and_game_credit(self):
        rows = build_outcomes(("Windows",), (self.love, self.windows), {}, None)
        report = format_build_report("Game", "Another Studio", self.root, rows)
        self.assertIn(CREATOR_CREDIT, report)
        self.assertIn("Game developer / studio: Another Studio", report)
        self.assertIn(str(self.windows.relative_to(self.root)), report)
        self.assertNotIn(str(self.windows), report)
        self.assertIn("MIT license does not select or replace the game's license", report)


if __name__ == "__main__":
    unittest.main()
