"""Tests de ``core.metadata`` — EXIF photos, ffprobe vidéos et cache mémoire.

Les vidéos sont testées **sans ffprobe** : le binaire est simulé (monkeypatch de
``fftools.run``), ce qui rend la suite exécutable sur une machine sans ffmpeg.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime

import piexif
import pytest
from PIL import Image

from core import metadata

from .conftest import write_photo

_DATE = datetime(2023, 7, 15, 10, 20, 30)
_GPS = (48.8584, 2.2945)


def _fake_ffprobe(monkeypatch, payload, returncode: int = 0) -> None:
    """Simule ffprobe : binaire présent, sortie JSON imposée."""
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    monkeypatch.setattr(metadata.fftools, "ffprobe_path", lambda: "ffprobe.exe")
    monkeypatch.setattr(
        metadata.fftools,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=raw, stderr=b""
        ),
    )


class TestPhotos:
    """Lecture EXIF des photos."""

    def test_sans_exif(self, tmp_path):
        """Une photo nue expose ses dimensions, sans date ni GPS."""
        path = write_photo(tmp_path / "nue.jpg", size=(64, 48))
        meta = metadata.read(path)
        assert (meta.width, meta.height) == (64, 48)
        assert meta.is_video is False
        assert meta.datetime_original is None
        assert meta.has_gps is False
        assert meta.size == os.path.getsize(path)

    def test_date_de_prise_de_vue(self, tmp_path):
        path = write_photo(tmp_path / "datee.jpg", dt=_DATE)
        assert metadata.read(path).datetime_original == _DATE

    def test_coordonnees_gps(self, tmp_path):
        path = write_photo(tmp_path / "geo.jpg", gps=_GPS)
        meta = metadata.read(path)
        assert meta.has_gps
        assert meta.latitude == pytest.approx(_GPS[0], abs=1e-5)
        assert meta.longitude == pytest.approx(_GPS[1], abs=1e-5)

    def test_hemisphere_sud_ouest(self, tmp_path):
        """Les coordonnées négatives font un aller-retour correct."""
        path = write_photo(tmp_path / "sydney.jpg", gps=(-33.8688, -151.2093))
        meta = metadata.read(path)
        assert meta.latitude == pytest.approx(-33.8688, abs=1e-5)
        assert meta.longitude == pytest.approx(-151.2093, abs=1e-5)

    def test_exif_etendu(self, tmp_path):
        """Appareil, objectif et paramètres de prise de vue sont remontés."""
        path = write_photo(tmp_path / "reflex.jpg")
        exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        exif["0th"][piexif.ImageIFD.Make] = "Canon"
        exif["0th"][piexif.ImageIFD.Model] = "EOS R6"
        exif["Exif"][piexif.ExifIFD.LensModel] = "RF 50mm F1.8"
        exif["Exif"][piexif.ExifIFD.ISOSpeedRatings] = 400
        exif["Exif"][piexif.ExifIFD.FNumber] = (28, 10)
        exif["Exif"][piexif.ExifIFD.FocalLength] = (50, 1)
        exif["Exif"][piexif.ExifIFD.ExposureTime] = (1, 250)
        piexif.insert(piexif.dump(exif), path)

        meta = metadata.read(path)
        assert meta.camera == "Canon EOS R6"
        assert meta.lens == "RF 50mm F1.8"
        assert meta.iso == 400
        assert meta.aperture == pytest.approx(2.8)
        assert meta.focal_length == pytest.approx(50.0)
        assert meta.exposure == pytest.approx(1 / 250)

    def test_helper_has_gps(self, tmp_path):
        avec = write_photo(tmp_path / "avec.jpg", gps=_GPS)
        sans = write_photo(tmp_path / "sans.jpg")
        assert metadata.has_gps(avec) is True
        assert metadata.has_gps(sans) is False


class TestToleranceAuxErreurs:
    """Aucun fichier ne doit provoquer d'exception (convention du projet)."""

    def test_fichier_corrompu(self, tmp_path):
        path = tmp_path / "corrompu.jpg"
        path.write_bytes(b"ceci n'est pas une image")
        meta = metadata.read(str(path))
        assert meta.width is None
        assert meta.datetime_original is None
        assert meta.has_gps is False

    def test_fichier_inexistant(self, tmp_path):
        meta = metadata.read(str(tmp_path / "absent.jpg"))
        assert meta.size == 0
        assert meta.has_gps is False

    def test_fichier_vide(self, tmp_path):
        path = tmp_path / "vide.jpg"
        path.write_bytes(b"")
        assert metadata.read(str(path)).width is None


class TestVideos:
    """Lecture des métadonnées vidéo via ffprobe (simulé)."""

    @pytest.fixture
    def video(self, tmp_path):
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"faux conteneur mp4")
        return str(path)

    def test_duree_dimensions_et_date(self, monkeypatch, video):
        _fake_ffprobe(
            monkeypatch,
            {
                "format": {
                    "duration": "12.5",
                    "tags": {"creation_time": "2023-07-15T10:20:30.000000Z"},
                },
                "streams": [
                    {"codec_type": "audio"},
                    {"codec_type": "video", "width": 1920, "height": 1080},
                ],
            },
        )
        meta = metadata.read(video)
        assert meta.is_video is True
        assert meta.duration == pytest.approx(12.5)
        assert (meta.width, meta.height) == (1920, 1080)
        assert meta.datetime_original == _DATE

    def test_gps_iso6709(self, monkeypatch, video):
        _fake_ffprobe(
            monkeypatch,
            {
                "format": {
                    "tags": {
                        "com.apple.quicktime.location.ISO6709": "+48.8584+002.2945/"
                    }
                },
                "streams": [],
            },
        )
        meta = metadata.read(video)
        assert meta.latitude == pytest.approx(_GPS[0])
        assert meta.longitude == pytest.approx(_GPS[1])

    def test_ffprobe_absent(self, monkeypatch, video):
        """Sans binaire, les métadonnées sont vides mais rien ne plante."""
        monkeypatch.setattr(metadata.fftools, "ffprobe_path", lambda: None)
        meta = metadata.read(video)
        assert meta.is_video is True
        assert meta.duration is None
        assert meta.has_gps is False

    def test_sortie_json_invalide(self, monkeypatch, video):
        _fake_ffprobe(monkeypatch, b"ceci n'est pas du JSON")
        assert metadata.read(video).duration is None

    def test_ffprobe_en_erreur(self, monkeypatch, video):
        _fake_ffprobe(monkeypatch, {"format": {"duration": "9"}}, returncode=1)
        assert metadata.read(video).duration is None

    def test_duree_illisible(self, monkeypatch, video):
        _fake_ffprobe(monkeypatch, {"format": {"duration": "n/a"}, "streams": []})
        assert metadata.read(video).duration is None


class TestCache:
    """Cache mémoire : mémoïsation et invalidation (ARCHITECTURE §6.16)."""

    def test_deux_lectures_memoisees(self, tmp_path):
        path = write_photo(tmp_path / "cache.jpg", dt=_DATE)
        assert metadata.read(path) is metadata.read(path)

    def test_invalidation_si_le_fichier_change(self, tmp_path):
        """La clé inclut mtime et taille : un fichier réécrit est relu."""
        path = write_photo(tmp_path / "evolutif.jpg", dt=_DATE)
        assert metadata.read(path).datetime_original == _DATE

        autre = datetime(2020, 1, 2, 3, 4, 5)
        write_photo(tmp_path / "evolutif.jpg", dt=autre, size=(80, 60))
        assert metadata.read(path).datetime_original == autre

    def test_invalidate_explicite(self, tmp_path):
        path = write_photo(tmp_path / "manuel.jpg", dt=_DATE)
        premier = metadata.read(path)
        metadata.invalidate(path)
        assert metadata.read(path) is not premier

    def test_clear_cache(self, tmp_path):
        path = write_photo(tmp_path / "global.jpg", dt=_DATE)
        premier = metadata.read(path)
        metadata.clear_cache()
        assert metadata.read(path) is not premier

    def test_invalidate_chemin_inconnu(self, tmp_path):
        """Invalider un chemin absent du cache ne lève pas."""
        metadata.invalidate(str(tmp_path / "jamais_lu.jpg"))


class TestLectureDepuisEnTete:
    """``read_from_header`` : métadonnées sans rouvrir le fichier.

    Chemin critique : le worker de vignettes lit les premiers octets une seule
    fois et en tire à la fois la vignette et les métadonnées. Le résultat doit
    être **identique** à une lecture complète, sans quoi dates, GPS et carte
    divergeraient silencieusement.
    """

    @staticmethod
    def _entete(path: str, taille: int = 64 * 1024) -> bytes:
        with open(path, "rb") as fh:
            return fh.read(taille)

    def test_identique_a_la_lecture_complete(self, tmp_path):
        path = write_photo(tmp_path / "ref.jpg", dt=_DATE, gps=_GPS, size=(80, 60))
        complet = metadata.read(path)
        metadata.clear_cache()
        partiel = metadata.read_from_header(path, self._entete(path))

        assert partiel is not None
        assert (partiel.width, partiel.height) == (complet.width, complet.height)
        assert partiel.datetime_original == complet.datetime_original
        assert partiel.latitude == pytest.approx(complet.latitude)
        assert partiel.longitude == pytest.approx(complet.longitude)
        assert partiel.size == complet.size

    def test_photo_sans_exif(self, tmp_path):
        """Une photo nue reste exploitable : seules les dimensions sont lues."""
        path = write_photo(tmp_path / "nue.jpg", size=(64, 48))
        meta = metadata.read_from_header(path, self._entete(path))
        assert meta is not None
        assert (meta.width, meta.height) == (64, 48)
        assert meta.datetime_original is None

    def test_resultat_mis_en_cache(self, tmp_path):
        """Le cache partagé est alimenté : l'aperçu et le tri en profitent."""
        path = write_photo(tmp_path / "cache.jpg", dt=_DATE)
        depuis_entete = metadata.read_from_header(path, self._entete(path))
        assert metadata.read(path) is depuis_entete

    def test_cache_prioritaire(self, tmp_path):
        """Si le fichier est déjà en cache, l'en-tête n'est pas re-analysé."""
        path = write_photo(tmp_path / "deja.jpg", dt=_DATE)
        premier = metadata.read(path)
        assert metadata.read_from_header(path, b"octets sans rapport") is premier

    def test_entete_inexploitable(self, tmp_path):
        """En-tête tronqué ou illisible : None, pour que l'appelant relise."""
        path = write_photo(tmp_path / "photo.jpg", dt=_DATE)
        assert metadata.read_from_header(path, b"pas une image") is None

    def test_entete_vide(self, tmp_path):
        path = write_photo(tmp_path / "photo.jpg")
        assert metadata.read_from_header(path, b"") is None

    def test_fichier_inexistant(self, tmp_path):
        assert metadata.read_from_header(str(tmp_path / "absent.jpg"), b"x") is None

    def test_exif_etendu_present(self, tmp_path):
        """L'EXIF étendu est en tête de fichier : il doit survivre à la troncature."""
        path = write_photo(tmp_path / "reflex.jpg", dt=_DATE)
        exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        exif["0th"][piexif.ImageIFD.Make] = "Canon"
        exif["0th"][piexif.ImageIFD.Model] = "EOS R6"
        exif["Exif"][piexif.ExifIFD.ISOSpeedRatings] = 800
        piexif.insert(piexif.dump(exif), path)

        meta = metadata.read_from_header(path, self._entete(path))
        assert meta is not None
        assert meta.camera == "Canon EOS R6"
        assert meta.iso == 800


class TestFormatsDeDate:
    """Parsing des dates EXIF et conteneur."""

    @pytest.mark.parametrize(
        "valeur", [None, "", "pas une date", "2023-07-15 10:20:30", 12345],
        ids=["none", "vide", "texte", "mauvais-separateur", "entier"],
    )
    def test_date_exif_invalide(self, valeur):
        assert metadata._parse_exif_datetime(valeur) is None

    @pytest.mark.parametrize(
        "valeur",
        [
            "2023-07-15T10:20:30.000000Z",
            "2023-07-15T10:20:30",
            "2023-07-15 10:20:30",
        ],
    )
    def test_date_conteneur_valide(self, valeur):
        assert metadata._parse_container_datetime(valeur) == _DATE

    @pytest.mark.parametrize("valeur", [None, "", "hier", 42])
    def test_date_conteneur_invalide(self, valeur):
        assert metadata._parse_container_datetime(valeur) is None


class TestFormatsImage:
    """Les formats non JPEG restent lisibles (sans EXIF)."""

    def test_png(self, tmp_path):
        path = tmp_path / "image.png"
        Image.new("RGB", (32, 16), (10, 20, 30)).save(path, "PNG")
        meta = metadata.read(str(path))
        assert (meta.width, meta.height) == (32, 16)
        assert meta.datetime_original is None
