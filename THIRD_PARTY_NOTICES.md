# Third-party notices

The root [MIT license](LICENSE) covers this exporter's original source and
documentation. It does **not** relicense bundled runtimes, tools, fonts, or
exported games. Preserve each upstream component's original notices.

## Files included in this repository

| Component | Source | Original notices |
| --- | --- | --- |
| LÖVE 11.5 Windows runtime | [Official LÖVE releases](https://github.com/love2d/love/releases/tag/11.5) | `templates/love-11.5-win64/love-11.5-win64/license.txt` |
| LÖVE 11.5 macOS runtime | Same official release; original ZIP retained for symlinks | `love.app/Contents/Resources/license.txt` and framework notices inside `templates/love-11.5-macos.zip` |
| LÖVE 11.5 Linux runtime | Complete extracted x86_64 AppImage from that release | `templates/linux/squashfs-root/license.txt` and runtime/share notices |
| rcedit | [electron/rcedit](https://github.com/electron/rcedit) | `templates/licenses/RCEDIT-LICENSE.txt` (GitHub Inc., MIT) |
| love.js notice | [Davidobot/love.js](https://github.com/Davidobot/love.js), default builder `love.js@11.4.1` | `templates/licenses/LOVEJS-LICENSE.txt` (Tanner Rogalsky, MIT) |

The runtime license bundles include LÖVE **and its libraries**, which have their
own license/copyright information. They are copied unchanged into game packages.
The original macOS ZIP is intentionally not unpacked/repacked on Windows.

`love.js` itself is fetched/cached by npm when Web export is requested; the npm
cache and Node.js installation are not published as project files. The Web build
includes the pinned package's notice and a desktop LÖVE notice bundle for reference;
review actual Web dependency notices when distributing or overriding that package.

## Dependencies bundled when building the exporter EXE

Python, Tcl/Tk, [CustomTkinter](https://github.com/TomSchimansky/CustomTkinter),
[Pillow](https://github.com/python-pillow/Pillow), darkdetect, packaging, and their
assets retain upstream licenses. PyInstaller's collection includes available
package data; the Windows build additionally gathers installed distribution
license files into `third-party/python/`. See those notices in the release ZIP
and in the app's About / License window. This list is not a dependency license audit.

Users' game licenses stay separate. The Developer / Studio field credits a game
developer; it never substitutes this exporter's creator or chooses MIT for a game.
