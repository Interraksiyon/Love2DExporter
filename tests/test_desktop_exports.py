"""Regression tests using the bundled LÖVE runtimes, not mock executable blobs.

Run: python -m unittest discover -s tests -v
On Windows, these tests actually start a headless, fused game with and without
a custom icon. Its main.lua must write a marker and exit successfully.
"""

from __future__ import annotations

import os
import io
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import plistlib

from PIL import Image

import exporter


ROOT = Path(__file__).resolve().parents[1]
SMOKE_MAIN = '''function love.load(args)
    local file = assert(io.open(assert(args[1], "Missing marker path"), "w"))
    file:write("FUSED=" .. tostring(love.filesystem.isFused()) .. "\\n")
    local payload = assert(love.filesystem.read("assets/payload.txt"))
    file:write(payload)
    file:close()
    love.event.quit(0)
end
'''
SMOKE_CONF = '''function love.conf(t)
    for name in pairs(t.modules) do t.modules[name] = false end
    t.modules.event = true
    t.modules.timer = true
    t.window = nil
    t.identity = "loveexporter-regression"
end
'''


class DesktopExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix=".export-test-", dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "Smoke Project"
        (self.project / "assets").mkdir(parents=True)
        (self.project / "main.lua").write_text(SMOKE_MAIN, encoding="utf-8")
        (self.project / "conf.lua").write_text(SMOKE_CONF, encoding="utf-8")
        (self.project / "assets" / "payload.txt").write_text("embedded-game-ok", encoding="utf-8")
        self.icon = self.root / "custom icon.png"
        # Deliberately rectangular and smaller than common icon sizes.
        Image.new("RGBA", (12, 8), (50, 125, 220, 255)).save(self.icon)
        self.love = exporter.create_love_file(self.project, self.root / "Source" / "Smoke.love")

    def test_love_has_root_main_and_preserves_game_assets(self):
        with zipfile.ZipFile(self.love) as archive:
            self.assertIn("main.lua", archive.namelist())
            self.assertIn("assets/payload.txt", archive.namelist())
            self.assertNotIn("Smoke Project/main.lua", archive.namelist())
            self.assertIsNone(archive.testzip())

    def test_main_lua_is_case_sensitive_even_on_windows(self):
        (self.project / "main.lua").rename(self.project / "Main.lua")
        with self.assertRaisesRegex(ValueError, "exactly main.lua"):
            exporter.create_love_file(self.project, self.root / "bad.love")

    def test_new_archive_never_zips_itself_or_its_staging_directory(self):
        output = self.project / "self.love"
        exporter.create_love_file(self.project, output)
        original = output.read_bytes()
        exporter.create_love_file(self.project, output)
        with zipfile.ZipFile(output) as archive:
            self.assertNotIn("self.love", archive.namelist())
            self.assertFalse(any(".love-build-" in name for name in archive.namelist()))
        self.assertEqual(original, output.read_bytes())

    def test_bad_archive_is_rejected_for_every_desktop_platform(self):
        bad = self.root / "wrong.love"
        with zipfile.ZipFile(bad, "w") as archive:
            archive.writestr("game/main.lua", "")
        functions = (
            lambda: exporter.export_windows(bad, None, self.root / "Win", "Game"),
            lambda: exporter.export_mac(bad, None, self.root / "Mac", "Game"),
            lambda: exporter.export_linux(bad, self.root / "Linux", "Game"),
        )
        for build in functions:
            with self.subTest(build=build), self.assertRaisesRegex(ValueError, "no main.lua at its root"):
                build()

    def test_failed_archive_creation_preserves_previous_good_output(self):
        original = self.love.read_bytes()
        with patch.object(exporter.zipfile.ZipFile, "write", side_effect=OSError("unreadable asset")):
            with self.assertRaises(OSError):
                exporter.create_love_file(self.project, self.love)
        self.assertEqual(self.love.read_bytes(), original)

    def test_icon_edit_happens_before_game_is_attached(self):
        def destructive_resource_editor(executable, icon):
            self.assertFalse(zipfile.is_zipfile(executable), "Never edit a fused executable")
            executable.write_bytes(b"edited executable prefix")

        with patch.object(exporter, "_set_windows_icon", destructive_resource_editor), \
             patch.object(exporter, "_rar_executable", return_value=None), \
             self.assertWarns(RuntimeWarning):
            artifact = exporter.export_windows(self.love, self.icon, self.root / "Windows", "Game")
        executable = artifact.parent / "Game" / "Game.exe"
        exporter._verify_fused_binary(executable, self.love)
        with zipfile.ZipFile(artifact) as archive:
            self.assertEqual(archive.read("Game/Game.exe"), executable.read_bytes())
            for library in exporter.WINDOWS_RUNTIME_DLLS:
                self.assertIn(f"Game/{library}", archive.namelist())

    def test_incomplete_windows_runtime_is_an_error_not_a_broken_build(self):
        runtime = self.root / "partial-win64"
        runtime.mkdir()
        shutil.copy2(exporter._windows_template_dir() / "love.exe", runtime / "love.exe")
        with patch.object(exporter, "_windows_template_dir", return_value=runtime):
            with self.assertRaisesRegex(FileNotFoundError, "runtime is incomplete"):
                exporter.export_windows(self.love, None, self.root / "Windows", "Game")

    def test_windows_rebuild_replaces_folder_and_archive_together(self):
        with patch.object(exporter, "_rar_executable", return_value=None), self.assertWarns(RuntimeWarning):
            archive = exporter.export_windows(self.love, None, self.root / "Windows", "Game")
        executable = archive.parent / "Game" / "Game.exe"
        old = executable.read_bytes()
        with patch.object(exporter, "_create_zip_archive", side_effect=RuntimeError("disk full")), \
             patch.object(exporter, "_rar_executable", return_value=None), self.assertWarns(RuntimeWarning):
            with self.assertRaisesRegex(RuntimeError, "disk full"):
                exporter.export_windows(self.love, None, archive.parent, "Game")
        self.assertEqual(executable.read_bytes(), old)
        with zipfile.ZipFile(archive) as package:
            self.assertEqual(package.read("Game/Game.exe"), old)
        with patch.object(exporter, "_rar_executable", return_value=None), self.assertWarns(RuntimeWarning):
            exporter.export_windows(self.love, None, archive.parent, "Game")
        self.assertFalse(list(archive.parent.glob(".*-previous-*")))

    @unittest.skipUnless(os.name == "nt", "native Windows runtime test")
    def test_archive_replace_retries_a_brief_windows_lock_without_deleting_output(self):
        source, destination = self.root / "new.zip", self.root / "existing.zip"
        source.write_bytes(b"new build")
        destination.write_bytes(b"previous good build")
        real_replace = os.replace
        locked = PermissionError("Temporary sharing violation")
        locked.winerror = 32
        attempts = 0

        def replace(first, second):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                self.assertEqual(destination.read_bytes(), b"previous good build")
                raise locked
            return real_replace(first, second)

        with patch.object(exporter.os, "replace", side_effect=replace), patch.object(exporter.time, "sleep") as pause:
            exporter._replace_output_archive(source, destination)
        self.assertEqual(destination.read_bytes(), b"new build")
        self.assertEqual(attempts, 2)
        pause.assert_called_once()

    def test_failed_pair_promotion_restores_both_previous_outputs(self):
        with patch.object(exporter, "_rar_executable", return_value=None), self.assertWarns(RuntimeWarning):
            archive = exporter.export_windows(self.love, None, self.root / "Windows", "Game")
        executable = archive.parent / "Game" / "Game.exe"
        old_game, old_archive = executable.read_bytes(), archive.read_bytes()
        with patch.object(exporter, "_replace_output_archive", side_effect=PermissionError("Archive locked")), \
             patch.object(exporter, "_rar_executable", return_value=None), self.assertWarns(RuntimeWarning):
            with self.assertRaises(PermissionError):
                exporter.export_windows(self.love, None, archive.parent, "Game")
        self.assertEqual(executable.read_bytes(), old_game)
        self.assertEqual(archive.read_bytes(), old_archive)
        self.assertFalse(list(archive.parent.glob(".*-previous-*")))

    @unittest.skipUnless(os.name == "nt", "native Windows runtime test")
    def test_persistent_archive_lock_still_fails_and_keeps_previous_build(self):
        source, destination = self.root / "new.zip", self.root / "existing.zip"
        source.write_bytes(b"new build")
        destination.write_bytes(b"previous good build")
        locked = PermissionError("Persistent sharing violation")
        locked.winerror = 32
        with patch.object(exporter.os, "replace", side_effect=locked), patch.object(exporter.time, "sleep"):
            with self.assertRaises(PermissionError):
                exporter._replace_output_archive(source, destination)
        self.assertEqual(destination.read_bytes(), b"previous good build")
        self.assertTrue(source.is_file())

    @unittest.skipUnless(os.name == "nt", "native Windows runtime test")
    def test_real_windows_game_runs_with_and_without_custom_icon(self):
        environment = dict(os.environ)
        # The EXE must use its adjacent DLLs, not a development LÖVE installation.
        environment["PATH"] = str(Path(os.environ["SystemRoot"]) / "System32")
        for icon in (None, self.icon):
            with self.subTest(icon=icon), patch.object(exporter, "_rar_executable", return_value=None), \
                 self.assertWarns(RuntimeWarning):
                output = self.root / ("Icon build" if icon else "Default build")
                artifact = exporter.export_windows(
                    self.love, icon, output, "Smoke Game", developer_name="Regression Studio",
                )
                executable = artifact.parent / "Smoke Game" / "Smoke Game.exe"
                marker = self.root / "marker.txt"
                marker.unlink(missing_ok=True)
                completed = subprocess.run(
                    [str(executable), str(marker)], cwd=self.root,
                    env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout)
                self.assertEqual(marker.read_text(), "FUSED=true\nembedded-game-ok")
                # Reading a version field does not edit the EXE or its game overlay.
                company = subprocess.run(
                    [str(exporter.RCEDIT_PATH), str(executable), "--get-version-string", "CompanyName"],
                    capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                self.assertEqual(company.returncode, 0, company.stderr)
                self.assertIn("Regression Studio", company.stdout)
                exporter._verify_fused_binary(executable, self.love)

    def test_linux_love_named_game_is_not_deleted(self):
        package = exporter.export_linux(self.love, self.root / "Linux", "love")
        with tarfile.open(package) as archive:
            binary = archive.getmember("love-linux-x86_64/bin/love")
            self.assertEqual(binary.mode, 0o755)
            data = archive.extractfile(binary).read()
            self.assertTrue(data.endswith(self.love.read_bytes()))
            for library in exporter.LINUX_RUNTIME_LIBRARIES:
                self.assertIsNotNone(archive.getmember(f"love-linux-x86_64/lib/{library}"))
            launcher = archive.extractfile("love-linux-x86_64/AppRun").read().decode()
            self.assertIn('exec "$APPDIR/bin/love" "$@"', launcher)
            self.assertNotIn("FUSE_PATH", launcher)
            self.assertNotIn("beta3", launcher)
            self.assertNotIn("\r", launcher)
            self.assertEqual(archive.getmember("love-linux-x86_64/AppRun").mode, 0o755)
            self.assertTrue(archive.getmember("love-linux-x86_64/.DirIcon").issym())

    def test_linux_name_and_launcher_are_consistent(self):
        package = exporter.export_linux(self.love, self.root / "Linux", "Game's Demo")
        with tarfile.open(package) as archive:
            root = "Game's Demo-linux-x86_64"
            binary_name = exporter._linux_binary_name("Game's Demo")
            self.assertIsNotNone(archive.getmember(f"{root}/bin/{binary_name}"))
            self.assertNotIn(f"{root}/bin/love", archive.getnames())
            launcher = archive.extractfile(f"{root}/AppRun").read().decode()
            self.assertIn(f'bin/{binary_name}" "$@"', launcher)

    def test_incomplete_linux_runtime_is_detected(self):
        runtime = self.root / "partial-linux"
        (runtime / "bin").mkdir(parents=True)
        template = exporter._linux_template_root()
        shutil.copy2(template / "bin" / "love", runtime / "bin" / "love")
        shutil.copy2(template / "AppRun", runtime / "AppRun")
        with patch.dict(os.environ, {"LOVE_LINUX_TEMPLATE": str(runtime)}):
            with self.assertRaisesRegex(FileNotFoundError, "runtime is incomplete"):
                exporter.export_linux(self.love, self.root / "Linux", "Game")

    def test_mac_bundle_game_name_libraries_modes_and_links(self):
        name = "Game's Demo"
        package = exporter.export_mac(self.love, self.icon, self.root / "Mac", name)
        prefix = f"{name}.app/"
        with zipfile.ZipFile(package) as archive:
            info = plistlib.loads(archive.read(f"{prefix}Contents/Info.plist"))
            self.assertEqual(info["CFBundleExecutable"], name)
            self.assertEqual(info["CFBundleName"], name)
            self.assertNotIn("UTExportedTypeDeclarations", info)
            self.assertNotIn("CFBundleDocumentTypes", info)
            binary = archive.getinfo(f"{prefix}Contents/MacOS/{name}")
            self.assertEqual(stat.S_IMODE(binary.external_attr >> 16), 0o755)
            self.assertEqual(binary.create_system, 3)
            self.assertEqual(archive.read(f"{prefix}Contents/Resources/game.love"), self.love.read_bytes())
            links = [entry for entry in archive.infolist() if stat.S_ISLNK(entry.external_attr >> 16)]
            self.assertGreaterEqual(len(links), 30)
            self.assertIn("Prepare-macOS.command", archive.namelist())
            self.assertIn(b"NOT Developer", archive.read("README-macOS.txt"))
            with Image.open(io.BytesIO(archive.read(f"{prefix}Contents/Resources/OS X AppIcon.icns"))) as image:
                self.assertEqual(image.format, "ICNS")
            self.assertIsNone(archive.testzip())

    def test_bundle_identifier_is_ascii_and_stable(self):
        name = "LÖVE Türkçe"
        identifier = exporter._mac_bundle_identifier(name)
        self.assertTrue(identifier.isascii())
        self.assertEqual(identifier, exporter._mac_bundle_identifier(name))
        self.assertNotEqual(identifier, exporter._mac_bundle_identifier("L-VE T-rk-e"))

    def test_unsafe_output_names_are_rejected(self):
        for name in ("..", ".", "CON", "NUL.txt", "Game/evil", "Game.", "bad\nname", "A:B"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                exporter._game_name(name)


if __name__ == "__main__":
    unittest.main()
