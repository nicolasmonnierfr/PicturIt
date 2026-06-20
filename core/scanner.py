"""Scan récursif d'un dossier source et détection des formats média.

Parcourt récursivement l'arborescence et renvoie les photos/vidéos regroupées
par sous-dossier (section). Aucune lecture EXIF ici : ce module ne fait que
lister les fichiers et reconnaître les formats par extension.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Extensions reconnues (cf. SPEC 6.1). Toujours comparées en minuscules.
PHOTO_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tiff", ".bmp", ".webp", ".gif"}
)
VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".wmv"}
)

# Libellé de la section pour les fichiers situés à la racine du dossier source.
ROOT_SECTION_LABEL = "(dossier racine)"


@dataclass(slots=True)
class MediaFile:
    """Un fichier média trouvé lors du scan."""

    path: str          # chemin absolu
    section: str       # chemin relatif du sous-dossier (libellé d'en-tête)
    is_video: bool     # True = vidéo, False = photo
    size: int          # taille en octets


def is_supported(filename: str) -> bool:
    """Renvoie True si le fichier a une extension photo ou vidéo reconnue."""
    ext = os.path.splitext(filename)[1].lower()
    return ext in PHOTO_EXTENSIONS or ext in VIDEO_EXTENSIONS


def is_video(filename: str) -> bool:
    """Renvoie True si le fichier a une extension vidéo reconnue."""
    return os.path.splitext(filename)[1].lower() in VIDEO_EXTENSIONS


def section_key(name: str) -> tuple[int, str]:
    """Clé de tri des sections : le dossier racine d'abord, puis l'ordre alpha."""
    return (0, "") if name == ROOT_SECTION_LABEL else (1, name.lower())


def scan(root: str) -> list[tuple[str, list[MediaFile]]]:
    """Scanne récursivement *root* et renvoie les médias groupés par section.

    Le résultat est une liste ordonnée de tuples ``(section, fichiers)`` :
    - les sections sont triées par chemin relatif (racine en premier) ;
    - les fichiers de chaque section sont triés par nom.

    Les erreurs d'accès (dossier illisible, fichier disparu) sont ignorées
    silencieusement pour ne pas interrompre le scan.
    """
    sections: dict[str, list[MediaFile]] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        # Tri stable de la descente pour un ordre déterministe.
        dirnames.sort(key=str.lower)

        rel = os.path.relpath(dirpath, root)
        section = ROOT_SECTION_LABEL if rel == "." else rel

        for filename in sorted(filenames, key=str.lower):
            if not is_supported(filename):
                continue
            full = os.path.join(dirpath, filename)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            sections.setdefault(section, []).append(
                MediaFile(
                    path=full,
                    section=section,
                    is_video=is_video(filename),
                    size=size,
                )
            )

    # Ordre des sections : racine d'abord, puis ordre alphabétique.
    return [(name, sections[name]) for name in sorted(sections, key=section_key)]
