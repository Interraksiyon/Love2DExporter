"""Platform-specific build helpers for LÖVE projects.

Start reading at create_love_file(), then the four export_*() functions. Each
export validates input, builds in a temporary folder, checks the result, and
publishes the finished package. A "fused" binary is simply the LÖVE executable
with the game's ZIP bytes appended; this does not compile the Lua game code.

gui.py handles widgets/background work. build_notes.py handles developer credits
and license documents. Helpers below the public exports handle validation,
packaging, icons and platform launchers; they never access the GUI.
"""

from __future__ import annotations

from copy import copy
from html import escape
import os
import plistlib
import posixpath
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
import warnings
import zipfile
from pathlib import Path, PurePosixPath
from typing import TypeAlias

from build_notes import normalize_developer_name, prepare_build_notes, write_build_notes


PathInput: TypeAlias = str | os.PathLike[str]

# Resource paths and runtime versions ----------------------------------------


def resource_path(relative_path: str | os.PathLike[str]) -> str:
    """Return a bundled resource path in development and PyInstaller builds."""
    # PyInstaller extracts --onefile data into sys._MEIPASS at launch. During
    # normal development, use this module's directory rather than the current
    # working directory so launching gui.py from elsewhere remains reliable.
    base_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return str(base_path / relative_path)


PROJECT_ROOT = Path(resource_path("."))
TEMPLATES_DIR = Path(resource_path("templates"))
RCEDIT_PATH = Path(resource_path("rcedit-x64.exe"))
LOVEJS_PACKAGE = "love.js@11.4.1"
WEB_EXPORT_TIMEOUT_SECONDS = 10 * 60
WINDOWS_RUNTIME_DLLS = (
    "love.dll", "lua51.dll", "mpg123.dll", "msvcp120.dll", "msvcr120.dll",
    "OpenAL32.dll", "SDL2.dll",
)
LINUX_RUNTIME_LIBRARIES = (
    "liblove-11.5.so", "libluajit-5.1.so.2", "libSDL2-2.0.so.0",
    "libopenal.so.1", "libfreetype.so.6", "libmodplug.so.1", "libmpg123.so.0",
    "libogg.so.0", "libvorbis.so.0", "libvorbisfile.so.3", "libtheoradec.so.1",
    "libpng16.so.16", "libz.so.1", "libbrotlidec.so.1", "libbrotlicommon.so.1",
)


# Input and archive checks ---------------------------------------------------

def validate_project_directory(project_dir: PathInput) -> Path:
    """Require the exact main.lua filename, including on case-insensitive hosts."""
    project = Path(project_dir).expanduser().resolve()
    if not project.is_dir():
        raise NotADirectoryError(f"LÖVE project folder does not exist: {project}")
    if not any(child.name == "main.lua" and child.is_file() for child in project.iterdir()):
        raise ValueError(
            "The project must contain a file named exactly main.lua (lowercase) "
            "at its root. Select the game folder, not its parent folder."
        )
    return project


def _require_love_archive(path: PathInput) -> Path:
    """Reject corrupt or incorrectly nested games before producing a runtime."""
    source = _require_file(path, ".love file")
    _validate_love_archive(source)
    return source


def _validate_love_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if "main.lua" not in names or archive.getinfo("main.lua").is_dir():
                raise ValueError(
                    "The game archive has no main.lua at its root. "
                    "Re-export the folder containing main.lua."
                )
            if len(set(names)) != len(names):
                raise ValueError("The game archive contains duplicate file paths.")
            for name in names:
                parts = PurePosixPath(name).parts
                if name.startswith("/") or "\\" in name or ".." in parts:
                    raise ValueError(f"Unsafe path inside the game archive: {name}")
            damaged = archive.testzip()
            if damaged:
                raise ValueError(f"The game archive contains a damaged file: {damaged}")
    except (zipfile.BadZipFile, RuntimeError, EOFError) as error:
        raise ValueError(f"The game archive is not a readable ZIP: {path}") from error


def _verify_fused_binary(binary: Path, love_archive: Path) -> None:
    """Verify the byte-exact overlay AND its playable ZIP, not just file size."""
    archive_size = love_archive.stat().st_size
    if binary.stat().st_size <= archive_size:
        raise ValueError(f"The exported executable has no LÖVE runtime: {binary}")
    with binary.open("rb") as executable, love_archive.open("rb") as game:
        executable.seek(-archive_size, os.SEEK_END)
        while chunk := game.read(1024 * 1024):
            if executable.read(len(chunk)) != chunk:
                raise ValueError(
                    "The exported executable lost its embedded game. "
                    "Resource editing must happen BEFORE attaching the .love archive."
                )
    _validate_love_archive(binary)


def create_love_file(project_dir: PathInput, output_path: PathInput) -> Path:
    """Create a ``.love`` archive containing the contents of *project_dir*.

    Archive members are stored relative to the project directory, so the
    project folder itself is not an extra top-level directory inside the
    resulting archive.
    Editor/cache folders are skipped. The previous archive is replaced only
    after the new ZIP passes validation. License/credits packaging happens
    later, so the game's original source files are not rewritten here.
    """
    project = validate_project_directory(project_dir)

    destination = Path(output_path).expanduser()
    if destination.suffix.lower() != ".love":
        destination = destination.with_suffix(".love")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()

    # Leave the previous good archive untouched if reading a source file fails.
    with tempfile.TemporaryDirectory(prefix=".love-build-", dir=destination.parent) as temp:
        staging_root = Path(temp).resolve()
        staged_archive = staging_root / "game.love"
        with zipfile.ZipFile(staged_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for source in sorted(project.rglob("*"), key=lambda path: path.as_posix().casefold()):
                if source.resolve() == destination or source.is_relative_to(staging_root):
                    continue
                relative_path = source.relative_to(project)
                if _skip_project_path(relative_path):
                    continue
                if not source.resolve().is_relative_to(project):
                    raise ValueError(f"Project link points outside the project: {source}")
                archive_name = relative_path.as_posix()
                archive.write(source, f"{archive_name}/" if source.is_dir() else archive_name)
        _validate_love_archive(staged_archive)
        os.replace(staged_archive, destination)

    return destination


def _skip_project_path(relative_path: Path) -> bool:
    """Exclude editor, cache, and version-control files from a .love archive."""
    ignored_directories = {
        ".git",
        ".hg",
        ".svn",
        ".idea",
        ".vscode",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
    }
    ignored_files = {".ds_store", "thumbs.db"}
    return (
        any(part.casefold() in ignored_directories for part in relative_path.parts)
        or relative_path.name.casefold() in ignored_files
    )


# Public platform exports ----------------------------------------------------
# developer_name and game_license_file are optional keyword arguments. Existing
# calls using the original positional arguments continue to work unchanged.

def export_windows(
    love_file: PathInput,
    icon_png: PathInput | None,
    output_dir: PathInput,
    game_name: str,
    *,
    developer_name: str = "",
    game_license_file: PathInput | None = None,
) -> Path:
    """Return a RAR/ZIP, and keep a playable EXE folder beside that archive.

    Developer credit appears in the EXE's CompanyName and package documents.
    License text is copied by build_notes.py; it is never applied to LÖVE itself.
    """
    love_archive = _require_love_archive(love_file)
    icon = _optional_file(icon_png, "PNG icon")
    destination = _ensure_output_dir(output_dir)
    name = _game_name(game_name)
    template_dir = _windows_template_dir()
    _validate_windows_runtime(template_dir)
    developer = normalize_developer_name(developer_name)
    documents = prepare_build_notes(
        love_archive, name, developer,
        Path(game_license_file) if game_license_file else None,
        runtime_name="LÖVE 11.5 (Windows x86_64)",
        runtime_license=(template_dir / "license.txt").read_bytes(),
    )

    # Build in a private staging directory. The user only ever sees a complete
    # archive and playable folder, never a partially fused executable.
    with tempfile.TemporaryDirectory(prefix=f".{name}-windows-", dir=destination) as temp_dir:
        staging_root = Path(temp_dir)
        package_dir = staging_root / name
        package_dir.mkdir()
        executable = package_dir / f"{name}.exe"

        shutil.copy2(template_dir / "love.exe", executable)
        if developer:
            # Company/product properties are PE resources too. Just like icons,
            # change them BEFORE appending the game or rcedit will erase it.
            _set_windows_developer(executable, name, developer)
        if icon is not None:
            ico_file = staging_root / f"{name}.ico"
            _png_to_ico(icon, ico_file)
            # rcedit/UpdateResource discards PE overlays. Editing a fused EXE
            # was removing the entire game and launching LÖVE's empty screen.
            _set_windows_icon(executable, ico_file)
        with love_archive.open("rb") as game_archive, executable.open("ab") as fused_executable:
            shutil.copyfileobj(game_archive, fused_executable, length=1024 * 1024)
        _verify_fused_binary(executable, love_archive)

        # These dependencies live beside the fused executable, which makes the
        # extracted package runnable on a 64-bit Windows PC without LÖVE.
        for dll in template_dir.glob("*.dll"):
            shutil.copy2(dll, package_dir / dll.name)

        license_file = template_dir / "license.txt"
        if license_file.is_file():
            shutil.copy2(license_file, package_dir / license_file.name)
        _write_windows_readme(package_dir, name, developer)
        write_build_notes(package_dir, documents)

        rar_executable = _rar_executable()
        if rar_executable is not None:
            archive = destination / f"{name}_Windows.rar"
            staged_archive = staging_root / archive.name
            _create_rar_archive(rar_executable, staged_archive, package_dir)
        else:
            # ZIP remains usable on every supported Windows machine. It is an
            # explicit fallback, never ZIP data disguised with a .rar suffix.
            warnings.warn(
                "WinRAR was not found; created a ZIP package instead of a RAR archive.",
                RuntimeWarning,
                stacklevel=2,
            )
            archive = destination / f"{name}_Windows.zip"
            staged_archive = staging_root / archive.name
            _create_zip_archive(staged_archive, package_dir)

        if archive.exists() and not archive.is_file():
            raise FileExistsError(
                f"Windows package path exists but is not a file: {archive}"
            )
        _publish_windows_package(package_dir, destination / name, staged_archive, archive)

    return archive


def export_mac(
    love_file: PathInput,
    icon_png: PathInput | None,
    output_dir: PathInput,
    game_name: str,
    *,
    developer_name: str = "",
    game_license_file: PathInput | None = None,
) -> Path:
    """Return a complete .app ZIP, preserving macOS file modes and links.

    Developer data is written to Info.plist and readable notes. These are app
    metadata, not an Apple signing identity; signing is a separate Mac step.
    """
    love_archive = _require_love_archive(love_file)
    icon = _optional_file(icon_png, "PNG icon")
    destination = _ensure_output_dir(output_dir)
    name = _game_name(game_name)
    template_archive, template_prefix = _mac_template_archive()
    developer = normalize_developer_name(developer_name)
    with zipfile.ZipFile(template_archive) as template_zip:
        runtime_license = template_zip.read(f"{template_prefix}Contents/Resources/license.txt")
    documents = prepare_build_notes(
        love_archive, name, developer,
        Path(game_license_file) if game_license_file else None,
        runtime_name="LÖVE 11.5 (macOS)", runtime_license=runtime_license,
    )
    output_archive = destination / f"{name}_mac.zip"
    if output_archive.exists() and not output_archive.is_file():
        raise FileExistsError(
            f"macOS package path exists but is not a file: {output_archive}"
        )

    # The canonical template ZIP retains the framework symlinks and Unix
    # permissions that an extracted .app loses on a Windows filesystem.
    with tempfile.TemporaryDirectory(prefix="love-macos-", dir=destination) as temp_dir:
        staged_archive = Path(temp_dir) / output_archive.name
        custom_icon = Path(temp_dir) / "OS X AppIcon.icns"
        icon_data = None
        if icon is not None and _create_mac_icns(icon, custom_icon):
            icon_data = custom_icon.read_bytes()

        _write_macos_zip(
            template_archive=template_archive,
            template_prefix=template_prefix,
            output_archive=staged_archive,
            love_archive=love_archive,
            game_name=name,
            icon_data=icon_data,
            developer_name=developer,
            documents=documents,
        )
        _validate_macos_package(staged_archive, love_archive, name)
        os.replace(staged_archive, output_archive)

    return output_archive


def export_linux(
    love_file: PathInput,
    output_dir: PathInput,
    game_name: str,
    *,
    developer_name: str = "",
    game_license_file: PathInput | None = None,
) -> Path:
    """Fuse a staged LÖVE Linux runtime and package it as a ``.tar.gz`` file.

    The pre-extracted AppImage filesystem must live in
    ``templates/linux/squashfs-root``.  Its ``AppRun`` launcher and complete
    ``lib`` tree are copied into the output archive alongside the fused binary.
    """
    love_archive = _require_love_archive(love_file)
    destination = _ensure_output_dir(output_dir)
    name = _game_name(game_name)
    template_root = _linux_template_root()
    developer = normalize_developer_name(developer_name)
    documents = prepare_build_notes(
        love_archive, name, developer,
        Path(game_license_file) if game_license_file else None,
        runtime_name="LÖVE 11.5 (Linux x86_64)",
        runtime_license=(template_root / "license.txt").read_bytes(),
    )
    package_name = f"{name}-linux-x86_64"
    output_archive = destination / f"{name}_Linux_x86_64.tar.gz"
    if output_archive.exists() and not output_archive.is_file():
        raise FileExistsError(
            f"Linux package path exists but is not a file: {output_archive}"
        )

    with tempfile.TemporaryDirectory(prefix="love-linux-", dir=destination) as temp_dir:
        staged_root = Path(temp_dir) / package_name
        staged_archive = Path(temp_dir) / output_archive.name
        shutil.copytree(template_root, staged_root, symlinks=True)
        love_binary = staged_root / "bin" / "love"
        binary_name = _linux_binary_name(name)
        game_binary = staged_root / "bin" / binary_name
        _fuse_linux_binary(love_binary, game_binary, love_archive)
        _verify_fused_binary(game_binary, love_archive)
        # A project named 'love' uses the original path; do not delete its game.
        if game_binary != love_binary:
            love_binary.unlink()
        _configure_linux_launcher(staged_root, binary_name, name)
        _write_linux_readme(staged_root, name, developer)
        write_build_notes(staged_root, documents)

        with tarfile.open(staged_archive, "w:gz", dereference=False) as archive:
            archive.add(
                staged_root,
                arcname=package_name,
                recursive=True,
                filter=_linux_tar_filter,
            )
            # 7-Zip on Windows turns this AppImage symlink into an empty file.
            # Store the original icon link as POSIX metadata, just as on Linux.
            dir_icon = staged_root / ".DirIcon"
            if dir_icon.is_file() and not dir_icon.is_symlink() and dir_icon.stat().st_size == 0:
                # The empty placeholder is omitted by the tar filter below.
                if (staged_root / "love.svg").is_file():
                    link = tarfile.TarInfo(f"{package_name}/.DirIcon")
                    link.type = tarfile.SYMTYPE
                    link.linkname = "love.svg"
                    link.mode = 0o777
                    archive.addfile(link)
        _validate_linux_package(staged_archive, love_archive, package_name, binary_name)
        os.replace(staged_archive, output_archive)

    return output_archive


def export_web(
    love_file: PathInput,
    output_dir: PathInput,
    game_name: str,
    *,
    developer_name: str = "",
    game_license_file: PathInput | None = None,
    memory_bytes: int | None = None,
    apply_pixel_art_css: bool = True,
) -> Path:
    """Run love.js through npx and optionally style the canvas for pixel art.

    ``memory_bytes`` controls love.js's initial WebAssembly memory allocation.
    When omitted, a safe value based on the archive size is used; set
    ``LOVE_EXPORTER_WEB_MEMORY_BYTES`` to override it for all builds.
    """
    love_archive = _require_file(love_file, ".love file")
    destination = _ensure_output_dir(output_dir)
    name = _game_name(game_name)
    web_output = destination / f"{name}_web"
    initial_memory = _web_memory_bytes(love_archive, memory_bytes)
    developer = normalize_developer_name(developer_name)

    # `love.js.cmd` is a Windows shim created after installing love.js, not an
    # npm package. Explicitly name the package first, then run that shim. This
    # avoids npm 11 interpreting `love.js.cmd` as a package name and returning
    # the E404 shown by the previous exporter build.
    if os.name == "nt":
        npx = shutil.which("npx.cmd") or shutil.which("npx")
        lovejs_binary = "love.js.cmd"
    else:
        npx = shutil.which("npx")
        lovejs_binary = "love.js"
    if npx is None:
        raise RuntimeError("Node.js and npx are required for Web exports.")

    package = os.environ.get("LOVE_EXPORTER_LOVEJS_PACKAGE", LOVEJS_PACKAGE).strip()
    if not package:
        raise ValueError("LOVE_EXPORTER_LOVEJS_PACKAGE must not be empty.")
    lovejs_license = TEMPLATES_DIR / "licenses" / "LOVEJS-LICENSE.txt"
    extra_licenses = {}
    if package == LOVEJS_PACKAGE and lovejs_license.is_file():
        extra_licenses["LOVEJS-LICENSE.txt"] = lovejs_license.read_bytes()
    documents = prepare_build_notes(
        love_archive, name, developer,
        Path(game_license_file) if game_license_file else None,
        runtime_name=f"LÖVE via {package}", runtime_license=_web_runtime_license(),
        runtime_note=(
            "This LÖVE notice bundle is copied from the included desktop template; "
            "it may mention components not used by Web. Review the actual Web "
            "dependencies when distributing. The bundled love.js notice applies "
            "to the default pinned package, not an arbitrary package override."
        ),
        extra_licenses=extra_licenses,
    )
    if web_output.exists() and not web_output.is_dir():
        raise FileExistsError(
            f"Web output path exists but is not a folder: {web_output}"
        )

    # Work in a sibling staging folder. A failed Node build then cannot erase
    # a previously good web export or leave it half-replaced.
    staging_parent = Path(
        tempfile.mkdtemp(prefix=f".{name}-web-build-", dir=destination)
    )
    staged_output = staging_parent / web_output.name
    backup_output: Path | None = None
    promoted = False
    command = [
        npx,
        "--yes",
        f"--package={package}",
        "--",
        lovejs_binary,
        "-c",
        "-t",
        name,
        "-m",
        str(initial_memory),
        str(love_archive),
        str(staged_output),
    ]
    try:
        # Passing an argv list avoids shell quoting/injection bugs. Capturing
        # the tool output makes a Node/npm failure actionable in the GUI.
        try:
            completed = subprocess.run(
                command,
                cwd=destination,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=WEB_EXPORT_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            output = error.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            detail = _command_output_tail(output)
            raise RuntimeError(
                "Web export timed out after "
                f"{WEB_EXPORT_TIMEOUT_SECONDS // 60} minutes."
                f"{detail}"
            ) from error

        if completed.returncode != 0:
            raise RuntimeError(
                f"Web export failed (love.js exited with code {completed.returncode})."
                f"{_command_output_tail(completed.stdout)}"
            )

        index_file = staged_output / "index.html"
        if not index_file.is_file():
            raise FileNotFoundError(
                "love.js completed without creating index.html in the staged "
                f"Web build: {index_file}"
            )
        if apply_pixel_art_css:
            _add_pixel_art_canvas_style(index_file)
        _add_web_credits(index_file, name, developer)
        _write_web_readme(staged_output, name, developer)
        write_build_notes(staged_output, documents)

        if web_output.exists():
            backup_output = destination / (
                f".{web_output.name}-backup-{uuid.uuid4().hex}"
            )
            web_output.replace(backup_output)

        try:
            staged_output.replace(web_output)
        except Exception:
            if backup_output is not None and backup_output.exists() and not web_output.exists():
                backup_output.replace(web_output)
            raise

        promoted = True
        if backup_output is not None and backup_output.exists():
            shutil.rmtree(backup_output, ignore_errors=True)
        return web_output
    finally:
        # `staged_output` moves away on success; otherwise its parent is a
        # private temporary folder safe to clean up.
        if staging_parent.exists():
            shutil.rmtree(staging_parent, ignore_errors=True)
        if (
            not promoted
            and backup_output is not None
            and backup_output.exists()
            and not web_output.exists()
        ):
            backup_output.replace(web_output)


# Shared output / Web helpers ------------------------------------------------

def _web_runtime_license() -> bytes | None:
    """Reuse the unmodified bundled LÖVE notices without requiring a native build."""
    try:
        return (_windows_template_dir() / "license.txt").read_bytes()
    except (OSError, ValueError):
        return None  # Web can still build without desktop templates installed.


def _add_web_credits(index_file: Path, game_name: str, developer: str) -> None:
    """Add escaped author information and note links; leave the start overlay alone."""
    contents = index_file.read_text(encoding="utf-8")
    if 'id="love-exporter-credits"' in contents:
        return
    # Names are data, not markup: '<', quotes and '&' must not become HTML/code.
    author = escape(developer or "Not specified", quote=True)
    meta = f'\n<meta name="author" content="{author}">\n'
    end_head = contents.lower().rfind("</head>")
    if end_head != -1:
        contents = contents[:end_head] + meta + contents[end_head:]
    footer = (
        '\n<footer id="love-exporter-credits" style="padding:1rem;text-align:center;font:14px sans-serif">'
        f'{escape(game_name)} &mdash; Developer: {author}<br>'
        '<a href="CREDITS.txt">Credits</a> &middot; '
        '<a href="licenses/GAME-LICENSE.txt">Game license</a> &middot; '
        '<a href="licenses/README.txt">Third-party notices</a></footer>\n'
    )
    end_body = contents.lower().rfind("</body>")
    contents = contents[:end_body] + footer + contents[end_body:] if end_body != -1 else contents + footer
    index_file.write_text(contents, encoding="utf-8")


def _command_output_tail(
    output: str | None,
    *,
    tool_name: str = "love.js",
    limit: int = 4_000,
) -> str:
    """Return a concise, user-facing tail of a captured subprocess log."""
    cleaned = (output or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) > limit:
        cleaned = f"…{cleaned[-limit:]}"
    return f"\n\nDetails from {tool_name}:\n{cleaned}"


def _add_pixel_art_canvas_style(index_file: Path) -> None:
    """Inject an idempotent CSS rule for crisp pixel-art canvas scaling."""
    if not index_file.is_file():
        warnings.warn(
            f"love.js did not create an index.html file at {index_file}.",
            RuntimeWarning,
            stacklevel=2,
        )
        return

    marker = "love-exporter-canvas-style"
    contents = index_file.read_text(encoding="utf-8")
    if marker in contents:
        return

    style = (
        "\n<style id=\"love-exporter-canvas-style\">\n"
        "canvas#canvas {\n"
        "  image-rendering: pixelated;\n"
        "  image-rendering: crisp-edges;\n"
        "}\n"
        "</style>\n"
    )
    closing_head = contents.lower().rfind("</head>")
    closing_body = contents.lower().rfind("</body>")
    if closing_head != -1:
        contents = f"{contents[:closing_head]}{style}{contents[closing_head:]}"
    elif closing_body != -1:
        contents = f"{contents[:closing_body]}{style}{contents[closing_body:]}"
    else:
        contents += style

    index_file.write_text(contents, encoding="utf-8")


def _write_web_readme(web_directory: Path, game_name: str, developer: str = "") -> None:
    """Include deployment instructions with a completed web build."""
    (web_directory / "README.txt").write_text(
        f"{game_name} - Web build\nDeveloper / Studio: {developer or 'Not specified'}\n\n"
        "HOW TO PLAY\n"
        "1. Upload this whole folder to an HTTP(S) host, or use Test Web Build.\n"
        "2. Open the hosted page and click its start button to enable audio.\n"
        "Do not double-click index.html: file:// cannot load the WASM game.\n"
        "Players need a compatible browser, not LÖVE or Node.js.\n\n"
        "CREDITS AND TERMS\n"
        "See CREDITS.txt and licenses/README.txt. Keep these with your upload.\n",
        encoding="utf-8",
    )


def _web_memory_bytes(love_archive: Path, requested_memory: int | None) -> int:
    """Choose a configurable initial love.js memory allocation in bytes."""
    configured_memory = os.environ.get("LOVE_EXPORTER_WEB_MEMORY_BYTES")
    value = requested_memory if requested_memory is not None else configured_memory
    if value is not None:
        try:
            memory_bytes = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "Web memory must be an integer number of bytes."
            ) from error
        if memory_bytes < 16 * 1024 * 1024:
            raise ValueError("Web memory must be at least 16 MiB.")
        return memory_bytes

    # love.js defaults to 16 MiB. Start higher for most games while allowing
    # the runtime to grow dynamically; users can override this for large games.
    return max(64 * 1024 * 1024, love_archive.stat().st_size * 4)


def _ensure_output_dir(output_dir: PathInput) -> Path:
    destination = Path(output_dir).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    return destination.resolve()


def _require_file(path: PathInput, description: str) -> Path:
    source = Path(path).expanduser()
    if not source.is_file():
        raise FileNotFoundError(f"{description} was not found: {source}")
    return source.resolve()


def _optional_file(path: PathInput | None, description: str) -> Path | None:
    if path is None or not str(path).strip():
        return None
    return _require_file(path, description)


def _game_name(game_name: str) -> str:
    name = str(game_name).strip()
    if not name:
        raise ValueError("A game name is required.")
    if (
        name in {".", ".."}
        or name.endswith(".")
        or any(character in '<>:"/\\|?*' or ord(character) < 32 for character in name)
        or name.split(".", 1)[0].upper() in {
            "CON", "PRN", "AUX", "NUL",
            *(f"COM{number}" for number in range(1, 10)),
            *(f"LPT{number}" for number in range(1, 10)),
        }
    ):
        raise ValueError("Use a game/project folder name that is a valid Windows filename.")
    return name


# Windows runtime / resource / archive helpers ------------------------------

def _windows_template_dir() -> Path:
    candidates = (
        TEMPLATES_DIR / "win64",
        TEMPLATES_DIR / "love-11.5-win64" / "love-11.5-win64",
    )
    for candidate in candidates:
        if (candidate / "love.exe").is_file():
            return candidate

    matches = list(TEMPLATES_DIR.rglob("love.exe")) if TEMPLATES_DIR.is_dir() else []
    if matches:
        return matches[0].parent
    raise FileNotFoundError(
        "Windows LÖVE template not found. Expected templates/win64/love.exe."
    )


def _pe_machine(path: Path) -> int:
    """Read Windows' PE machine code (0x8664 = x86_64), without executing it."""
    with path.open("rb") as binary:
        header = binary.read(64)
        if len(header) < 64 or header[:2] != b"MZ":
            raise ValueError(f"Invalid Windows runtime binary: {path}")
        binary.seek(struct.unpack_from("<I", header, 60)[0])
        # Offset 60 in the DOS header points to the PE header; its first two
        # bytes after 'PE\0\0' identify the CPU architecture.
        pe_header = binary.read(6)
        if len(pe_header) != 6 or pe_header[:4] != b"PE\0\0":
            raise ValueError(f"Invalid Windows PE header: {path}")
        return struct.unpack_from("<H", pe_header, 4)[0]


def _validate_windows_runtime(template_dir: Path) -> None:
    missing = [name for name in (*WINDOWS_RUNTIME_DLLS, "license.txt") if not (template_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "Windows runtime is incomplete. Restore the complete LÖVE 11.5 win64 "
            f"template, including: {', '.join(missing)}"
        )
    for binary in [template_dir / "love.exe", *template_dir.glob("*.dll")]:
        if _pe_machine(binary) != 0x8664:
            raise ValueError(f"The win64 template contains a non-x86_64 binary: {binary.name}")


def _publish_windows_package(
    staged_folder: Path,
    final_folder: Path,
    staged_archive: Path,
    final_archive: Path,
) -> None:
    """Publish folder + archive as a recoverable pair, not a destructive overwrite.

    Move BOTH prior outputs aside first. Some Windows filesystems deny replacing
    an existing moved archive even when renaming it is allowed. Promoting to an
    empty name avoids that condition without changing ACLs or security settings.
    If promotion fails, put the old folder/archive back exactly where they were.
    """
    if final_folder.is_symlink() or (final_folder.exists() and not final_folder.is_dir()):
        raise FileExistsError(f"Windows game folder is not a normal directory: {final_folder}")
    if final_archive.is_symlink() or (final_archive.exists() and not final_archive.is_file()):
        raise FileExistsError(f"Windows archive path is not a normal file: {final_archive}")
    folder_backup: Path | None = None
    archive_backup: Path | None = None
    promoted_folder = False
    try:
        if final_folder.exists():
            folder_backup = final_folder.with_name(f".{final_folder.name}-previous-{uuid.uuid4().hex}")
            final_folder.replace(folder_backup)
        if final_archive.exists():
            archive_backup = final_archive.with_name(f".{final_archive.name}-previous-{uuid.uuid4().hex}")
            final_archive.replace(archive_backup)
        staged_folder.replace(final_folder)
        promoted_folder = True
        _replace_output_archive(staged_archive, final_archive)
    except Exception:
        if promoted_folder and final_folder.exists():
            final_folder.replace(staged_folder)
        if folder_backup is not None and folder_backup.exists():
            folder_backup.replace(final_folder)
        if archive_backup is not None and archive_backup.exists():
            archive_backup.replace(final_archive)
        raise
    if folder_backup is not None:
        # Exact exporter-owned backups only, after both new outputs succeeded.
        shutil.rmtree(folder_backup, ignore_errors=True)
    if archive_backup is not None:
        try:
            archive_backup.unlink()
        except OSError:
            pass  # A locked backup may remain; the finished build is still valid.


def _replace_output_archive(source: Path, destination: Path) -> None:
    """Tolerate a brief Windows file lock, without deleting a previous good build.

    Windows can briefly deny a replace while a scanner/indexer has the ZIP open.
    Retry only recognized Windows lock/access errors, for at most ~1.4 seconds;
    persistent permission problems still fail and trigger the caller's rollback.
    This runs on the export worker, never on the Tk main thread.
    """
    delays = (0.06, 0.12, 0.24, 0.4, 0.6)
    for attempt in range(len(delays) + 1):
        try:
            os.replace(source, destination)
            return
        except PermissionError as error:
            if os.name != "nt" or getattr(error, "winerror", None) not in {5, 32, 33} or attempt == len(delays):
                raise
            time.sleep(delays[attempt])


def _write_windows_readme(package_dir: Path, game_name: str, developer: str = "") -> None:
    """Add brief, practical launch instructions to a Windows distribution."""
    (package_dir / "README.txt").write_text(
        f"{game_name} - Windows x86_64\nDeveloper / Studio: {developer or 'Not specified'}\n\n"
        "HOW TO PLAY\n"
        "1. If received as RAR/ZIP, extract the ENTIRE archive first.\n"
        f"2. Open the game folder and double-click {game_name}.exe.\n"
        "Keep the EXE and every DLL together. LÖVE is already included.\n\n"
        "HOW TO SHARE\n"
        "Send the RAR/ZIP or the whole game folder, not the EXE by itself.\n"
        "Keep CREDITS.txt, licenses/, and the original runtime license.txt.\n",
        encoding="utf-8",
    )


def _set_windows_icon(executable: Path, icon_file: Path) -> None:
    """Edit the UNFUSED runtime: rcedit removes appended game data."""
    _run_rcedit(executable, ["--set-icon", str(icon_file)], "apply the Windows icon")


def _set_windows_developer(executable: Path, game_name: str, developer: str) -> None:
    """Write Windows Details properties without inventing copyright/license terms."""
    _run_rcedit(executable, [
        "--set-version-string", "CompanyName", developer,
        "--set-version-string", "ProductName", game_name,
        "--set-version-string", "FileDescription", game_name,
    ], "set the Windows developer metadata")


def _run_rcedit(executable: Path, arguments: list[str], description: str) -> None:
    """Run the resource editor only on a not-yet-fused staged executable."""
    if not RCEDIT_PATH.is_file():
        raise FileNotFoundError(f"rcedit executable was not found: {RCEDIT_PATH}")

    completed = subprocess.run(
        [str(RCEDIT_PATH), str(executable), *arguments],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Could not {description} (rcedit exited with code {completed.returncode})."
            f"{_command_output_tail(completed.stdout, tool_name='rcedit')}"
        )


def _rar_executable() -> Path | None:
    """Locate a user-installed WinRAR command-line executable, if available."""
    configured = os.environ.get("LOVE_EXPORTER_RAR_EXE", "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())

    located = shutil.which("rar.exe") or shutil.which("rar")
    if located:
        candidates.append(Path(located))

    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidates.append(Path(root) / "WinRAR" / "Rar.exe")

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _create_rar_archive(rar_executable: Path, archive: Path, package_dir: Path) -> None:
    """Create and integrity-test a RAR archive containing ``package_dir``."""
    completed = subprocess.run(
        [
            str(rar_executable),
            "a",
            "-cfg-",
            "-idq",
            "-y",
            "-r",
            "-ma5",
            "-m5",
            str(archive),
            package_dir.name,
        ],
        cwd=package_dir.parent,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0 or not archive.is_file():
        raise RuntimeError(
            f"Could not create the Windows RAR package (WinRAR exited with code "
            f"{completed.returncode})."
            f"{_command_output_tail(completed.stdout, tool_name='WinRAR')}"
        )

    verification = subprocess.run(
        [str(rar_executable), "t", "-cfg-", "-idq", str(archive)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if verification.returncode != 0:
        raise RuntimeError(
            "WinRAR created an archive that did not pass its integrity test."
            f"{_command_output_tail(verification.stdout, tool_name='WinRAR')}"
        )


def _create_zip_archive(archive: Path, package_dir: Path) -> None:
    """Create a portable ZIP fallback while retaining the game folder root."""
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
        root_entry = zipfile.ZipInfo(f"{package_dir.name}/")
        root_entry.external_attr = (stat.S_IFDIR | 0o755) << 16
        output.writestr(root_entry, b"")
        for source in sorted(package_dir.rglob("*")):
            relative = source.relative_to(package_dir).as_posix()
            archive_name = f"{package_dir.name}/{relative}"
            if source.is_dir():
                directory_entry = zipfile.ZipInfo(f"{archive_name}/")
                directory_entry.external_attr = (stat.S_IFDIR | 0o755) << 16
                output.writestr(directory_entry, b"")
            else:
                output.write(source, archive_name)
    with zipfile.ZipFile(archive) as output:
        if output.testzip() is not None:
            raise ValueError("The Windows ZIP package failed its integrity check.")


# Native template discovery and icon conversion -----------------------------

def _mac_template_archive() -> tuple[Path, str]:
    """Return the canonical macOS template ZIP and its root app prefix."""
    preferred = TEMPLATES_DIR / "love-11.5-macos.zip"
    candidates = [preferred]
    if TEMPLATES_DIR.is_dir():
        candidates.extend(
            candidate
            for candidate in TEMPLATES_DIR.rglob("*.zip")
            if candidate != preferred
        )

    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            with zipfile.ZipFile(candidate) as archive:
                for entry in archive.infolist():
                    suffix = "/Contents/Info.plist"
                    if entry.filename.endswith(suffix):
                        prefix = entry.filename.removesuffix(suffix)
                        if prefix.endswith(".app"):
                            return candidate.resolve(), f"{prefix}/"
        except zipfile.BadZipFile:
            continue

    raise FileNotFoundError(
        "macOS LÖVE template ZIP not found. Add a ZIP containing love.app "
        "under templates/."
    )


def _linux_template_root() -> Path:
    """Find the pre-extracted LÖVE AppImage filesystem used for Linux builds."""
    configured_path = os.environ.get("LOVE_LINUX_TEMPLATE")
    candidates = [
        Path(configured_path).expanduser()
        if configured_path
        else TEMPLATES_DIR / "linux" / "squashfs-root",
        TEMPLATES_DIR / "squashfs-root",
    ]

    for candidate in candidates:
        love_binary = candidate / "bin" / "love"
        app_run = candidate / "AppRun"
        if love_binary.is_file() and (app_run.exists() or app_run.is_symlink()):
            root = candidate.resolve()
            missing = [name for name in LINUX_RUNTIME_LIBRARIES if not (root / "lib" / name).is_file()]
            if not (root / "license.txt").is_file():
                missing.append("license.txt")
            if missing:
                raise FileNotFoundError(
                    "Linux runtime is incomplete. Restore the entire extracted LÖVE "
                    f"11.5 AppImage, including: {', '.join(missing)}"
                )
            for binary in [love_binary, *(root / "lib").glob("*.so*")]:
                with binary.open("rb") as source:
                    header = source.read(20)
                # ELF class 2 means 64-bit, data 1 means little-endian, and
                # machine code 62 (at offset 18) is AMD64/x86_64.
                if (
                    len(header) < 20 or header[:6] != b"\x7fELF\x02\x01"
                    or struct.unpack_from("<H", header, 18)[0] != 62
                ):
                    raise ValueError(f"Invalid or non-x86_64 Linux runtime: {binary.name}")
            return root

    raise FileNotFoundError(
        "Linux template not found. Extract a compatible LÖVE AppImage once and "
        "place its squashfs-root folder at templates/linux/squashfs-root."
    )


def _png_to_ico(icon_png: Path, ico_file: Path) -> None:
    """Convert a PNG image to a multi-resolution Windows icon using Pillow."""
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required for icon conversion. Install requirements.txt first."
        ) from error

    with Image.open(icon_png) as image:
        # ICO needs square images, even when the chosen PNG is rectangular or tiny.
        from PIL import ImageOps
        converted = ImageOps.pad(image.convert("RGBA"), (256, 256), color=(0, 0, 0, 0))
        converted.save(
            ico_file, format="ICO",
            sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)],
        )


# macOS bundle helpers -------------------------------------------------------

def _write_macos_zip(
    *,
    template_archive: Path,
    template_prefix: str,
    output_archive: Path,
    love_archive: Path,
    game_name: str,
    icon_data: bytes | None,
    developer_name: str = "",
    documents: dict[str, bytes] | None = None,
) -> None:
    """Clone a macOS template ZIP while preserving POSIX metadata and links."""
    destination_prefix = f"{game_name}.app/"
    info_plist_written = False

    with zipfile.ZipFile(template_archive, "r") as source_zip, zipfile.ZipFile(
        output_archive,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as output_zip:
        template_plist = plistlib.loads(source_zip.read(f"{template_prefix}Contents/Info.plist"))
        original_executable = template_plist.get("CFBundleExecutable", "love")
        if not isinstance(original_executable, str) or "/" in original_executable or "\\" in original_executable:
            raise ValueError("Invalid CFBundleExecutable in the macOS template.")
        original_binary = f"Contents/MacOS/{original_executable}"
        if f"{template_prefix}{original_binary}" not in source_zip.namelist():
            raise FileNotFoundError("The macOS template has no bundle executable.")
        for source_info in source_zip.infolist():
            if not source_info.filename.startswith(template_prefix):
                continue

            relative_name = source_info.filename.removeprefix(template_prefix)
            if relative_name.removeprefix("Contents/Resources/") in (documents or {}):
                # Replace our generated documents, rather than create duplicate ZIP entries.
                continue
            if relative_name.startswith("Contents/Resources/") and relative_name.endswith(".love"):
                continue
            # Bundle-level signatures are invalid once Info.plist/resources
            # change. Framework-local signatures remain unchanged.
            if relative_name == "Contents/_CodeSignature" or relative_name.startswith(
                "Contents/_CodeSignature/"
            ):
                continue

            target_info = copy(source_info)
            target_info.filename = f"{destination_prefix}{relative_name}"
            target_info.orig_filename = target_info.filename
            target_info.create_system = 3
            if source_info.is_dir():
                target_info.external_attr = (stat.S_IFDIR | 0o755) << 16 | 0x10
            elif not stat.S_ISLNK(source_info.external_attr >> 16):
                mode = stat.S_IMODE(source_info.external_attr >> 16) or 0o644
                target_info.external_attr = (stat.S_IFREG | mode) << 16

            if relative_name == "Contents/Info.plist":
                contents = _renamed_plist(source_zip.read(source_info), game_name, developer_name)
                info_plist_written = True
            elif relative_name == "Contents/Resources/OS X AppIcon.icns" and icon_data:
                contents = icon_data
            else:
                contents = source_zip.read(source_info)

            # Windows cannot retain chmod bits while extracting/copying an
            # .app. Explicitly write the main LÖVE executable as 0755.
            if relative_name == original_binary:
                target_info.filename = f"{destination_prefix}Contents/MacOS/{game_name}"
                target_info.orig_filename = target_info.filename
                target_info.create_system = 3  # Unix
                target_info.external_attr = (stat.S_IFREG | 0o755) << 16

            output_zip.writestr(target_info, contents)

        if not info_plist_written:
            raise FileNotFoundError("The macOS template ZIP has no Info.plist.")

        game_info = zipfile.ZipInfo(
            f"{destination_prefix}Contents/Resources/game.love"
        )
        game_info.create_system = 3  # Unix
        game_info.external_attr = (stat.S_IFREG | 0o644) << 16
        game_info.compress_type = zipfile.ZIP_DEFLATED
        output_zip.writestr(game_info, love_archive.read_bytes())
        # Notes outside the .app are easy to read after unzipping. The same files
        # inside Resources travel with the app if the user copies only the .app.
        for relative_name, data in (documents or {}).items():
            _write_unix_zip_file(output_zip, relative_name, data, 0o644)
            _write_unix_zip_file(output_zip, f"{destination_prefix}Contents/Resources/{relative_name}", data, 0o644)
        readme = (
            f"{game_name} - macOS\nDeveloper / Studio: {developer_name or 'Not specified'}\n\n"
            "FOR PLAYERS\n"
            "1. Extract this ZIP ON A MAC with Archive Utility (or ditto -x -k).\n"
            f"2. Open {game_name}.app. The LÖVE runtime is already included.\n"
            "Do not unpack/re-zip the app on Windows: framework links and execute\n"
            "permissions must be preserved. Keep the whole .app together.\n\n"
            "CREDITS AND TERMS\n"
            "Read CREDITS.txt and licenses/README.txt beside the app. Copies also\n"
            "live in the app's Contents/Resources/ folder (Show Package Contents).\n"
            "The original runtime/framework license notices are retained too.\n\n"
            "FOR THE DEVELOPER - SIGNING\n"
            "This build is unsigned and unnotarized. macOS may block downloads.\n"
            "Prepare-macOS.command can ad-hoc sign for local testing on a Mac.\n"
            "It is NOT Developer ID signing or notarization; it does not disable\n"
            "Gatekeeper. Public releases need the appropriate signing/notarization\n"
            "workflow on a Mac before making the final distribution ZIP.\n"
            "The Developer / Studio field is credit text, NOT a signing certificate.\n"
            "Signing help: https://developer.apple.com/developer-id/\n"
        )
        _write_unix_zip_file(output_zip, "README-macOS.txt", readme.encode("utf-8"), 0o644)
        script = (
            "#!/bin/sh\nset -eu\n"
            "# Local testing helper. A studio name in Info.plist is not a certificate.\n"
            "# Do not use ad-hoc signing as a substitute for a public release workflow.\n"
            'base=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)\n'
            f"app_name={shlex.quote(game_name + '.app')}\n"
            'app="$base/$app_name"\n'
            'echo "Ad-hoc signing for LOCAL TESTING, not public notarized distribution."\n'
            '/usr/bin/codesign --force --deep --sign - "$app"\n'
            '/usr/bin/codesign --verify --deep --strict --verbose=2 "$app"\n'
            'echo "Local signature verified. Public releases still need Developer ID and notarization."\n'
        )
        _write_unix_zip_file(output_zip, "Prepare-macOS.command", script.encode("utf-8"), 0o755)


def _write_unix_zip_file(archive: zipfile.ZipFile, name: str, data: bytes, mode: int) -> None:
    entry = zipfile.ZipInfo(name)
    entry.create_system = 3
    entry.external_attr = (stat.S_IFREG | mode) << 16
    entry.compress_type = zipfile.ZIP_DEFLATED
    archive.writestr(entry, data)


def _validate_macos_package(path: Path, love_archive: Path, game_name: str) -> None:
    """Check the bundle's entry point, game, frameworks, modes and link targets."""
    prefix = f"{game_name}.app/"
    with zipfile.ZipFile(path) as archive:
        entries = {entry.filename.rstrip("/"): entry for entry in archive.infolist()}
        if len(entries) != len(archive.infolist()):
            raise ValueError("The macOS package contains duplicate entries.")
        bundle_info = plistlib.loads(archive.read(f"{prefix}Contents/Info.plist"))
        binary = entries[f"{prefix}Contents/MacOS/{bundle_info['CFBundleExecutable']}"]
        if not (binary.external_attr >> 16) & 0o111:
            raise ValueError("The macOS executable has no Unix execute permission.")
        magic = archive.read(binary)[:4]
        if magic not in {b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"}:
            raise ValueError("The macOS bundle has no valid native executable.")
        if archive.read(f"{prefix}Contents/Resources/game.love") != love_archive.read_bytes():
            raise ValueError("The macOS bundle lost its game archive.")
        for framework in (
            "love", "Lua", "SDL2", "OpenAL-Soft", "freetype", "libmodplug",
            "mpg123", "ogg", "vorbis", "theora",
        ):
            if f"{prefix}Contents/Frameworks/{framework}.framework/{framework}" not in entries:
                raise ValueError(f"The macOS runtime is missing {framework}.framework.")
        for name, entry in entries.items():
            if not stat.S_ISLNK(entry.external_attr >> 16):
                continue
            target = name
            visited: set[str] = set()
            # Framework links can point THROUGH other links: Resources ->
            # Versions/Current/Resources, then Current -> A. Resolve that chain
            # in ZIP member names, not by extracting broken Windows symlinks.
            while True:
                if target in visited or not target.startswith(prefix):
                    raise ValueError(f"Unsafe or cyclic macOS framework link: {name}")
                visited.add(target)
                parts = target.split("/")
                for index in range(1, len(parts) + 1):
                    component = "/".join(parts[:index])
                    link = entries.get(component)
                    if link is not None and stat.S_ISLNK(link.external_attr >> 16):
                        link_value = archive.read(link).decode("utf-8")
                        target = posixpath.normpath(posixpath.join(
                            posixpath.dirname(component), link_value, *parts[index:],
                        ))
                        break
                else:
                    if target not in entries:
                        raise ValueError(f"Broken macOS framework link: {name} -> {target}")
                    break
        if archive.testzip() is not None:
            raise ValueError("The macOS ZIP package failed its integrity check.")


def _renamed_plist(contents: bytes, game_name: str, developer_name: str = "") -> bytes:
    """Safely set macOS display metadata while preserving XML or binary format."""
    bundle_info = plistlib.loads(contents)
    if not isinstance(bundle_info, dict):
        raise ValueError("The macOS template Info.plist does not contain a dictionary.")

    bundle_info["CFBundleName"] = game_name
    bundle_info["CFBundleDisplayName"] = game_name
    bundle_info["CFBundleExecutable"] = game_name
    bundle_info["CFBundleIdentifier"] = _mac_bundle_identifier(game_name, developer_name)
    bundle_info["LoveExporterDeveloper"] = developer_name
    if developer_name:
        # Informational credit only: don't claim that a developer name owns the
        # engine copyright or silently impose a copyright/license on the game.
        bundle_info["NSHumanReadableCopyright"] = (
            f"Developer: {developer_name}. Game and third-party license notices "
            "are included in Contents/Resources/licenses."
        )
    # This declaration belongs to the base LÖVE app, not the exported game.
    # Removing it avoids advertising LÖVE's document type under every game.
    bundle_info.pop("UTExportedTypeDeclarations", None)
    bundle_info.pop("CFBundleDocumentTypes", None)
    plist_format = plistlib.FMT_BINARY if contents.startswith(b"bplist") else plistlib.FMT_XML
    return plistlib.dumps(bundle_info, fmt=plist_format, sort_keys=False)


def _mac_bundle_identifier(game_name: str, developer_name: str = "") -> str:
    """Create an ASCII bundle ID; a supplied studio separates same-name games."""
    game_slug = _bundle_id_component(game_name)
    studio_slug = f"{_bundle_id_component(developer_name)}." if developer_name else ""
    return f"org.loveexporter.{studio_slug}{game_slug}"


def _bundle_id_component(name: str) -> str:
    """Keep identifiers stable, including when the credit contains Unicode."""
    slug = "".join(
        character.lower() if character.isascii() and character.isalnum() else "-"
        for character in name
    ).strip("-")
    if not slug or not name.isascii():
        slug = f"{slug or 'game'}-{uuid.uuid5(uuid.NAMESPACE_DNS, name).hex[:10]}"
    return slug


def _create_mac_icns(icon_png: Path, output_file: Path) -> bool:
    """Create an ICNS icon, including a Pillow fallback on Windows."""
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required for icon conversion. Install requirements.txt first."
        ) from error

    iconutil = shutil.which("iconutil")
    if iconutil is None:
        try:
            with Image.open(icon_png) as image:
                from PIL import ImageOps
                source = ImageOps.pad(image.convert("RGBA"), (1024, 1024), color=(0, 0, 0, 0))
                source.save(
                    output_file,
                    format="ICNS",
                    sizes=[
                        (16, 16),
                        (32, 32),
                        (64, 64),
                        (128, 128),
                        (256, 256),
                        (512, 512),
                        (1024, 1024),
                    ],
                )
            return output_file.is_file()
        except (OSError, ValueError) as error:
            warnings.warn(
                "Could not create a macOS .icns icon with Pillow; the macOS "
                f"build will use the template icon. ({error})",
                RuntimeWarning,
                stacklevel=2,
            )
            return False

    icon_sizes = {
        "icon_16x16.png": 16,
        "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32,
        "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128,
        "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256,
        "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512,
        "icon_512x512@2x.png": 1024,
    }
    iconset = output_file.parent / "AppIcon.iconset"
    iconset.mkdir()
    with Image.open(icon_png) as image:
        source = image.convert("RGBA")
        for filename, size in icon_sizes.items():
            source.resize((size, size), Image.Resampling.LANCZOS).save(
                iconset / filename,
                format="PNG",
            )

    subprocess.run(
        [iconutil, "-c", "icns", str(iconset), "-o", str(output_file)],
        check=True,
    )
    return True


# Linux bundle / launcher helpers -------------------------------------------

def _fuse_linux_binary(
    love_binary: Path,
    game_binary: Path,
    love_archive: Path,
) -> None:
    """Create a named fused game binary from the staged LÖVE executable."""
    if not love_binary.is_file():
        raise FileNotFoundError(f"Linux LÖVE executable was not found: {love_binary}")

    original_mode = stat.S_IMODE(love_binary.stat().st_mode)
    temporary_binary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=game_binary.parent,
            prefix=".fused-love-",
            delete=False,
        ) as temporary_file:
            temporary_binary = Path(temporary_file.name)
            with love_binary.open("rb") as source, love_archive.open("rb") as archive:
                shutil.copyfileobj(source, temporary_file, length=1024 * 1024)
                shutil.copyfileobj(archive, temporary_file, length=1024 * 1024)

        os.replace(temporary_binary, game_binary)
        temporary_binary = None
        try:
            game_binary.chmod(
                original_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
        except OSError:
            # The .tar.gz metadata below supplies the Unix execute bits when
            # this code is running from a Windows filesystem.
            pass
    finally:
        if temporary_binary is not None:
            temporary_binary.unlink(missing_ok=True)


def _linux_binary_name(game_name: str) -> str:
    """Return a shell-safe file name for the Linux game executable."""
    safe = "".join(
        character if character.isalnum() or character in "._-" else "-"
        for character in game_name
    ).strip(".-")
    return safe or "love-game"


def _configure_linux_launcher(
    staged_root: Path,
    game_binary_name: str,
    display_name: str,
) -> None:
    """Point AppRun and the desktop entry at the named fused game binary."""
    launcher = staged_root / "AppRun"
    if not launcher.is_file():
        raise FileNotFoundError(f"Linux AppRun launcher was not found: {launcher}")

    # Do not inherit an unrelated APPDIR/FUSE_PATH from the exporting user's
    # environment. Always locate OUR bundled runtime, even when called from /tmp.
    contents = (
        "#!/bin/sh\nset -eu\n"
        'APPDIR=$(CDPATH= cd -- "$(dirname -- "$(readlink -f -- "$0")")" && pwd -P)\n'
        'export APPDIR\n'
        'export LD_LIBRARY_PATH="$APPDIR/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"\n'
        'export XDG_DATA_DIRS="$APPDIR/share:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}"\n'
        'LUA_PATH="$APPDIR/share/lua/5.1/?.lua;$APPDIR/share/lua/5.1/?/init.lua;${LUA_PATH:-;}"\n'
        'for directory in "$APPDIR"/share/luajit-*; do\n'
        '    [ ! -d "$directory" ] || LUA_PATH="$directory/?.lua;$LUA_PATH"\n'
        'done\n'
        'export LUA_PATH\n'
        'export LUA_CPATH="$APPDIR/lib/lua/5.1/?.so;${LUA_CPATH:-;}"\n'
        'cd -- "$APPDIR"\n'
        f'exec "$APPDIR/bin/{game_binary_name}" "$@"\n'
    )
    # Replace a symlinked launcher rather than overwriting its target file.
    launcher.unlink()
    launcher.write_text(contents, encoding="utf-8", newline="\n")
    launcher.chmod(0o755)

    desktop_file = staged_root / "love.desktop"
    if desktop_file.is_file():
        desktop = desktop_file.read_text(encoding="utf-8", errors="replace")
        desktop = _replace_desktop_field(desktop, "Name", display_name)
        # %k expands to the desktop file's own location. Quote through both the
        # Desktop Entry and POSIX shell parsers; no hard-coded export path.
        script = 'directory=$(dirname -- "$1"); shift; exec "$directory/AppRun" "$@"'
        quoted = script.replace("\\", "\\\\\\\\").replace('"', '\\\\"').replace("$", "\\\\$")
        desktop = _replace_desktop_field(desktop, "Exec", f'sh -c "{quoted}" sh %k %F')
        desktop = _replace_desktop_field(desktop, "Terminal", "false")
        desktop = _replace_desktop_field(desktop, "Icon", "love")
        desktop_file.write_text(desktop, encoding="utf-8", newline="\n")


def _replace_desktop_field(contents: str, field: str, value: str) -> str:
    """Set a ``.desktop`` key while retaining any unrelated template fields."""
    prefix = f"{field}="
    lines = contents.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            lines[index] = f"{prefix}{value}"
            return "\n".join(lines) + "\n"
    return f"{contents.rstrip()}\n{prefix}{value}\n"


def _write_linux_readme(staged_root: Path, game_name: str, developer: str = "") -> None:
    """Explain how recipients launch the bundled Linux runtime."""
    (staged_root / "README.txt").write_text(
        f"{game_name} - Linux x86_64\nDeveloper / Studio: {developer or 'Not specified'}\n\n"
        "HOW TO PLAY\n"
        "1. Extract with a Linux archive manager or tar -xzf.\n"
        "2. Open the extracted folder and run ./AppRun. LÖVE is included.\n"
        "Keep bin/, lib/ and share/ together. AppRun sets the library paths.\n"
        "If permissions were lost, run: chmod +x AppRun bin/*\n\n"
        "COMPATIBILITY\n"
        "This is x86_64 Linux, not a Windows EXE or an ARM build. Linux supplies\n"
        "compatible glibc, graphics drivers and standard system libraries.\n\n"
        "CREDITS AND TERMS\n"
        "Keep CREDITS.txt, licenses/, and the original runtime license.txt.\n",
        encoding="utf-8",
    )


def _linux_tar_filter(member: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Normalise key Unix permissions for a Linux archive built on Windows."""
    member_name = member.name.replace("\\", "/")
    if member_name.endswith("/.DirIcon") and member.isreg() and member.size == 0:
        return None
    member.uid = member.gid = 0
    member.uname = member.gname = "root"
    if member.isdir():
        member.mode = 0o755
    elif member_name.endswith("/AppRun") or "/bin/" in member_name:
        member.mode = 0o755
    elif member.isreg():
        # Windows reports ordinary files as 0666. Distribute docs/libraries as
        # readable but owner-writable (0644), not writable by every Linux user.
        member.mode = 0o644
    return member


def _validate_linux_package(
    path: Path, love_archive: Path, package_name: str, binary_name: str,
) -> None:
    """Ensure the distributable contains the fused game AND all runtime files."""
    with tarfile.open(path, "r:gz") as archive:
        members = {entry.name: entry for entry in archive.getmembers()}
        binary = members[f"{package_name}/bin/{binary_name}"]
        launcher = members[f"{package_name}/AppRun"]
        if not binary.mode & 0o111 or not launcher.mode & 0o111:
            raise ValueError("The Linux game or AppRun is not executable in the tarball.")
        with archive.extractfile(binary) as source:
            source.seek(-love_archive.stat().st_size, os.SEEK_END)
            if source.read() != love_archive.read_bytes():
                raise ValueError("The Linux package lost its embedded game.")
        for library in LINUX_RUNTIME_LIBRARIES:
            if f"{package_name}/lib/{library}" not in members:
                raise ValueError(f"The Linux package is missing {library}.")
