"""Tests de ``core.editing`` — rotation, recadrage et conversion.

Ces fonctions **écrivent sur le disque** ; tous les fichiers manipulés ici
vivent dans le ``tmp_path`` de pytest.

Le point le plus délicat est la rotation JPEG « sans perte » : elle ne doit
modifier que le tag EXIF *Orientation*, jamais les pixels encodés.
"""

from __future__ import annotations

import os

import piexif
import pytest
from PIL import Image

from core import editing

from .conftest import write_photo


def _orientation(path: str) -> int:
    """Valeur du tag EXIF Orientation (1 si absent)."""
    return piexif.load(path)["0th"].get(piexif.ImageIFD.Orientation, 1)


def _pixels_bruts(path: str) -> bytes:
    """Pixels tels que stockés, sans appliquer l'orientation EXIF."""
    with Image.open(path) as img:
        return img.convert("RGB").tobytes()


class TestRotationJpeg:
    """Rotation JPEG : sans perte, via le tag Orientation."""

    def test_horaire_depuis_orientation_absente(self, tmp_path):
        path = write_photo(tmp_path / "a.jpg")
        editing.rotate(path, clockwise=True)
        assert _orientation(path) == 6

    def test_antihoraire_depuis_orientation_absente(self, tmp_path):
        path = write_photo(tmp_path / "a.jpg")
        editing.rotate(path, clockwise=False)
        assert _orientation(path) == 8

    def test_pixels_inchanges(self, tmp_path):
        """Aucun ré-encodage : les pixels stockés sont strictement identiques."""
        path = write_photo(tmp_path / "a.jpg", size=(64, 48))
        avant = _pixels_bruts(path)
        editing.rotate(path, clockwise=True)
        assert _pixels_bruts(path) == avant

    def test_dimensions_stockees_inchangees(self, tmp_path):
        path = write_photo(tmp_path / "a.jpg", size=(64, 48))
        editing.rotate(path, clockwise=True)
        with Image.open(path) as img:
            assert img.size == (64, 48)

    def test_quatre_rotations_reviennent_au_depart(self, tmp_path):
        path = write_photo(tmp_path / "a.jpg")
        for _ in range(4):
            editing.rotate(path, clockwise=True)
        assert _orientation(path) == 1

    def test_rotations_opposees_s_annulent(self, tmp_path):
        path = write_photo(tmp_path / "a.jpg")
        editing.rotate(path, clockwise=True)
        editing.rotate(path, clockwise=False)
        assert _orientation(path) == 1

    @pytest.mark.parametrize(
        "depart,attendu", [(1, 6), (6, 3), (3, 8), (8, 1)]
    )
    def test_cycle_horaire(self, tmp_path, depart, attendu):
        path = write_photo(tmp_path / "a.jpg")
        exif = piexif.load(path)
        exif["0th"][piexif.ImageIFD.Orientation] = depart
        piexif.insert(piexif.dump(exif), path)

        editing.rotate(path, clockwise=True)
        assert _orientation(path) == attendu

    def test_date_exif_preservee(self, tmp_path):
        """La rotation ne doit pas perdre les métadonnées existantes."""
        from datetime import datetime

        path = write_photo(tmp_path / "a.jpg", dt=datetime(2023, 7, 15, 10, 20, 30))
        editing.rotate(path, clockwise=True)
        exif = piexif.load(path)
        assert exif["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2023:07:15 10:20:30"


class TestRotationAutresFormats:
    """Formats non JPEG : rotation pixel avec ré-encodage."""

    def test_png_dimensions_inversees(self, tmp_path):
        path = str(tmp_path / "a.png")
        Image.new("RGB", (64, 48), (10, 20, 30)).save(path, "PNG")
        editing.rotate(path, clockwise=True)
        with Image.open(path) as img:
            assert img.size == (48, 64)

    def test_png_deux_rotations(self, tmp_path):
        path = str(tmp_path / "a.png")
        Image.new("RGB", (64, 48), (10, 20, 30)).save(path, "PNG")
        editing.rotate(path, clockwise=True)
        editing.rotate(path, clockwise=True)
        with Image.open(path) as img:
            assert img.size == (64, 48)


class TestCrop:
    """Recadrage pixel."""

    def test_dimensions_resultantes(self, tmp_path):
        path = write_photo(tmp_path / "a.jpg", size=(100, 80))
        editing.crop(path, (10, 10, 40, 30))
        with Image.open(path) as img:
            assert img.size == (30, 20)

    def test_boite_debordante_ramenee_dans_l_image(self, tmp_path):
        """Une zone qui dépasse est ramenée aux bords, sans erreur."""
        path = write_photo(tmp_path / "a.jpg", size=(100, 80))
        editing.crop(path, (-50, -50, 500, 500))
        with Image.open(path) as img:
            assert img.size == (100, 80)

    def test_boite_degeneree(self, tmp_path):
        """Une zone de largeur nulle produit au moins un pixel, sans planter."""
        path = write_photo(tmp_path / "a.jpg", size=(100, 80))
        editing.crop(path, (10, 10, 10, 10))
        with Image.open(path) as img:
            assert img.size == (1, 1)

    def test_png(self, tmp_path):
        path = str(tmp_path / "a.png")
        Image.new("RGB", (100, 80), (10, 20, 30)).save(path, "PNG")
        editing.crop(path, (0, 0, 50, 40))
        with Image.open(path) as img:
            assert img.size == (50, 40)


class TestConvert:
    """Conversion de format."""

    @pytest.mark.parametrize("ext", [".png", ".gif", ".bmp"])
    def test_formats_supportes(self, tmp_path, ext):
        src = write_photo(tmp_path / "a.jpg", size=(32, 24))
        dst = editing.convert(src, ext)

        assert dst == str(tmp_path / f"a{ext}")
        assert os.path.exists(dst)
        with Image.open(dst) as img:
            assert img.size == (32, 24)

    def test_source_conservee(self, tmp_path):
        """La stratégie remplacer/copier appartient à l'appelant."""
        src = write_photo(tmp_path / "a.jpg")
        editing.convert(src, ".png")
        assert os.path.exists(src)

    def test_extension_en_majuscules(self, tmp_path):
        src = write_photo(tmp_path / "a.jpg")
        assert editing.convert(src, ".PNG").endswith(".png")

    def test_conversion_vers_le_meme_format(self, tmp_path):
        """Convertir un JPEG en JPEG réécrit le même chemin."""
        src = write_photo(tmp_path / "a.jpg")
        assert editing.convert(src, ".jpg") == src
        assert os.path.exists(src)

    @pytest.mark.parametrize("ext", [".tiff", ".webp", ".heic", ".txt", ""])
    def test_format_non_supporte(self, tmp_path, ext):
        src = write_photo(tmp_path / "a.jpg")
        with pytest.raises(ValueError):
            editing.convert(src, ext)

    def test_liste_des_formats(self):
        assert set(editing.CONVERT_FORMATS) == {".jpg", ".png", ".gif", ".bmp"}
