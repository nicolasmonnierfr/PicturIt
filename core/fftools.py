"""Localisation des binaires externes ffmpeg / ffprobe.

Stratégie de recherche (cf. SPEC 6.4) :
1. bundle PyInstaller (``sys._MEIPASS/bin``) pour l'exe autonome ;
2. dossier ``bin/`` à la racine du projet (mode développement) ;
3. ``PATH`` système en dernier recours.

Si aucun binaire n'est trouvé, les fonctions renvoient None ; l'appelant doit
gérer ce cas proprement (vignette générique, métadonnées vidéo absentes).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

# Empêche l'ouverture d'une fenêtre de console à chaque appel ffmpeg sur Windows.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Racine du projet (…/Picturit), parent du dossier core/.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _candidates(name: str):
    """Génère les emplacements candidats pour un binaire donné."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        yield os.path.join(meipass, "bin", name)
    yield os.path.join(_PROJECT_ROOT, "bin", name)


def _find(name: str) -> str | None:
    for candidate in _candidates(name):
        if os.path.isfile(candidate):
            return candidate
    # Repli sur le PATH système (avec et sans extension .exe).
    base = name[:-4] if name.lower().endswith(".exe") else name
    return shutil.which(name) or shutil.which(base)


def ffmpeg_path() -> str | None:
    """Chemin de ffmpeg.exe, ou None s'il est introuvable."""
    return _find("ffmpeg.exe")


def ffprobe_path() -> str | None:
    """Chemin de ffprobe.exe, ou None s'il est introuvable."""
    return _find("ffprobe.exe")


def run(args: list[str], timeout: float = 20.0) -> subprocess.CompletedProcess | None:
    """Exécute un binaire ff* sans fenêtre console. Renvoie None en cas d'échec."""
    try:
        return subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
