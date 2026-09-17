"""Fixtures partagées du harnais de tests.

Les tests portent sur ``core/`` (logique métier), qui n'importe pas Qt — à la
seule exception de ``core/thumbnails.py``, volontairement laissé de côté ici.
Aucun test ne touche donc à QApplication ni à QtWebEngine.

Toutes les écritures se font dans le ``tmp_path`` de pytest : rien n'est écrit
dans le dépôt ni dans le dossier de l'utilisateur, et la corbeille Windows est
systématiquement simulée (cf. la fixture ``fake_trash``).
"""

from __future__ import annotations

import os
from datetime import datetime

import piexif
import pytest
from PIL import Image

from core import metadata, operations


# --- Isolation du cache mémoire de métadonnées ---
@pytest.fixture(autouse=True)
def _clear_metadata_cache():
    """Vide le cache de ``core.metadata`` autour de chaque test.

    Le cache est un état global de module ; sans ce nettoyage, un fichier
    recréé au même chemin dans un autre test pourrait renvoyer un résultat
    périmé (la clé inclut mtime/taille, mais l'isolation reste préférable).
    """
    metadata.clear_cache()
    yield
    metadata.clear_cache()


# --- Construction d'images de test ---
def _dms_rational(value: float) -> tuple:
    """Convertit un degré décimal en triplet DMS rationnel (format EXIF).

    Les secondes sont exprimées au 1/10000 pour garder une précision très
    inférieure au mètre (les seuils « similaires » se jouent à 50 m).
    """
    value = abs(value)
    degrees = int(value)
    minutes_float = (value - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    return ((degrees, 1), (minutes, 1), (int(round(seconds * 10000)), 10000))


def write_photo(
    path,
    *,
    dt: datetime | None = None,
    gps: tuple[float, float] | None = None,
    size: tuple[int, int] = (64, 48),
    color: tuple[int, int, int] = (120, 60, 30),
) -> str:
    """Écrit un JPEG de test, éventuellement daté et géolocalisé.

    Renvoie le chemin écrit (pratique pour chaîner dans un test).
    """
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)

    if dt is None and gps is None:
        return path

    exif: dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    if dt is not None:
        stamp = dt.strftime("%Y:%m:%d %H:%M:%S")
        exif["Exif"][piexif.ExifIFD.DateTimeOriginal] = stamp
        exif["0th"][piexif.ImageIFD.DateTime] = stamp
    if gps is not None:
        lat, lon = gps
        exif["GPS"][piexif.GPSIFD.GPSLatitudeRef] = "N" if lat >= 0 else "S"
        exif["GPS"][piexif.GPSIFD.GPSLatitude] = _dms_rational(lat)
        exif["GPS"][piexif.GPSIFD.GPSLongitudeRef] = "E" if lon >= 0 else "W"
        exif["GPS"][piexif.GPSIFD.GPSLongitude] = _dms_rational(lon)
    piexif.insert(piexif.dump(exif), path)
    return path


def pad_to(path, target_size: int) -> None:
    """Complète un JPEG jusqu'à *target_size* octets exactement.

    Les octets ajoutés après le marqueur de fin d'image sont ignorés par les
    décodeurs : l'image reste lisible, seule sa taille sur disque change. Sert
    à fabriquer des fichiers de même taille mais de contenu différent (cas
    « doublon identique » par taille + date, cf. ARCHITECTURE §6.4).
    """
    current = os.path.getsize(path)
    if current > target_size:
        raise ValueError(
            f"{path} fait déjà {current} octets (> {target_size} demandés)"
        )
    with open(path, "ab") as fh:
        fh.write(b"\x00" * (target_size - current))


@pytest.fixture
def photo_factory(tmp_path):
    """Fabrique de photos de test dans le ``tmp_path`` du test courant."""

    def _make(name: str, **kwargs) -> str:
        return write_photo(tmp_path / name, **kwargs)

    return _make


# --- Corbeille simulée ---
@pytest.fixture
def fake_trash(monkeypatch):
    """Remplace send2trash par une suppression réelle mais tracée.

    Indispensable : les tests ne doivent jamais remplir la corbeille Windows
    de l'utilisateur. La fixture renvoie la liste des chemins « mis à la
    corbeille », dans l'ordre des appels.
    """
    trashed: list[str] = []

    def _fake(path: str) -> None:
        trashed.append(path)
        os.remove(path)

    monkeypatch.setattr(operations, "send2trash", _fake)
    return trashed
