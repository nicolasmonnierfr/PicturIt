"""Tests de ``core.geo`` — conversions GPS et distances.

Point sensible couvert ici : ``dms_to_decimal`` doit accepter **deux**
représentations de rationnels (tuples piexif et IFDRational Pillow), piège
identifié dans ARCHITECTURE §6.3.
"""

from __future__ import annotations

import pytest

from core import geo

# Tour Eiffel, coordonnées de référence des tests.
_EIFFEL_LAT, _EIFFEL_LON = 48.8584, 2.2945


class TestDmsToDecimal:
    """Conversion DMS (EXIF) vers degrés décimaux."""

    def test_tuples_piexif(self):
        """Format piexif : chaque composante est un couple (num, den)."""
        dms = ((48, 1), (51, 1), (3024, 100))  # 48° 51' 30,24"
        assert geo.dms_to_decimal(dms, "N") == pytest.approx(48.8584, abs=1e-6)

    def test_floats_pillow(self):
        """Format Pillow : composantes déjà numériques (IFDRational)."""
        assert geo.dms_to_decimal((48.0, 51.0, 30.24), "N") == pytest.approx(
            48.8584, abs=1e-6
        )

    def test_formats_equivalents(self):
        """Les deux représentations doivent donner le même résultat."""
        tuples = geo.dms_to_decimal(((2, 1), (17, 1), (4200, 1000)), "E")
        floats = geo.dms_to_decimal((2.0, 17.0, 4.2), "E")
        assert tuples == pytest.approx(floats)

    @pytest.mark.parametrize("ref", ["S", "W", "s", "w"])
    def test_hemisphere_negatif(self, ref):
        """Les références S et W inversent le signe, casse indifférente."""
        assert geo.dms_to_decimal(((10, 1), (0, 1), (0, 1)), ref) == -10.0

    @pytest.mark.parametrize("ref", ["N", "E"])
    def test_hemisphere_positif(self, ref):
        assert geo.dms_to_decimal(((10, 1), (0, 1), (0, 1)), ref) == 10.0

    def test_ref_en_bytes(self):
        """Certaines bibliothèques renvoient la référence en bytes."""
        assert geo.dms_to_decimal(((10, 1), (0, 1), (0, 1)), b"S") == -10.0

    def test_ref_absente(self):
        """Sans référence, la valeur reste positive et rien n'est levé."""
        assert geo.dms_to_decimal(((10, 1), (0, 1), (0, 1)), None) == 10.0

    @pytest.mark.parametrize(
        "dms",
        [None, (), (1, 2), "abcdef", ((1, 0), (0, 1), (0, 1))],
        ids=["none", "vide", "incomplet", "texte", "division-par-zero"],
    )
    def test_donnees_invalides(self, dms):
        """Une donnée illisible renvoie None, jamais une exception."""
        assert geo.dms_to_decimal(dms, "N") is None


class TestParseIso6709:
    """Parsing des coordonnées ISO 6709 des conteneurs vidéo."""

    def test_format_apple(self):
        assert geo.parse_iso6709("+48.8584+002.2945/") == pytest.approx(
            (_EIFFEL_LAT, _EIFFEL_LON)
        )

    def test_coordonnees_negatives(self):
        lat, lon = geo.parse_iso6709("-33.8688+151.2093/")
        assert (lat, lon) == pytest.approx((-33.8688, 151.2093))

    def test_avec_altitude(self):
        """Une 3e composante (altitude) ne perturbe pas la lecture."""
        assert geo.parse_iso6709("+48.8584+002.2945+031.000/") == pytest.approx(
            (_EIFFEL_LAT, _EIFFEL_LON)
        )

    @pytest.mark.parametrize(
        "text",
        ["", None, "pas de coordonnees", "+48.8584"],
        ids=["vide", "none", "texte", "une-seule-valeur"],
    )
    def test_entrees_invalides(self, text):
        assert geo.parse_iso6709(text) is None


class TestHaversine:
    """Distance approximative entre deux points."""

    def test_distance_nulle(self):
        distance = geo.haversine_m(
            _EIFFEL_LAT, _EIFFEL_LON, _EIFFEL_LAT, _EIFFEL_LON
        )
        assert distance == 0.0

    def test_un_degre_de_latitude(self):
        """1° de latitude vaut environ 111 km (tolérance 1 km)."""
        assert geo.haversine_m(0.0, 0.0, 1.0, 0.0) == pytest.approx(111_195, abs=1000)

    def test_symetrie(self):
        aller = geo.haversine_m(48.85, 2.29, 43.30, 5.37)
        retour = geo.haversine_m(43.30, 5.37, 48.85, 2.29)
        assert aller == pytest.approx(retour)

    def test_seuil_des_similaires(self):
        """Un écart de 0,0002° de latitude reste sous le seuil de 50 m."""
        from core.duplicates import SIMILAR_GPS_METERS

        proche = geo.haversine_m(
            _EIFFEL_LAT, _EIFFEL_LON, _EIFFEL_LAT + 0.0002, _EIFFEL_LON
        )
        loin = geo.haversine_m(
            _EIFFEL_LAT, _EIFFEL_LON, _EIFFEL_LAT + 0.0020, _EIFFEL_LON
        )
        assert proche < SIMILAR_GPS_METERS < loin
