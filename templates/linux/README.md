# Linux runtime template

This is the **exporter's runtime template**, not a game project. Do not put your
`main.lua` here. Select the game folder in the GUI instead.

## Keep the complete runtime

~~~text
squashfs-root/
  AppRun           Launcher: prepares local library/Lua paths, then starts bin/love
  bin/love         The native x86_64 LÖVE executable
  lib/             Bundled LÖVE, SDL2, LuaJIT, audio/font/compression libraries
  share/           Lua modules, icons and supporting runtime files
  license.txt      Original LÖVE/dependency notices; do not replace with game terms
  [other original AppImage assets]
~~~

`squashfs-root/` comes from an extracted official LÖVE **11.5 x86_64 AppImage**.
Do not keep only `bin/love`: the other directories are needed for distribution.
The exporter validates the expected 11.5 libraries and architecture. A different
version/layout needs corresponding validation/build changes, not just a renamed file.

## What happens during export

1. Copy this whole template into a temporary working folder (the original stays intact).
2. Append the game's `.love` ZIP to a named executable under `bin/`.
3. Write an `AppRun` that finds the bundled libraries relative to its own location.
4. Add Developer / Studio credits and a `licenses/` folder. The game's terms
   are kept separate from the original runtime `license.txt`.
5. Package the complete folder as `.tar.gz`, storing Linux execute bits for
   `AppRun` and the game binary even when exporting from Windows.

No AppImageTool or Linux process is required to **package** this extracted
template on Windows. Linux is required to **play/test** the resulting Linux build.

## Replacing the template

Extract the matching official AppImage on Linux with `--appimage-extract`, then
bring its complete `squashfs-root/` here. Preserve real libraries, relative links,
support files, and original license notices. Confirm that the chosen runtime's
glibc/system-library baseline matches the Linux distributions you intend to support;
bundling LÖVE does not bundle a Linux OS or GPU drivers.

## Player instructions

Extract `Game_Linux_x86_64.tar.gz` on Linux with `tar -xzf` or a Linux archive
manager. Open the extracted game folder and run `./AppRun`. If an archive tool
lost execute permissions, run `chmod +x AppRun bin/*`. Keep `bin/`, `lib/`,
`share/`, the credits, and all license notices together. LÖVE need not be installed.
