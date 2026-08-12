# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MegaCode.

Builds a single, windowed (no console) ``MegaCode.exe``. Heavy Qt modules that
the app never touches are excluded to keep the binary lean; the core
QtCore/QtGui/QtWidgets that the UI needs are always kept.
"""

# fmt: off
excludes = [
    # WebEngine / QML / Quick (huge, unused by QtWidgets apps)
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtQuickTest",
    # 3D
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.Qt3DLogic",
    # Multimedia / charts / data viz / pdf
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtDataVisualizationQml",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    # Hardware / location / niche
    "PySide6.QtBluetooth", "PySide6.QtNfc", "PySide6.QtSerialPort", "PySide6.QtSerialBus",
    "PySide6.QtSensors", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSpatialAudio", "PySide6.QtScxml", "PySide6.QtStateMachine",
    "PySide6.QtRemoteObjects", "PySide6.QtHelp", "PySide6.QtDesigner",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    # stdlib not needed at runtime
    "tkinter", "test", "unittest",
]
# fmt: on

# pywinpty ships compiled extensions / agent binaries that PyInstaller must bundle.
from PyInstaller.utils.hooks import collect_all as _collect_all
_datas, _binaries, _hiddenimports = [], [], []
for _pkg in ("winpty", "pyte"):
    _d, _b, _h = _collect_all(_pkg)
    _datas += _d
    _binaries += _b
    _hiddenimports += _h

a = Analysis(
    ["src/run.py"],
    pathex=["src"],
    binaries=_binaries,
    datas=_datas,
    hiddenimports=_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MegaCode",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon=None,
)
