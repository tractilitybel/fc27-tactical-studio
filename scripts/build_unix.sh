#!/usr/bin/env bash
# Development build for macOS/Linux (the Windows build is scripts/build_windows.bat).
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen python -m pytest -q tests
pyinstaller --noconfirm --clean FC27TacticalStudio.spec
QT_QPA_PLATFORM=offscreen ./dist/FC27TacticalStudio/FC27TacticalStudio --selfcheck
