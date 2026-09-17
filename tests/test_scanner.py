"""Tests de ``core.scanner`` — reconnaissance des formats et scan récursif."""

from __future__ import annotations

import os

import pytest

from core import scanner


class TestReconnaissanceDesFormats:
    """Les extensions sont toujours comparées en minuscules."""

    @pytest.mark.parametrize(
        "name", ["a.jpg", "a.jpeg", "a.png", "a.heic", "a.tiff", "a.bmp", "a.webp"]
    )
    def test_photos_reconnues(self, name):
        assert scanner.is_supported(name)
        assert not scanner.is_video(name)

    @pytest.mark.parametrize("name", ["a.mp4", "a.mov", "a.avi", "a.mkv", "a.wmv"])
    def test_videos_reconnues(self, name):
        assert scanner.is_supported(name)
        assert scanner.is_video(name)

    @pytest.mark.parametrize("name", ["PHOTO.JPG", "Film.MP4", "Image.PnG"])
    def test_casse_indifferente(self, name):
        assert scanner.is_supported(name)

    @pytest.mark.parametrize(
        "name", ["notes.txt", "doc.pdf", "archive.zip", "sans_extension", "a.jpg.bak"]
    )
    def test_formats_ignores(self, name):
        assert not scanner.is_supported(name)


class TestSectionKey:
    """Le dossier racine doit toujours passer avant les sous-dossiers."""

    def test_racine_en_premier(self):
        noms = ["zoo", scanner.ROOT_SECTION_LABEL, "alpha"]
        assert sorted(noms, key=scanner.section_key)[0] == scanner.ROOT_SECTION_LABEL

    def test_ordre_alphabetique_insensible_a_la_casse(self):
        noms = ["Beta", "alpha", "Gamma"]
        assert sorted(noms, key=scanner.section_key) == ["alpha", "Beta", "Gamma"]


class TestScan:
    """Scan récursif d'une arborescence."""

    @pytest.fixture
    def arborescence(self, tmp_path):
        """Crée une arborescence de test et renvoie sa racine.

        racine/ : b.jpg, a.jpg, notes.txt
        racine/vacances/ : film.mp4, photo.png
        racine/vacances/jour2/ : x.jpeg
        racine/vide/ : aucun média
        """
        (tmp_path / "vacances" / "jour2").mkdir(parents=True)
        (tmp_path / "vide").mkdir()
        for rel in (
            "b.jpg",
            "a.jpg",
            "notes.txt",
            "vacances/film.mp4",
            "vacances/photo.png",
            "vacances/jour2/x.jpeg",
        ):
            (tmp_path / rel).write_bytes(b"contenu-de-test")
        return tmp_path

    def test_sections_attendues(self, arborescence):
        sections = scanner.scan(str(arborescence))
        noms = [nom for nom, _ in sections]
        assert noms == [
            scanner.ROOT_SECTION_LABEL,
            "vacances",
            os.path.join("vacances", "jour2"),
        ]

    def test_dossier_sans_media_absent(self, arborescence):
        """Un sous-dossier sans média ne produit aucune section."""
        noms = [nom for nom, _ in scanner.scan(str(arborescence))]
        assert "vide" not in noms

    def test_fichiers_tries_par_nom(self, arborescence):
        sections = dict(scanner.scan(str(arborescence)))
        racine = sections[scanner.ROOT_SECTION_LABEL]
        assert [os.path.basename(m.path) for m in racine] == ["a.jpg", "b.jpg"]

    def test_non_medias_ignores(self, arborescence):
        chemins = [
            m.path for _, files in scanner.scan(str(arborescence)) for m in files
        ]
        assert not any(p.endswith("notes.txt") for p in chemins)

    def test_metadonnees_du_mediafile(self, arborescence):
        sections = dict(scanner.scan(str(arborescence)))
        film = sections["vacances"][0]
        assert os.path.basename(film.path) == "film.mp4"
        assert film.is_video is True
        assert film.section == "vacances"
        assert film.size == len(b"contenu-de-test")
        assert os.path.isabs(film.path)

    def test_photo_non_marquee_video(self, arborescence):
        sections = dict(scanner.scan(str(arborescence)))
        photo = sections["vacances"][1]
        assert os.path.basename(photo.path) == "photo.png"
        assert photo.is_video is False

    def test_dossier_vide(self, tmp_path):
        assert scanner.scan(str(tmp_path)) == []

    def test_dossier_inexistant(self, tmp_path):
        """Un chemin absent ne doit pas lever : os.walk reste silencieux."""
        assert scanner.scan(str(tmp_path / "absent")) == []
