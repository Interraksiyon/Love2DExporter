"""Create the readable credits and license files shared by every export target.

This module only prepares documents; it never modifies game code or chooses a
license for the developer. Platform packaging stays in exporter.py, and widgets
stay in gui.py. Keeping these responsibilities separate makes the flow easier
to follow and lets the document rules be tested without opening a window.
"""

from __future__ import annotations

import json
from pathlib import Path
from collections.abc import Mapping
import zipfile


# A normal license is text and small. This limit catches accidentally selected
# game archives/videos without reading huge files on the export worker.
MAX_LICENSE_BYTES = 2 * 1024 * 1024
LICENSE_NAMES = (
    "license", "license.txt", "license.md", "license.rst",
    "copying", "copying.txt", "copying.md", "copying.rst",
    "licence", "licence.txt", "licence.md",
)


def normalize_developer_name(value: str) -> str:
    """Allow studio/person names, but keep generated notices on one text line."""
    name = value.strip()
    if len(name) > 160 or any(not character.isprintable() for character in name):
        raise ValueError("Developer / Studio must be one line, up to 160 characters.")
    return name


def validate_license_path(path: str | Path) -> Path:
    """Validate an explicitly selected license; never invent or rewrite its terms."""
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Game license file was not found: {source}")
    if source.suffix.lower() not in {"", ".txt", ".md", ".rst"}:
        raise ValueError("Choose a text license (.txt, .md, .rst, or a file named LICENSE/COPYING).")
    if not 0 < source.stat().st_size <= MAX_LICENSE_BYTES:
        raise ValueError("The game license must be non-empty and no larger than 2 MiB.")
    return source


def _game_license(love_file: Path, selected_file: Path | None) -> tuple[bytes | None, str]:
    """Prefer the selected file; otherwise find a conventional root license.

    Only root files count as the game's primary license: an assets/library
    license must not accidentally become the license for the entire game.
    Original project files remain untouched inside the .love archive.
    """
    if selected_file is not None:
        source = validate_license_path(selected_file)
        data = source.read_bytes()
        if not data.strip() or len(data) > MAX_LICENSE_BYTES:
            raise ValueError("The selected game license is empty or larger than 2 MiB.")
        return data, f"Selected file: {source.name}"

    with zipfile.ZipFile(love_file) as archive:
        root_files = {
            entry.filename.casefold(): entry
            for entry in archive.infolist()
            if not entry.is_dir() and "/" not in entry.filename
        }
        for candidate in LICENSE_NAMES:
            entry = root_files.get(candidate)
            if entry is None:
                continue
            if entry.file_size > MAX_LICENSE_BYTES:
                raise ValueError(f"Project license is larger than 2 MiB: {entry.filename}")
            data = archive.read(entry)
            if not data.strip():
                raise ValueError(f"Project license is empty: {entry.filename}")
            return data, f"Project root: {entry.filename}"
    return None, "No game license supplied"


def prepare_build_notes(
    love_file: Path,
    game_name: str,
    developer_name: str = "",
    game_license_file: Path | None = None,
    *,
    runtime_name: str = "LÖVE",
    runtime_license: bytes | None = None,
    runtime_note: str = "",
    extra_licenses: Mapping[str, bytes] | None = None,
) -> dict[str, bytes]:
    """Return package-relative filenames and their bytes, ready to copy/zip.

    Game-license bytes are copied verbatim, including any existing copyright
    notice. Developer credit is not an ownership claim, license grant, or macOS
    signing certificate. A missing game license becomes an explicit notice.
    """
    developer = normalize_developer_name(developer_name)
    display_developer = developer or "Not specified"
    game_license, license_source = _game_license(love_file, game_license_file)
    license_provided = game_license is not None
    if game_license is None:
        game_license = (
            f"GAME LICENSE NOT PROVIDED\n\nGame: {game_name}\n"
            f"Developer / Studio: {display_developer}\n\n"
            "No game license was selected or found at the project root.\n"
            "This generated notice does not grant permission or assign a license.\n"
            "Ask the developer for the game's terms. To include them in a new\n"
            "build, select a text license in the exporter or add LICENSE/COPYING\n"
            "to the project root, then export again.\n\n"
            "Bundled third-party software has its own separate license notices.\n"
        ).encode("utf-8")

    credits = (
        f"{game_name}\n\nDeveloper / Studio: {display_developer}\n"
        f"Built with: {runtime_name}\n\n"
        "Game terms: licenses/GAME-LICENSE.txt\n"
        "Runtime / third-party notices: licenses/README.txt\n\n"
        "Developer credit was supplied in the exporter. It does not change the\n"
        "ownership or license of the game or any bundled third-party component.\n"
    )
    license_index = (
        f"{game_name} - license information\n\n"
        f"Developer / Studio: {display_developer}\n\n"
        "1. GAME-LICENSE.txt\n"
        f"   Source: {license_source}\n"
        "   The selected/project license is copied without altering its terms.\n"
        "   If no license was supplied, this file is a notice, not a license grant.\n\n"
        "2. LOVE-LICENSE.txt\n"
        "   Notices for the bundled LÖVE runtime and its components. These are\n"
        "   separate from the game's license. Original template notices are\n"
        "   also retained in their normal runtime/framework locations.\n"
    )
    if runtime_note:
        license_index += f"\n   {runtime_note}\n"
    if runtime_license is None:
        runtime_license = (
            "No runtime license bundle was available in the templates.\n"
            "Review the runtime's notices before distributing this build:\n"
            "https://github.com/love2d/love\n"
        ).encode("utf-8")
    documents = {
        "CREDITS.txt": credits.encode("utf-8"),
        "licenses/GAME-LICENSE.txt": game_license,
        "licenses/LOVE-LICENSE.txt": runtime_license,
    }
    for filename, contents in (extra_licenses or {}).items():
        # Extra notices come from fixed bundled resources, never user paths.
        if Path(filename).name != filename or filename in {".", "..", "GAME-LICENSE.txt", "LOVE-LICENSE.txt", "README.txt"}:
            raise ValueError(f"Invalid additional license filename: {filename}")
        documents[f"licenses/{filename}"] = contents
        license_index += f"\nAdditional component: {filename}\n"
    license_index += (
        "\nThe exporter copies notices; it does not audit the licensing of your\n"
        "code, assets, or every dependency. Keep any additional required notices\n"
        "with your distribution. Original project files remain in the game archive.\n"
    )
    documents["licenses/README.txt"] = license_index.encode("utf-8")
    documents["build-metadata.json"] = (json.dumps({
        "game_name": game_name,
        "developer": developer,
        "runtime": runtime_name,
        "game_license_provided": license_provided,
        "game_license_source": license_source,
    }, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    return documents


def write_build_notes(destination: Path, documents: Mapping[str, bytes]) -> None:
    """Write prepared documents into a private staged build before publishing it."""
    for relative_name, contents in documents.items():
        target = destination / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents)
