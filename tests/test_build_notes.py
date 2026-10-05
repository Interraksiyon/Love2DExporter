"""Credits/license tests: no fake legal terms and no lost native game payloads."""

import json
from pathlib import Path
import plistlib
import stat
import tarfile
import unittest
from unittest.mock import patch
import zipfile

import build_notes
import exporter
import test_desktop_exports as desktop_fixtures


class NotesTests(unittest.TestCase):
    # Reuse the fixture, not the TestCase class: don't discover its tests twice.
    setUp = desktop_fixtures.DesktopExportTests.setUp

    def notes(self, **kwargs):
        return build_notes.prepare_build_notes(
            self.love, "Game", runtime_license=b"Unmodified runtime license\r\n", **kwargs,
        )

    def test_missing_license_is_a_notice_not_an_invented_license(self):
        documents = self.notes(developer_name="My Studio")
        self.assertIn(b"GAME LICENSE NOT PROVIDED", documents["licenses/GAME-LICENSE.txt"])
        self.assertIn(b"does not grant permission", documents["licenses/GAME-LICENSE.txt"])
        metadata = json.loads(documents["build-metadata.json"])
        self.assertFalse(metadata["game_license_provided"])
        self.assertEqual(metadata["developer"], "My Studio")
        self.assertEqual(documents["licenses/LOVE-LICENSE.txt"], b"Unmodified runtime license\r\n")

    def test_project_license_is_auto_detected_and_copied_byte_exact(self):
        license_bytes = b"\xef\xbb\xbfDeveloper-authored terms.\r\nKeep this copyright.\r\n"
        (self.project / "LICENSE.md").write_bytes(license_bytes)
        self.love = exporter.create_love_file(self.project, self.love)
        documents = self.notes(developer_name="Örnek Stüdyo")
        self.assertEqual(documents["licenses/GAME-LICENSE.txt"], license_bytes)
        self.assertIn("Örnek Stüdyo", documents["CREDITS.txt"].decode())
        self.assertEqual(json.loads(documents["build-metadata.json"])["game_license_source"], "Project root: LICENSE.md")

    def test_selected_license_takes_priority_without_changing_game_archive(self):
        (self.project / "LICENSE").write_text("Old project terms", encoding="utf-8")
        self.love = exporter.create_love_file(self.project, self.love)
        original_game = self.love.read_bytes()
        selected = self.root / "Selected terms.txt"
        selected.write_bytes(b"My explicitly selected terms\r\n")
        documents = self.notes(game_license_file=selected)
        self.assertEqual(documents["licenses/GAME-LICENSE.txt"], selected.read_bytes())
        self.assertEqual(self.love.read_bytes(), original_game)
        self.assertNotIn(str(self.root), documents["build-metadata.json"].decode())

    def test_nested_asset_license_is_not_the_license_of_the_whole_game(self):
        (self.project / "assets" / "LICENSE").write_text("An asset's terms", encoding="utf-8")
        self.love = exporter.create_love_file(self.project, self.love)
        self.assertIn(b"NOT PROVIDED", self.notes()["licenses/GAME-LICENSE.txt"])

    def test_developer_validation_accepts_unicode_but_not_multiline_values(self):
        self.assertEqual(build_notes.normalize_developer_name("  Örnek & Partners  "), "Örnek & Partners")
        self.assertEqual(build_notes.normalize_developer_name(""), "")
        for name in ("Studio\nInjected credit", "Name\x00", "x" * 161):
            with self.subTest(name=name), self.assertRaises(ValueError):
                build_notes.normalize_developer_name(name)

    def test_license_path_validation_rejects_missing_empty_and_nontext_files(self):
        with self.assertRaises(FileNotFoundError):
            build_notes.validate_license_path(self.root / "missing.txt")
        empty = self.root / "empty.txt"
        empty.touch()
        with self.assertRaises(ValueError):
            build_notes.validate_license_path(empty)
        with self.assertRaises(ValueError):
            build_notes.validate_license_path(self.icon)

    def test_windows_folder_and_archive_keep_game_and_runtime_licenses_separate(self):
        (self.project / "LICENSE").write_bytes(b"My game license, not the engine license.\n")
        self.love = exporter.create_love_file(self.project, self.love)
        with patch.object(exporter, "_rar_executable", return_value=None), self.assertWarns(RuntimeWarning):
            package = exporter.export_windows(self.love, None, self.root / "Windows", "Game", developer_name="Studio")
        with zipfile.ZipFile(package) as archive:
            self.assertEqual(archive.read("Game/licenses/GAME-LICENSE.txt"), b"My game license, not the engine license.\n")
            original_runtime = (exporter._windows_template_dir() / "license.txt").read_bytes()
            self.assertEqual(archive.read("Game/license.txt"), original_runtime)
            self.assertEqual(archive.read("Game/licenses/LOVE-LICENSE.txt"), original_runtime)
            self.assertIn(b"Developer / Studio: Studio", archive.read("Game/CREDITS.txt"))

    def test_mac_copies_notices_inside_and_outside_app_and_sets_developer_metadata(self):
        selected = self.root / "LICENSE.txt"
        selected.write_bytes(b"Unmodified chosen game terms\n")
        studio = "Örnek & Partners"
        package = exporter.export_mac(
            self.love, None, self.root / "Mac", "Game", developer_name=studio, game_license_file=selected,
        )
        prefix = "Game.app/Contents/Resources/"
        with zipfile.ZipFile(package) as archive:
            info = plistlib.loads(archive.read("Game.app/Contents/Info.plist"))
            self.assertEqual(info["LoveExporterDeveloper"], studio)
            self.assertIn(studio, info["NSHumanReadableCopyright"])
            self.assertTrue(info["CFBundleIdentifier"].isascii())
            for filename in ("CREDITS.txt", "licenses/GAME-LICENSE.txt", "licenses/LOVE-LICENSE.txt", "build-metadata.json"):
                self.assertEqual(archive.read(filename), archive.read(prefix + filename))
                self.assertEqual(stat.S_IMODE(archive.getinfo(prefix + filename).external_attr >> 16), 0o644)
            self.assertEqual(archive.read(prefix + "licenses/GAME-LICENSE.txt"), selected.read_bytes())
            self.assertEqual(archive.read(prefix + "license.txt"), archive.read(prefix + "licenses/LOVE-LICENSE.txt"))
            self.assertEqual(archive.read(prefix + "game.love"), self.love.read_bytes())
            self.assertNotEqual(exporter._mac_bundle_identifier("Game", studio), exporter._mac_bundle_identifier("Game", "Another Studio"))

    def test_binary_plist_format_is_retained_when_metadata_changes(self):
        original = plistlib.dumps({"CFBundleExecutable": "love"}, fmt=plistlib.FMT_BINARY)
        updated = exporter._renamed_plist(original, "Game", "My Studio")
        self.assertTrue(updated.startswith(b"bplist"))
        self.assertEqual(plistlib.loads(updated)["LoveExporterDeveloper"], "My Studio")

    def test_linux_tar_contains_studio_and_license_documents(self):
        selected = self.root / "LICENSE"
        selected.write_bytes(b"Developer's original game license")
        package = exporter.export_linux(self.love, self.root / "Linux", "love", developer_name="Linux Studio", game_license_file=selected)
        with tarfile.open(package) as archive:
            self.assertEqual(archive.extractfile("love-linux-x86_64/licenses/GAME-LICENSE.txt").read(), selected.read_bytes())
            self.assertIn(b"Linux Studio", archive.extractfile("love-linux-x86_64/CREDITS.txt").read())
            self.assertEqual(archive.getmember("love-linux-x86_64/AppRun").mode, 0o755)
            self.assertEqual(archive.getmember("love-linux-x86_64/licenses/GAME-LICENSE.txt").mode, 0o644)

    def test_web_metadata_is_escaped_and_start_overlay_is_untouched(self):
        # Stub ONLY the Node command. Exercise real staging/promotion and note writing.
        def build(command, **kwargs):
            directory = Path(command[-1])
            directory.mkdir()
            (directory / "index.html").write_text('<html><head></head><body><button id="start">Click to start</button><canvas id="canvas"></canvas></body></html>', encoding="utf-8")
            (directory / "game.wasm").write_bytes(b"wasm fixture")
            (directory / "game.data").write_bytes(b"data fixture")
            from subprocess import CompletedProcess
            return CompletedProcess(command, 0, "")

        studio = 'Studio "A" & <script>alert(1)</script>'
        with patch.object(exporter.shutil, "which", return_value="npx.cmd"), patch.object(exporter.subprocess, "run", side_effect=build):
            directory = exporter.export_web(self.love, self.root / "Web", "Game", developer_name=studio)
        contents = (directory / "index.html").read_text()
        self.assertIn('<button id="start">Click to start</button>', contents)
        self.assertIn('Studio &quot;A&quot; &amp; &lt;script&gt;', contents)
        self.assertNotIn("<script>alert(1)</script>", contents)
        self.assertIn("licenses/GAME-LICENSE.txt", contents)
        self.assertEqual((directory / "game.wasm").read_bytes(), b"wasm fixture")
        self.assertEqual((directory / "licenses" / "LOVEJS-LICENSE.txt").read_bytes(), (exporter.TEMPLATES_DIR / "licenses" / "LOVEJS-LICENSE.txt").read_bytes())
        exporter._add_web_credits(directory / "index.html", "Game", studio)
        self.assertEqual((directory / "index.html").read_text().count('id="love-exporter-credits"'), 1)


if __name__ == "__main__":
    unittest.main()
