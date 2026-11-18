# PyInstaller spec — build with:  pyinstaller --noconfirm FC27TacticalStudio.spec
# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

block_cipher = None

import os
from PyInstaller.utils.hooks import collect_data_files

hiddenimports = collect_submodules("av") + collect_submodules("onnxruntime") + ["sounddevice", "_sounddevice", "cv2"]
binaries = collect_dynamic_libs("av") + collect_dynamic_libs("onnxruntime")

datas = [
    ("fc27studio/resources", "fc27studio/resources"),
    ("docs/USER_GUIDE.md", "docs"),
    ("LICENSE", "."),
    ("docs/LICENSING.md", "docs"),
] + collect_data_files("onnxruntime") + collect_data_files("_sounddevice_data")

# AI player-selection model (downloaded by scripts/fetch_models.py)
if os.path.isfile("fc27studio/models/mobile_sam_image_encoder.onnx"):
    datas.append(("fc27studio/models", "fc27studio/models"))
else:
    print("WARNING: AI model not found - run scripts/fetch_models.py; building without AI selection")

# Qt modules the app does not use (keeps the bundle much smaller).
excludes = [
    "tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQml", "PySide6.Qt3DCore", "PySide6.Qt3DRender",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf",
    "PySide6.QtPdfWidgets", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtBluetooth",
    "PySide6.QtNfc", "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSql", "PySide6.QtTextToSpeech", "PySide6.QtDesigner",
    "PySide6.QtHelp", "PySide6.QtOpenGL", "PySide6.QtOpenGLWidgets", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSpatialAudio", "PySide6.QtWebChannel",
    "PySide6.QtWebSockets", "PySide6.QtNetworkAuth", "PySide6.QtHttpServer",
]

a = Analysis(
    ["run.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + ["PySide6.QtTest"],
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FC27TacticalStudio",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon="fc27studio/resources/icon.ico",
    version="scripts/version_info.txt" if sys.platform == "win32" else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="FC27TacticalStudio",
)
