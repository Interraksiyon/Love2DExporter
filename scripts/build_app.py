"""Build the clickable Windows exporter and a notice-complete release ZIP.

Run with a full Python/Tk installation: python scripts/build_app.py
All paths are repository-relative. No user's game or npm cache is bundled.
"""

from __future__ import annotations

import hashlib
from importlib import metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from branding import APP_VERSION, CREATOR_CREDIT


def collect_python_notices(destination: Path) -> None:
    """Copy the installed distributions' actual notices, without rewriting them."""
    versions = {}
    for name in ("customtkinter", "Pillow", "darkdetect", "packaging", "pyinstaller"):
        package = metadata.distribution(name)
        versions[name] = package.version
        found = []
        for file in package.files or ():
            path = Path(str(file))
            # Match license *files*, not packaging/licenses Python source code.
            if not path.name.casefold().startswith(("license", "copying", "notice")):
                continue
            source = Path(package.locate_file(file))
            if not source.is_file() or source.suffix in {".py", ".pyc"}:
                continue
            target = destination / name / str(file).replace("\\", "/")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            found.append(target)
        if not found:
            raise RuntimeError(f"No installed license notice found for {name}. Update the build configuration.")
    python_notice = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_notice.is_file():
        raise FileNotFoundError("The full Python installation's LICENSE.txt was not found.")
    (destination / "Python").mkdir(parents=True, exist_ok=True)
    shutil.copy2(python_notice, destination / "Python" / "LICENSE.txt")
    for file in (Path(sys.base_prefix) / "tcl").glob("*/license*"):
        if file.is_file():
            target = destination / "Tcl-Tk" / file.parent.name / file.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file, target)
    (destination / "installed-versions.json").write_text(json.dumps(versions, indent=2) + "\n", encoding="utf-8")


def make_release(executable: Path, notices: Path) -> Path:
    """Distribute the app with project/dependency notices, not source work folders."""
    release = ROOT / "dist" / f"LoveExporter-{APP_VERSION}-Windows-x64.zip"
    with zipfile.ZipFile(release, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(executable, "LoveExporter.exe")
        for file in ("LICENSE", "README.md", "THIRD_PARTY_NOTICES.md"):
            archive.write(ROOT / file, file)
        for file in sorted(notices.rglob("*")):
            if file.is_file():
                archive.write(file, "third-party/python/" + file.relative_to(notices).as_posix())
        for file in sorted((ROOT / "templates" / "licenses").glob("*")):
            if file.is_file():
                archive.write(file, "third-party/tools/" + file.name)
        archive.write(ROOT / "templates/love-11.5-win64/love-11.5-win64/license.txt", "third-party/LOVE-Windows-license.txt")
        archive.write(ROOT / "templates/linux/squashfs-root/license.txt", "third-party/LOVE-Linux-license.txt")
        with zipfile.ZipFile(ROOT / "templates/love-11.5-macos.zip") as mac:
            for file in mac.infolist():
                if not file.is_dir() and "license" in file.filename.casefold():
                    archive.writestr("third-party/macOS/" + file.filename, mac.read(file))
        archive.writestr("START-HERE.txt", (
            f"LÖVE Exporter {APP_VERSION}\n{CREATOR_CREDIT}\n\n"
            "Extract this ZIP and double-click LoveExporter.exe. No Python installation needed.\n"
            "Node.js/npm is required only if YOU export Web; players don't need Node.js.\n"
            "WinRAR is optional. Without it, Windows game exports use real ZIP archives.\n"
            "Choose your project, output folder and targets. Results appear in Build Center.\n"
            "The executable is unsigned. Only run releases from the repository you trust.\n"
            "Keep LICENSE and third-party/ notices with copies of this tool.\n"
        ))
    digest = hashlib.sha256(release.read_bytes()).hexdigest()
    (ROOT / "dist" / f"{release.name}.sha256").write_text(f"{digest}  {release.name}\n", encoding="ascii")
    return release


def main() -> int:
    if sys.platform != "win32":
        print("Build the Windows EXE on Windows. Source usage remains cross-platform.", file=sys.stderr)
        return 1
    import tkinter
    import exporter
    tkinter.Tcl()  # Fail before packaging if Tcl/Tk is missing/broken.
    exporter._validate_windows_runtime(exporter._windows_template_dir())
    exporter._linux_template_root()  # This resolver validates the full ELF runtime.
    required = ("templates/love-11.5-macos.zip", "rcedit-x64.exe", "LICENSE", "THIRD_PARTY_NOTICES.md", "assets/windows-version-info.txt")
    for file in required:
        if not (ROOT / file).is_file():
            raise FileNotFoundError(f"Required build input missing: {file}")
    build_root = ROOT / "build"
    build_root.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="app-notices-", dir=build_root) as temporary:
        notices = Path(temporary) / "python"
        notices.mkdir()
        collect_python_notices(notices)
        command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                   "--noconsole", "--onefile", "--name", "LoveExporter",
                   "--collect-all", "customtkinter", "--collect-all", "PIL",
                   "--version-file", str(ROOT / "assets/windows-version-info.txt")]
        for source, destination in (
            ("templates/love-11.5-win64/love-11.5-win64", "templates/win64"),
            ("templates/love-11.5-macos.zip", "templates"),
            ("templates/linux/squashfs-root", "templates/linux/squashfs-root"),
            ("templates/licenses", "templates/licenses"),
            ("rcedit-x64.exe", "."), ("LICENSE", "."), ("THIRD_PARTY_NOTICES.md", "."),
            (str(notices), "third-party/python"),
        ):
            command.extend(["--add-data", f"{ROOT / source};{destination}"])
        command.append(str(ROOT / "gui.py"))
        subprocess.run(command, cwd=ROOT, check=True)
        executable = ROOT / "dist" / "LoveExporter.exe"
        release = make_release(executable, notices)
    print(f"\nBuilt: {executable}\nShare: {release}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
