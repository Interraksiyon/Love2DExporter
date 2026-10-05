# LÖVE Exporter

Created by **Musa Berk Gümüş / Interaksiyon**.

[![License: MIT](https://img.shields.io/badge/License-MIT-5de0c2.svg)](LICENSE)
[![Desktop app](https://img.shields.io/badge/Desktop-Windows_x64-29394b.svg)](https://github.com/Interraksiyon/Love2DExporter/releases)

Turn a LÖVE game folder into ready-to-share Windows, macOS, Linux, or Web
packages. Desktop packages include the LÖVE runtime; players do not have to
install it separately. Each operating system uses its own native build.

## Download and open

Get the Windows release ZIP from [Releases](https://github.com/Interraksiyon/Love2DExporter/releases).
Extract it and double-click `LoveExporter.exe`. Python is not required.
The exporter is unsigned; use releases from this repository that you trust.

Node.js/npm is needed only on the exporting computer for **Web** builds.
WinRAR is optional: without it, Windows exports use a real ZIP fallback.
Neither is needed by someone playing a finished desktop game.

## Export a game

1. On Windows, double-click **`dist/LoveExporter.exe`**.
2. Choose the **project folder** that contains exactly `main.lua` (lowercase).
3. Enter **Developer / Studio** if you want your name or studio credited.
4. Optionally select a **PNG icon** and a **text file containing your game license**.
5. Choose an **output folder outside the project**, tick platforms, and click Export.
6. Follow **Build Center**, then use **Open builds folder** to find the finished packages. Compression and external
   tools run in the background; the window remains responsive.

The input form scrolls on small screens. Platform choices and **Export builds**
stay visible below it. The teal-accented workspace separates next-run settings
from the last run's results. An error in one platform does not cancel other targets.

### Build Center: progress and finished results

- A stable row for the `.love` archive and each platform: waiting, building,
  exported, failed, or not built.
- Progress counts **completed/attempted stages**, not guessed elapsed-time percentages.
- Successful packages have **Open folder**. A leftover file from an older run
  never counts as a newly successful export.
- **View details** opens a scrollable, non-modal report. **Copy report** copies
  it only when requested. `BUILD-REPORT.txt` is saved in the release root too.
- **Preview Web** serves the most recently completed Web build on `127.0.0.1`,
  even if you edit the next run's form. **Stop preview** shuts down that server.
- If every platform fails, the panel reports failure even when the source archive
  exists. Partial success keeps working packages available and errors readable.

No completion popup covers the app. Controls are restored after success or failure.

### Developer and license fields

The developer field accepts a person's name or a studio name, including Unicode.
It is optional, one line, and at most 160 characters. It appears in:

- `CREDITS.txt`, readable build instructions, and `build-metadata.json`;
- the Windows EXE's **CompanyName** property (Details);
- macOS `Info.plist` developer information and the app's readable credit;
- the Web page's author metadata and credit/footer links.

The name is **credit text**, not a license, proof of ownership, or a macOS signing
certificate. It does not replace the original authors of LÖVE or its libraries.

For the game license, you can select a `.txt`, `.md`, `.rst`, or extensionless
text file. If left empty, the exporter looks for a conventional **root** file:
`LICENSE`, `LICENSE.txt`, `LICENSE.md`, `LICENSE.rst`, `COPYING` variants, or
`LICENCE` variants. Nested asset licenses are not treated as the whole game's
license. A selected file takes priority over auto-detection. The text is copied
**byte-for-byte**, without changing the terms or copyright notice.

If no license is supplied/found, `GAME-LICENSE.txt` explicitly says **GAME LICENSE
NOT PROVIDED**. That generated notice does not grant permissions or silently
choose MIT, GPL, or “all rights reserved.” Provide your own terms and export again.
Non-empty license text files must be no larger than 2 MiB.

## What a packaged build contains

~~~text
Game/
  [platform executable, libraries, and supporting files]
  README.txt                  How to launch and share this target
  CREDITS.txt                 Game name and Developer / Studio
  build-metadata.json         The same information for other tools
  licenses/
    README.txt                Explains which notices belong to which software
    GAME-LICENSE.txt          Your license, or an explicit missing-license notice
    LOVE-LICENSE.txt          Unmodified bundled LÖVE/dependency notices
    LOVEJS-LICENSE.txt        Web only, for the default pinned love.js package
~~~

The original template `license.txt` files and framework notices are retained in
their original locations too. **Your game's license and third-party licenses are
separate.** Keep the credits/notices with the whole distribution. The exporter
copies notices; it does not audit the licensing of your code, assets, or every
dependency. Additional required project notices remain your responsibility.

For macOS, credits and the `licenses/` folder appear both beside the app in the
ZIP and **inside `Game.app/Contents/Resources/`**. This keeps them with the app
even if someone copies only the `.app`.

## How players launch each target

| Platform | Exported package | How to play |
| --- | --- | --- |
| Windows x86_64 | `Windows/Game/Game.exe` + DLLs, and a RAR/ZIP to share | Extract the entire archive; run the EXE with all DLLs beside it. |
| macOS | `macOS/Game_mac.zip`, containing a complete `.app` and notes | Extract **on a Mac**, preserving framework symlinks and execute permissions; open the app. See the signing note below. |
| Linux x86_64 | `Linux/Game_Linux_x86_64.tar.gz` | Extract on Linux; keep `bin/`, `lib/`, and `share/` together; run `./AppRun`. |
| Web | `Web/Game_web/` | Host the entire folder using HTTP(S), or use Preview Web. Do not double-click `index.html` with `file://`. |

A Windows EXE is not a macOS/Linux executable. Linux still needs compatible
standard system libraries (including glibc) and graphics drivers. The desktop
templates use LÖVE 11.5. The default Web builder is `love.js@11.4.1`.

`Source/Game.love` is a ZIP of the **original project contents**, not a desktop
runtime package. Game code and original project license files are not rewritten.
Developer/selected-license additions are in the platform packages, not injected
into the game's Lua files or original `.love` contents.

### macOS signing is a separate step

An app packaged on Windows is **unsigned and unnotarized**. Changing a credit
field does not sign it. `Prepare-macOS.command` can ad-hoc sign the app **on a
Mac for local testing**; it does not disable Gatekeeper or produce a notarized
public release. The ZIP's `README-macOS.txt` explains player and developer steps
separately. For public distribution, use an appropriate
[Apple signing/notarization workflow](https://developer.apple.com/developer-id/).
Do not extract/re-zip the app on Windows; the original links and permissions matter.

### Optional tools

- **Node.js/npm (`npx`)** is required on the exporting computer for Web builds,
  not on players' computers. Browsers must retain the Click to start interaction
  for audio. The generated credit footer does not remove that overlay.
- **WinRAR** is needed only to create RAR. Without it, Windows gets a real ZIP
  fallback, not ZIP data renamed `.rar`.

The Web build includes an unmodified LÖVE notice bundle from the desktop template
for reference (it may mention platform components not used on Web), plus the
default love.js package's notice. Review the actual Web dependency notices if
you change the runtime or use `LOVE_EXPORTER_LOVEJS_PACKAGE` to override it.

## Working on the code

Start with these files:

| File | Responsibility / where to start |
| --- | --- |
| `gui.py` | `_build_ui()` creates widgets; `export()` validates/snapshots them; `_process_exports()` runs the worker; `_finish_export()` shows results. |
| `ui_components.py` | Color palette and reusable result rows; rendering only. |
| `export_report.py` | Pure result classification and saved/copyable reports. Counts platforms, not just source archives. |
| `branding.py` | Tool version and creator credit, separate from a game's developer. |
| `exporter.py` | `create_love_file()` then the four `export_*()` functions. Helpers below validate runtimes, edit resources, preserve permissions, and package files. |
| `build_notes.py` | `prepare_build_notes()` prepares credits/licenses without changing terms. `write_build_notes()` writes them into a staged package. |
| `build_windows.bat` / `scripts/build_app.py` | Choose full Python/Tk, collect notices, build the EXE, and create a versioned release ZIP and SHA256. |
| `tests/` | Regression tests for executable payloads, archives, worker routing, credits, and license copying. |

A **staging folder** is a temporary working copy: builds are checked there before
replacing finished output. A **fused executable** is the LÖVE binary followed by
the game's `.love` ZIP bytes; it does not compile Lua to native machine code.
Icons and Windows version properties must be edited **before** fusing because
rcedit removes appended game data. This order is covered by real runtime tests.

Tk widgets and `StringVar.get()` are main-thread-only. The worker receives an
immutable `ExportRequest`, does file/subprocess work, and queues UI callbacks.
The main thread's `after()` timer drains that queue, including completion after
failures. This keeps the interface responsive without unsafe worker widget calls.

### Run from source / rebuild the EXE

Use full Python **3.10 or newer**, with Tcl/Tk. Windows CI uses Python 3.12;
the local packaged release was also checked with full Python 3.14.

~~~powershell
python -m pip install -r requirements.txt
python gui.py
~~~

On Windows, run `build_windows.bat` or `python scripts/build_app.py`.
To choose another full Python for the batch wrapper, set `PYTHON_EXE` first.
The build includes UI assets, all desktop templates, rcedit, web notices,
creator/version properties, MIT license, and installed Python dependency notices.
Results are `dist/LoveExporter.exe`, a versioned Windows release ZIP, and a SHA256 file.
PyInstaller builds use their target OS;
Node.js and WinRAR are not bundled into the exporter.

Keep these runtime templates complete:

- `templates/love-11.5-win64/love-11.5-win64/`: official win64 EXE, every DLL,
  and original `license.txt`.
- `templates/love-11.5-macos.zip`: original official ZIP with framework links,
  not a ZIP recreated from a Windows-extracted app.
- `templates/linux/squashfs-root/`: complete extracted x86_64 AppImage; see
  `templates/linux/README.md` for its layout.
- `templates/licenses/`: notice for the default pinned love.js package.

### Verify changes

~~~powershell
python -m unittest discover -s tests -v
python tests/build_native_smoke.py
~~~

Windows tests actually run fused headless games, with custom icons/developer
metadata and no development LÖVE on PATH. Archive checks verify game payloads,
libraries, symlinks and permissions. Notes tests cover selected/auto-detected
licenses, byte-exact copying, missing licenses, and escaped Web credit text.
Mac archive checks are not a substitute for runtime testing on a Mac.
Persistent smoke builds go under `verification/native-smoke/` for target-OS tests.

## Repository and contributions

Canonical runtime templates are included so a clone has the build inputs.
Duplicate downloads/extracted macOS copies are ignored. Generated builds,
test games, editor state, secrets, and npm caches are **not** committed.
Do not upload your private game projects as exporter source.

The Windows [CI workflow](.github/workflows/windows.yml) tests the source and builds
a release ZIP artifact. See [CONTRIBUTING.md](CONTRIBUTING.md) for changes and releases.

## License and attribution

The exporter's original code/documentation is **MIT licensed** — see [LICENSE](LICENSE).
Created by Musa Berk Gümüş / Interaksiyon. The credit appears in the app footer,
About / License window, and Windows exporter file properties.

Third-party components retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Original runtime notices stay intact. **Your game is not automatically MIT licensed**:
select/provide its own terms. Enter your game's actual developer in the form,
not this tool's creator unless that is also your game's developer.
