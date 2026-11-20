from pathlib import Path
import sys


ROOT = Path(SPECPATH).resolve()
sys.path.insert(0, str(ROOT))

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VSVersionInfo,
    VarFileInfo,
    VarStruct,
)

from voice_randomizer import __version__


version_parts = tuple(int(part) for part in __version__.split(".")) + (0,)
version_text = ".".join(str(part) for part in version_parts[:3])
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_parts, prodvers=version_parts),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Fallout 4 Voice Randomizer"),
                        StringStruct("FileDescription", "Fallout 4 Voice Randomizer"),
                        StringStruct("FileVersion", version_text),
                        StringStruct("InternalName", "Fallout4VoiceRandomizer"),
                        StringStruct("OriginalFilename", "Fallout4VoiceRandomizer.exe"),
                        StringStruct("ProductName", "Fallout 4 Voice Randomizer"),
                        StringStruct("ProductVersion", version_text),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)

VENDOR = ROOT / "vendor" / "WMAEncodeRS"
datas = [
    (str(VENDOR / "WMAEncodeRS.exe"), "vendor/WMAEncodeRS"),
    (str(VENDOR / "LICENSE"), "vendor/WMAEncodeRS"),
    (str(VENDOR / "README.md"), "vendor/WMAEncodeRS"),
]

a = Analysis(
    [str(ROOT / "run_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Fallout4VoiceRandomizer",
    version=version_info,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Fallout4VoiceRandomizer",
)
