# Contributing

Created by Musa Berk Gümüş / Interaksiyon. Contributions to the original exporter
source/documentation are covered by the repository's MIT license.

## Local setup

Use full Python 3.10+ with Tcl/Tk; CI uses Windows and Python 3.12.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python gui.py
python -m unittest discover -s tests -v
```

Use Windows for rcedit/native Windows runtime tests. Hidden GUI tests check three
window sizes and that results unlock the UI after failure. Without graphical
Tcl/Tk they skip; that is not full GUI verification. Mac archive tests are not
equivalent to running the app on a real Mac.

## Keep these invariants

- Read Tk widgets only on the main thread. Pass a frozen request to the worker;
  enqueue callbacks for the main thread's `after()` loop.
- Edit Windows resources **before** attaching the game. rcedit can remove appended
  `.love` data. Real launch tests must continue to pass.
- Preserve macOS framework symlinks and UNIX permissions in the canonical ZIP.
- Package the whole Linux runtime, not only the fused ELF executable.
- Keep game, exporter, and third-party licenses separate. Copy original terms
  unchanged; a studio credit is not a copyright/license grant.
- Publish checked staged outputs; preserve a previous good build on failure.
  An old output is not a successful new build.

## Release checklist

1. Update `APP_VERSION` in `branding.py`, both versions in
   `assets/windows-version-info.txt`, and `CHANGELOG.md`.
2. Run the entire suite with full Python/Tk on Windows.
3. Close any running exporter EXE, then run `python scripts/build_app.py`.
4. Verify the EXE opens and the release ZIP contains app, `LICENSE`, and
   `third-party/` notices. Verify the SHA256 file.
5. Commit source, tag the matching version, and attach the ZIP plus `.sha256`
   to a GitHub Release. Do not commit `dist/` or `build/`.
6. Test representative games on their actual target systems before promising
   compatibility. Public macOS distribution still needs signing/notarization.

Never commit credentials, private projects, npm caches, or local test outputs.
Inspect the staged file list before any public push.
