"""Helpers de géolocalisation : conversions GPS et parsing de coordonnées.

- Conversion DMS (degrés/minutes/secondes EXIF) → degrés décimaux.
- Parsing du format ISO 6709 utilisé par les conteneurs vidéo (QuickTime/MP4).
- Distance approximative entre deux points (utilisée pour les « similaires »).
"""

from __future__ import annotations

import math
import re


def _rational_to_float(value) -> float:
    """Convertit une composante DMS en float.

    Gère les deux représentations rencontrées :
    - tuple/liste ``(numérateur, dénominateur)`` (piexif) ;
    - nombre déjà rationnel/flottant (``IFDRational`` de Pillow).
    """
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return value[0] / value[1]
    return float(value)


def dms_to_decimal(dms, ref) -> float | None:
    """Convertit une coordonnée EXIF DMS (degrés/minutes/secondes) en décimal.

    *dms* est un triplet (degrés, minutes, secondes), chaque composante étant
    soit un couple (num, den), soit un nombre. *ref* vaut 'N'/'S'/'E'/'W'.
    Renvoie None si la donnée est invalide.
    """
    try:
        deg = _rational_to_float(dms[0])
        minutes = _rational_to_float(dms[1])
        seconds = _rational_to_float(dms[2])
    except (TypeError, IndexError, ValueError, ZeroDivisionError):
        return None
    value = deg + minutes / 60.0 + seconds / 3600.0
    if isinstance(ref, bytes):
        ref = ref.decode("ascii", "ignore")
    if ref and str(ref).upper() in ("S", "W"):
        value = -value
    return value


def parse_iso6709(text: str) -> tuple[float, float] | None:
    """Extrait (lat, lon) d'une chaîne ISO 6709 (ex. '+48.8584+002.2945/').

    Utilisé pour le tag QuickTime `com.apple.quicktime.location.ISO6709`.
    Renvoie None si le format n'est pas reconnu.
    """
    matches = re.findall(r"[+-]\d+(?:\.\d+)?", text or "")
    if len(matches) < 2:
        return None
    try:
        return float(matches[0]), float(matches[1])
    except ValueError:
        return None


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance approximative en mètres entre deux points GPS (formule de haversine)."""
    radius = 6_371_000.0  # rayon terrestre moyen en mètres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius * math.asin(math.sqrt(a))
