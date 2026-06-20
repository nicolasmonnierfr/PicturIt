"""Édition d'images : rotation, recadrage, conversion (photos uniquement).

⚠️ Ces opérations **écrivent sur le disque** ; ce sont des actions explicites
de l'utilisateur (cohérent avec « opérations de tri explicites », SPEC 2). Elles
sont journalisées par l'appelant. Aucune modification de vidéo (hors périmètre).

- **Rotation** : sans perte pour le JPEG (mise à jour du tag EXIF *Orientation*,
  aucun ré-encodage) ; ré-encodage pixel pour les autres formats.
- **Recadrage** : recadrage pixel (boîte en coordonnées image), EXIF date/GPS
  préservé pour le JPEG.
- **Conversion** : vers JPEG / PNG / GIF / BMP.

Toutes les fonctions lèvent en cas d'échec (l'appelant gère le message).
"""

from __future__ import annotations

import os

import piexif
from PIL import Image, ImageOps

# Formats cibles autorisés pour la conversion (cf. décision utilisateur).
CONVERT_FORMATS = {
    ".jpg": "JPEG",
    ".png": "PNG",
    ".gif": "GIF",
    ".bmp": "BMP",
}

_JPEG_EXT = (".jpg", ".jpeg")

# Transitions du tag EXIF Orientation pour une rotation de 90° (1..8).
_ROTATE_CW = {1: 6, 2: 7, 3: 8, 4: 5, 5: 2, 6: 3, 7: 4, 8: 1}
_ROTATE_CCW = {v: k for k, v in _ROTATE_CW.items()}


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _save(img: Image.Image, path: str, exif_bytes: bytes | None = None) -> None:
    """Enregistre *img* dans *path* selon l'extension cible."""
    ext = _ext(path)
    if ext in _JPEG_EXT:
        params = {"quality": 95}
        if exif_bytes:
            params["exif"] = exif_bytes
        img.convert("RGB").save(path, "JPEG", **params)
    elif ext == ".png":
        img.save(path, "PNG")
    elif ext == ".bmp":
        img.convert("RGB").save(path, "BMP")
    elif ext == ".gif":
        img.convert("P", palette=Image.Palette.ADAPTIVE).save(path, "GIF")
    else:
        img.save(path)


def _jpeg_exif_upright(src: str) -> bytes | None:
    """Renvoie les EXIF de *src* avec Orientation=1 et sans vignette (ou None)."""
    try:
        exif = piexif.load(src)
        exif["0th"][piexif.ImageIFD.Orientation] = 1
        exif["1st"] = {}
        exif["thumbnail"] = None
        return piexif.dump(exif)
    except Exception:  # noqa: BLE001
        return None


def rotate(path: str, clockwise: bool) -> None:
    """Pivote l'image de 90° (horaire si *clockwise*), en place.

    JPEG : sans perte via le tag EXIF Orientation. Autres formats : rotation
    pixel avec ré-encodage.
    """
    if _ext(path) in _JPEG_EXT:
        try:
            exif = piexif.load(path)
            current = exif["0th"].get(piexif.ImageIFD.Orientation, 1)
            mapping = _ROTATE_CW if clockwise else _ROTATE_CCW
            exif["0th"][piexif.ImageIFD.Orientation] = mapping.get(current, 1)
            exif["1st"] = {}
            exif["thumbnail"] = None
            piexif.insert(piexif.dump(exif), path)
            return
        except Exception:  # noqa: BLE001 — repli sur la rotation pixel
            pass

    # Décodage complet avant fermeture, car on réécrit le même chemin.
    with Image.open(path) as opened:
        img = ImageOps.exif_transpose(opened).rotate(
            -90 if clockwise else 90, expand=True
        )
        img.load()
    _save(img, path)


def crop(path: str, box: tuple[int, int, int, int]) -> None:
    """Recadre l'image sur *box* = (gauche, haut, droite, bas), en place."""
    exif_bytes = _jpeg_exif_upright(path) if _ext(path) in _JPEG_EXT else None
    with Image.open(path) as opened:
        img = ImageOps.exif_transpose(opened)
        width, height = img.size
        left = max(0, min(box[0], width - 1))
        top = max(0, min(box[1], height - 1))
        right = max(left + 1, min(box[2], width))
        bottom = max(top + 1, min(box[3], height))
        cropped = img.crop((left, top, right, bottom))
        cropped.load()
    _save(cropped, path, exif_bytes)


def convert(src: str, target_ext: str) -> str:
    """Convertit *src* vers *target_ext* (.jpg/.png/.gif/.bmp).

    Renvoie le chemin de destination (même base, nouvelle extension). Ne supprime
    pas la source : la stratégie (remplacer/copier) est gérée par l'appelant.
    """
    target_ext = target_ext.lower()
    if target_ext not in CONVERT_FORMATS:
        raise ValueError(f"Format non supporté : {target_ext}")
    dst = os.path.splitext(src)[0] + target_ext
    with Image.open(src) as opened:
        img = ImageOps.exif_transpose(opened).copy()
    _save(img, dst)
    return dst
