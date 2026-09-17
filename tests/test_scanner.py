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


class TestScanNonRecursif:
    """Mode navigation : contenu direct seulement (SPEC 4.1 révisée).

    C'est ce mode qui rend le clic sur une racine de disque instantané : on ne
    descend jamais dans l'arborescence.
    """

    @pytest.fixture
    def arborescence(self, tmp_path):
        (tmp_path / "vacances" / "jour2").mkdir(parents=True)
        for rel in (
            "a.jpg",
            "b.mp4",
            "notes.txt",
            "vacances/photo.png",
            "vacances/jour2/x.jpeg",
        ):
            (tmp_path / rel).write_bytes(b"contenu-de-test")
        return tmp_path

    def test_une_seule_section(self, arborescence):
        sections = scanner.scan(str(arborescence), recursive=False)
        assert [nom for nom, _ in sections] == [scanner.ROOT_SECTION_LABEL]

    def test_seul_le_contenu_direct(self, arborescence):
        sections = dict(scanner.scan(str(arborescence), recursive=False))
        noms = [os.path.basename(m.path) for m in sections[scanner.ROOT_SECTION_LABEL]]
        assert noms == ["a.jpg", "b.mp4"]

    def test_sous_dossiers_absents(self, arborescence):
        chemins = [
            m.path
            for _, files in scanner.scan(str(arborescence), recursive=False)
            for m in files
        ]
        assert not any("vacances" in p for p in chemins)

    def test_dossier_sans_media_direct(self, tmp_path):
        """Un dossier dont les médias sont tous en profondeur paraît vide."""
        (tmp_path / "sous").mkdir()
        (tmp_path / "sous" / "photo.jpg").write_bytes(b"x")
        assert scanner.scan(str(tmp_path), recursive=False) == []

    def test_recursif_par_defaut(self, arborescence):
        """L'appel sans argument reste récursif (compatibilité)."""
        assert len(scanner.scan(str(arborescence))) == 3


class TestAnnulation:
    """Le parcours récursif doit pouvoir être interrompu."""

    @pytest.fixture
    def arborescence(self, tmp_path):
        for i in range(5):
            sous = tmp_path / f"dossier{i}"
            sous.mkdir()
            (sous / "photo.jpg").write_bytes(b"x")
        (tmp_path / "racine.jpg").write_bytes(b"x")
        return tmp_path

    def test_annulation_immediate(self, arborescence):
        """Annuler dès le premier dossier ne renvoie rien."""
        assert scanner.scan(str(arborescence), should_cancel=lambda: True) == []

    def test_sans_annulation(self, arborescence):
        sections = scanner.scan(str(arborescence), should_cancel=lambda: False)
        assert len(sections) == 6  # racine + 5 sous-dossiers

    def test_annulation_en_cours_de_route(self, arborescence):
        """Une annulation après quelques dossiers interrompt le parcours."""
        visites = []

        def annuler() -> bool:
            visites.append(1)
            return len(visites) > 3

        assert scanner.scan(str(arborescence), should_cancel=annuler) == []
        assert len(visites) == 4, "le parcours doit s'arrêter dès l'annulation"

    def test_rappel_interroge_a_chaque_dossier(self, arborescence):
        appels = []
        scanner.scan(str(arborescence), should_cancel=lambda: bool(appels.append(1)))
        assert len(appels) == 6
