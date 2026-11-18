# Building and packaging

## Automatic Windows builds (GitHub Actions)

`.github/workflows/build-windows.yml` runs on every push to `main`, on pull requests, and manually
(*Actions → Windows build → Run workflow*). On a `windows-latest` runner it:

1. installs Python 3.12 and the dependencies,
2. runs the full test suite,
3. builds the app with PyInstaller,
4. starts the packaged `.exe` with `--selfcheck` (decodes a video, renders drawings, creates the main window),
5. uploads `FC27TacticalStudio-windows-x64.zip` as a build artifact (kept 30 days).

Pushing a tag like `v1.0.0` also attaches the zip to a GitHub Release.

## Building on your own Windows PC

Requirements: Windows 10 or 11 (64-bit), **64-bit Python 3.11 or 3.12** from python.org
(tick "Add python.exe to PATH" during installation).

```bat
scripts\build_windows.bat
```

The script creates `.venv`, installs dependencies, runs the tests, builds, and verifies the result. Output:

```
dist\FC27TacticalStudio\FC27TacticalStudio.exe   <- run this
dist\FC27TacticalStudio\_internal\               <- Qt, FFmpeg and Python runtime (keep next to the exe)
```

Copy the whole `dist\FC27TacticalStudio` folder to install or share it. A single-file installer is planned
for Milestone 6.

### Manual steps (equivalent)

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
set QT_QPA_PLATFORM=offscreen && python -m pytest -q tests && set QT_QPA_PLATFORM=
pyinstaller --noconfirm --clean FC27TacticalStudio.spec
dist\FC27TacticalStudio\FC27TacticalStudio.exe --selfcheck --selfcheck-log selfcheck.txt
type selfcheck.txt
```

## macOS / Linux (development)

```bash
scripts/build_unix.sh
```

The app runs on macOS and Linux too, but Windows is the target platform; the live-capture features
planned for Milestone 5 are Windows-only.

## Notes

- **Windows SmartScreen** may warn the first time because the executable is not code-signed
  (*More info → Run anyway*). Code signing needs a certificate and is planned for Milestone 6.
- **Antivirus false positives** are occasionally reported for PyInstaller apps. The one-folder build used
  here (no self-extracting single file, no UPX compression) keeps that to a minimum.
- The download (zip) is about 90 MB; most of it is Qt and FFmpeg.
