# -*- mode: python ; coding: utf-8 -*-
"""Configuration PyInstaller de PicturIt.

Construit l'application en incluant :
- QtWebEngine (carte Leaflet) — pris en charge par les hooks PySide6 ;
- les binaires externes ffmpeg.exe / ffprobe.exe (dossier bin/) ;
- les ressources resources/map.html et resources/app.ico (icône).

Deux modes, sélectionnés par la variable d'environnement PICTURIT_ONEFILE :

  # Mode dossier (par défaut) — lancement quasi instantané, RECOMMANDÉ :
      pyinstaller build.spec --noconfirm --clean
  # -> dist/PicturIt/PicturIt.exe

  # Mode fichier unique — un seul .exe, démarrage plus lent (décompression) :
      (PowerShell)  $env:PICTURIT_ONEFILE=1; pyinstaller build.spec --noconfirm --clean
      (bash)        PICTURIT_ONEFILE=1 pyinstaller build.spec --noconfirm --clean
  # -> dist/PicturIt.exe

⚠️ ffmpeg.exe et ffprobe.exe doivent être présents dans ./bin/ avant le build
(voir README). Les vignettes/métadonnées vidéo en dépendent.
"""

import os

from PyInstaller.utils.hooks import collect_submodules

ONEFILE = bool(os.environ.get("PICTURIT_ONEFILE"))
block_cipher = None

# Données embarquées : (source, dossier de destination dans le bundle).
datas = [
    ("resources/map.html", "resources"),
    ("resources/app.ico", "resources"),  # icône (window icon au runtime)
]

# Binaires externes ffmpeg/ffprobe (ajoutés seulement s'ils existent).
binaries = []
for _name in ("ffmpeg.exe", "ffprobe.exe"):
    _path = os.path.join("bin", _name)
    if os.path.exists(_path):
        binaries.append((_path, "bin"))

# pillow-heif charge des modules natifs : on s'assure de tout embarquer.
hiddenimports = collect_submodules("pillow_heif")

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# --- Allègement du bundle -----------------------------------------------------
# Retire les ressources de debug / DevTools Chromium et les traductions Qt
# (l'app a ses propres libellés français ; les dialogues Qt retombent en anglais).
_EXCLUDE_DATA_SUBSTR = ("qtwebengine_devtools",)
_EXCLUDE_DATA_SUFFIX = (".debug.pak", ".debug.bin")


def _keep_data(entry):
    dest = entry[0].replace("\\", "/").lower()
    if any(sub in dest for sub in _EXCLUDE_DATA_SUBSTR):
        return False
    if dest.endswith(_EXCLUDE_DATA_SUFFIX):
        return False
    if "/translations/" in dest and dest.endswith(".qm"):
        return False  # traductions Qt (.qm) inutiles
    # Locales WebEngine (Chromium) : ne garder que l'anglais et le français.
    if "qtwebengine_locales/" in dest:
        name = dest.rsplit("/", 1)[-1]
        if name not in ("en-us.pak", "fr.pak"):
            return False
    return True


a.datas = [e for e in a.datas if _keep_data(e)]

# Modules Qt jamais utilisés par l'application (ni par QtWebEngine en mode widget).
_EXCLUDE_BIN_PREFIX = (
    "qt6pdf", "qt63d", "qt6graphs", "qt6charts", "qt6datavisualization",
    "qt6sensors", "qt6nfc", "qt6bluetooth", "qt6serialport", "qt6test",
    "qt6designer", "qt6help", "qt6sql", "qt6spatialaudio", "qt6texttospeech",
    "qt6scxml", "qt6remoteobjects", "qt6websockets", "qt6quick3d",
    "qt6quicktimeline",
)


def _keep_bin(entry):
    name = os.path.basename(entry[0]).lower()
    return not any(name.startswith(p) for p in _EXCLUDE_BIN_PREFIX)


a.binaries = [e for e in a.binaries if _keep_bin(e)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

if ONEFILE:
    # Un seul exécutable : tout est empaqueté dans l'EXE.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.zipfiles,
        a.datas,
        [],
        name="PicturIt",
        icon="resources/app.ico",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        runtime_tmpdir=None,
        console=False,  # application fenêtrée (pas de console)
    )
else:
    # Mode dossier : EXE léger + dépendances à côté (COLLECT).
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="PicturIt",
        icon="resources/app.ico",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.zipfiles,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="PicturIt",
    )
