"""Lecture des métadonnées : EXIF (photos) et conteneur (vidéos via ffprobe).

Expose un type ``Metadata`` homogène pour photos et vidéos :
date de prise de vue, coordonnées GPS, dimensions, taille, durée (vidéo).

Tout est tolérant aux données manquantes/corrompues : aucune exception n'est
remontée à l'appelant (beaucoup de fichiers n'ont ni date ni GPS).
"""

from __future__ import annotations

import io
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime

from PIL import Image

from core import fftools, geo, imaging, perf, scanner

# Configure Pillow (HEIC + images tronquées) : voir core/imaging.py.
_ = imaging.HEIF_SUPPORTED

# Identifiants de sous-IFD / tags EXIF utilisés (valeurs standard).
_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_DATETIME = 0x0132
_GPS_LAT_REF, _GPS_LAT = 1, 2
_GPS_LON_REF, _GPS_LON = 3, 4
# Tags pour l'EXIF étendu (appareil/objectif/expo).
_TAG_MAKE, _TAG_MODEL = 0x010F, 0x0110
_TAG_LENS = 0xA434
_TAG_ISO = 0x8827
_TAG_FNUMBER = 0x829D
_TAG_FOCAL = 0x920A
_TAG_EXPOSURE = 0x829A


@dataclass(slots=True)
class Metadata:
    """Métadonnées homogènes d'un média (champs absents = None)."""

    path: str
    is_video: bool
    size: int
    width: int | None = None
    height: int | None = None
    datetime_original: datetime | None = None
    latitude: float | None = None
    longitude: float | None = None
    duration: float | None = None  # secondes (vidéo)
    # EXIF étendu (photos).
    camera: str | None = None
    lens: str | None = None
    iso: int | None = None
    aperture: float | None = None
    focal_length: float | None = None
    exposure: float | None = None  # temps de pose en secondes

    @property
    def has_gps(self) -> bool:
        return self.latitude is not None and self.longitude is not None


# --- Cache mémoire de métadonnées (durée de session, aucune écriture disque) ---
# La lecture EXIF (photos) et surtout ffprobe (vidéos) est appelée plusieurs fois
# par fichier (worker de vignette, tri par date, aperçu). On mémoïse le résultat,
# invalidé automatiquement si le fichier change (clé = chemin + mtime + taille).
_cache: dict[tuple, Metadata] = {}
_cache_lock = threading.Lock()


def _cache_key(path: str) -> tuple | None:
    """Clé de cache incluant mtime/taille (None si le fichier est inaccessible)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (path, st.st_mtime_ns, st.st_size)


def read(path: str) -> Metadata:
    """Lit les métadonnées d'un fichier (dispatch photo/vidéo), avec cache.

    Le calcul lourd (PIL/ffprobe) est volontairement fait **hors verrou** pour
    ne pas sérialiser les workers : seule la table de cache est protégée.
    """
    key = _cache_key(path)
    if key is not None:
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None:
            return hit

    is_video = scanner.is_video(path)
    size = key[2] if key is not None else 0
    with perf.measure(
        "metadonnees: ffprobe (video)" if is_video else "metadonnees: EXIF (photo)"
    ):
        meta = _read_video(path, size) if is_video else _read_photo(path, size)

    if key is not None:
        with _cache_lock:
            _cache[key] = meta
    return meta


def read_from_header(path: str, header: bytes) -> Metadata | None:
    """Lit les métadonnées d'une photo depuis un en-tête **déjà chargé**.

    Évite une ouverture de fichier supplémentaire : l'appelant (le worker de
    vignettes) a de toute façon besoin de ces mêmes octets pour récupérer la
    vignette EXIF. Le résultat alimente le cache partagé, donc l'aperçu, le tri
    par date et la détection de doublons en profitent aussi.

    Renvoie None si l'en-tête ne suffit pas (l'appelant refait alors un
    ``read()`` classique). Le critère est la lecture des **dimensions** : Pillow
    les trouve dans les tout premiers octets de n'importe quel format, donc leur
    absence signale un en-tête inexploitable.
    """
    key = _cache_key(path)
    if key is None:
        return None
    with _cache_lock:
        hit = _cache.get(key)
    if hit is not None:
        return hit

    meta = _read_photo(path, key[2], header=header)
    if meta.width is None:
        return None  # en-tête insuffisant : l'appelant relira le fichier
    with _cache_lock:
        _cache[key] = meta
    return meta


def invalidate(path: str) -> None:
    """Oublie les entrées de cache d'un fichier (après édition sur disque)."""
    with _cache_lock:
        for k in [k for k in _cache if k[0] == path]:
            del _cache[k]


def clear_cache() -> None:
    """Vide tout le cache de métadonnées (changement de dossier source)."""
    with _cache_lock:
        _cache.clear()


def has_gps(path: str) -> bool:
    """Renvoie True si le fichier possède des coordonnées GPS (léger)."""
    return read(path).has_gps


# --- Photos ---
def _read_photo(path: str, size: int, header: bytes | None = None) -> Metadata:
    """Métadonnées d'une photo.

    *header* : premiers octets du fichier, déjà lus par l'appelant. Les
    dimensions et l'EXIF se trouvent en tête de fichier, il est donc inutile de
    rouvrir celui-ci — ce qui compte quand les photos sont sur un partage
    réseau, où chaque ouverture coûte des dizaines de millisecondes.
    """
    meta = Metadata(path=path, is_video=False, size=size)
    try:
        source = io.BytesIO(header) if header is not None else path
        with Image.open(source) as img:
            meta.width, meta.height = img.size
            exif = img.getexif()
    except Exception:  # noqa: BLE001 — fichier illisible : métadonnées vides
        return meta

    if not exif:
        return meta

    # Date de prise de vue (Exif IFD), avec repli sur DateTime (IFD0).
    try:
        exif_ifd = exif.get_ifd(_EXIF_IFD)
    except Exception:  # noqa: BLE001
        exif_ifd = {}
    dt_str = exif_ifd.get(_TAG_DATETIME_ORIGINAL) or exif.get(_TAG_DATETIME)
    meta.datetime_original = _parse_exif_datetime(dt_str)

    # EXIF étendu (appareil / objectif / exposition), tolérant aux absences.
    make = _clean_str(exif.get(_TAG_MAKE))
    model = _clean_str(exif.get(_TAG_MODEL))
    meta.camera = (f"{make} {model}".strip() or None) if (make or model) else None
    meta.lens = _clean_str(exif_ifd.get(_TAG_LENS))
    meta.iso = _to_int(exif_ifd.get(_TAG_ISO))
    meta.aperture = _to_float(exif_ifd.get(_TAG_FNUMBER))
    meta.focal_length = _to_float(exif_ifd.get(_TAG_FOCAL))
    meta.exposure = _to_float(exif_ifd.get(_TAG_EXPOSURE))

    # Coordonnées GPS.
    try:
        gps_ifd = exif.get_ifd(_GPS_IFD)
    except Exception:  # noqa: BLE001
        gps_ifd = {}
    if gps_ifd:
        lat = geo.dms_to_decimal(gps_ifd.get(_GPS_LAT), gps_ifd.get(_GPS_LAT_REF))
        lon = geo.dms_to_decimal(gps_ifd.get(_GPS_LON), gps_ifd.get(_GPS_LON_REF))
        if lat is not None and lon is not None:
            meta.latitude, meta.longitude = lat, lon

    return meta


def _clean_str(value) -> str | None:
    """Nettoie une valeur EXIF textuelle (str/bytes) ; None si vide."""
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8", "ignore")
    text = str(value).strip().strip("\x00").strip()
    return text or None


def _to_int(value) -> int | None:
    if value is None:
        return None
    try:
        if isinstance(value, (tuple, list)):
            value = value[0]
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return value[0] / value[1]
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _parse_exif_datetime(value) -> datetime | None:
    """Parse une date EXIF au format 'YYYY:MM:DD HH:MM:SS'."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


# --- Vidéos (ffprobe) ---
def _read_video(path: str, size: int) -> Metadata:
    meta = Metadata(path=path, is_video=True, size=size)
    probe = fftools.ffprobe_path()
    if probe is None:
        return meta

    result = fftools.run(
        [probe, "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path]
    )
    if result is None or result.returncode != 0:
        return meta

    try:
        info = json.loads(result.stdout.decode("utf-8", "ignore"))
    except (json.JSONDecodeError, ValueError):
        return meta

    fmt = info.get("format", {})
    streams = info.get("streams", [])

    # Durée.
    try:
        meta.duration = float(fmt.get("duration"))
    except (TypeError, ValueError):
        meta.duration = None

    # Dimensions : premier flux vidéo.
    for stream in streams:
        if stream.get("codec_type") == "video":
            meta.width = stream.get("width")
            meta.height = stream.get("height")
            break

    # Date de création (tags conteneur).
    tags = {**fmt.get("tags", {})}
    for stream in streams:
        tags.update(stream.get("tags", {}))
    meta.datetime_original = _parse_container_datetime(tags.get("creation_time"))

    # GPS : tag de localisation QuickTime (ISO 6709).
    location = (
        tags.get("location")
        or tags.get("com.apple.quicktime.location.ISO6709")
        or tags.get("location-eng")
    )
    if location:
        coords = geo.parse_iso6709(location)
        if coords:
            meta.latitude, meta.longitude = coords

    return meta


def _parse_container_datetime(value) -> datetime | None:
    """Parse une date ISO de conteneur (ex. '2023-07-15T10:20:30.000000Z')."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
