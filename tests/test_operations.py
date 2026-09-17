"""Tests de ``core.operations`` — tri, renommage, corbeille, annulation, journal.

La corbeille Windows n'est **jamais** sollicitée : la fixture ``fake_trash``
remplace ``send2trash`` et trace les appels (cf. conftest).
"""

from __future__ import annotations

import os

import pytest

from core import operations
from core.operations import COPIED, MOVED, REMOVED, OperationManager


@pytest.fixture
def manager():
    return OperationManager()


@pytest.fixture
def source(tmp_path):
    """Dossier source contenant photo.jpg et clip.mp4."""
    src = tmp_path / "source"
    src.mkdir()
    (src / "photo.jpg").write_bytes(b"pixels de la photo")
    (src / "clip.mp4").write_bytes(b"octets de la video")
    return src


@pytest.fixture
def cible(tmp_path):
    dest = tmp_path / "cible"
    dest.mkdir()
    return dest


class TestMove:
    """Déplacement vers un dossier cible."""

    def test_deplacement_simple(self, manager, source, cible):
        src = str(source / "photo.jpg")
        changes = manager.move([src], str(cible))

        assert len(changes) == 1
        assert changes[0].kind == MOVED
        assert changes[0].src == src
        assert not os.path.exists(src)
        assert os.path.exists(changes[0].dst)
        assert os.path.dirname(changes[0].dst) == str(cible)

    def test_contenu_preserve(self, manager, source, cible):
        changes = manager.move([str(source / "photo.jpg")], str(cible))
        assert open(changes[0].dst, "rb").read() == b"pixels de la photo"

    def test_dossier_cible_cree(self, manager, source, tmp_path):
        """Un dossier cible inexistant est créé à la volée."""
        dest = tmp_path / "nouveau" / "sous-dossier"
        changes = manager.move([str(source / "photo.jpg")], str(dest))
        assert len(changes) == 1
        assert os.path.isdir(dest)

    def test_collision_de_nom(self, manager, source, cible):
        """Un homonyme dans la cible fait suffixer le nouveau fichier."""
        (cible / "photo.jpg").write_bytes(b"deja present")
        changes = manager.move([str(source / "photo.jpg")], str(cible))

        assert os.path.basename(changes[0].dst) == "photo_1.jpg"
        assert (cible / "photo.jpg").read_bytes() == b"deja present"

    def test_deja_dans_la_cible(self, manager, source):
        """Déplacer un fichier vers son propre dossier ne fait rien."""
        assert manager.move([str(source / "photo.jpg")], str(source)) == []
        assert (source / "photo.jpg").exists()

    def test_source_inexistante_ignoree(self, manager, source, cible):
        changes = manager.move(
            [str(source / "absente.jpg"), str(source / "photo.jpg")], str(cible)
        )
        assert len(changes) == 1

    def test_liste_vide(self, manager, cible):
        assert manager.move([], str(cible)) == []
        assert manager.can_undo() is False


class TestCopy:
    """Copie vers un dossier cible."""

    def test_copie_simple(self, manager, source, cible):
        src = str(source / "photo.jpg")
        changes = manager.copy([src], str(cible))

        assert len(changes) == 1
        assert changes[0].kind == COPIED
        assert os.path.exists(src), "la source doit être conservée"
        assert os.path.exists(changes[0].dst)

    def test_copie_dans_le_meme_dossier(self, manager, source):
        """Contrairement au déplacement, copier sur place est autorisé."""
        changes = manager.copy([str(source / "photo.jpg")], str(source))
        assert len(changes) == 1
        assert os.path.basename(changes[0].dst) == "photo_1.jpg"

    def test_copie_multiple(self, manager, source, cible):
        changes = manager.copy(
            [str(source / "photo.jpg"), str(source / "clip.mp4")], str(cible)
        )
        assert len(changes) == 2
        assert all(os.path.exists(c.dst) for c in changes)


class TestRename:
    """Renommage dans le dossier d'origine."""

    def test_ajout_de_prefixe(self, manager, source):
        src = str(source / "photo.jpg")
        changes = manager.rename([(src, "2023_photo.jpg")])

        assert len(changes) == 1
        assert changes[0].kind == MOVED
        assert os.path.basename(changes[0].dst) == "2023_photo.jpg"
        assert not os.path.exists(src)

    def test_nom_inchange_ignore(self, manager, source):
        src = str(source / "photo.jpg")
        assert manager.rename([(src, "photo.jpg")]) == []

    def test_nom_vide_ignore(self, manager, source):
        assert manager.rename([(str(source / "photo.jpg"), "")]) == []

    def test_source_inexistante_ignoree(self, manager, source):
        assert manager.rename([(str(source / "absente.jpg"), "x.jpg")]) == []

    def test_collision(self, manager, source):
        """Renommer vers un nom déjà pris suffixe le résultat."""
        changes = manager.rename([(str(source / "photo.jpg"), "clip.mp4")])
        assert os.path.basename(changes[0].dst) == "clip_1.mp4"
        assert (source / "clip.mp4").read_bytes() == b"octets de la video"


class TestTrash:
    """Mise à la corbeille (simulée)."""

    def test_fichier_envoye(self, manager, source, fake_trash):
        src = str(source / "photo.jpg")
        changes = manager.trash([src])

        assert len(changes) == 1
        assert changes[0].kind == REMOVED
        assert changes[0].src == src
        assert not os.path.exists(src)
        assert len(fake_trash) == 1

    def test_chemin_absolu_transmis(self, manager, source, fake_trash):
        """send2trash reçoit toujours un chemin absolu."""
        manager.trash([str(source / "photo.jpg")])
        assert os.path.isabs(fake_trash[0])

    def test_inexistant_ignore(self, manager, source, fake_trash):
        assert manager.trash([str(source / "absente.jpg")]) == []
        assert fake_trash == []

    def test_non_annulable(self, manager, source, fake_trash):
        """La corbeille n'alimente pas la pile d'annulation (SPEC 5.1)."""
        manager.trash([str(source / "photo.jpg")])
        assert manager.can_undo() is False

    def test_echec_de_la_corbeille(self, manager, source, monkeypatch):
        """Si send2trash échoue, le fichier reste et rien n'est signalé."""
        monkeypatch.setattr(operations, "send2trash", None)
        src = str(source / "photo.jpg")
        assert manager.trash([src]) == []
        assert os.path.exists(src)


class TestUndo:
    """Pile d'annulation multi-niveaux."""

    def test_rien_a_annuler(self, manager):
        assert manager.can_undo() is False
        assert manager.undo() == []
        assert manager.last_undo_label() == ""

    def test_annulation_d_un_deplacement(self, manager, source, cible):
        src = str(source / "photo.jpg")
        manager.move([src], str(cible))
        assert manager.can_undo() is True
        assert manager.last_undo_label() == "Déplacement"

        manager.undo()
        assert os.path.exists(src), "le fichier doit revenir à sa place"
        assert not os.listdir(cible)

    def test_annulation_d_une_copie(self, manager, source, cible, fake_trash):
        """Annuler une copie envoie la copie à la corbeille, pas l'original."""
        src = str(source / "photo.jpg")
        changes = manager.copy([src], str(cible))
        assert manager.last_undo_label() == "Copie"

        manager.undo()
        assert os.path.exists(src)
        assert not os.path.exists(changes[0].dst)
        assert len(fake_trash) == 1

    def test_annulation_d_un_renommage(self, manager, source):
        src = str(source / "photo.jpg")
        manager.rename([(src, "renomme.jpg")])
        assert manager.last_undo_label() == "Renommage"

        manager.undo()
        assert os.path.exists(src)

    def test_lot_complet(self, manager, source, cible):
        """Un déplacement multiple s'annule en une fois."""
        manager.move(
            [str(source / "photo.jpg"), str(source / "clip.mp4")], str(cible)
        )
        manager.undo()
        assert sorted(os.listdir(source)) == ["clip.mp4", "photo.jpg"]

    def test_pile_lifo(self, manager, source, cible, tmp_path):
        """Les annulations se font dans l'ordre inverse des opérations."""
        autre = tmp_path / "autre"
        autre.mkdir()
        manager.move([str(source / "photo.jpg")], str(cible))
        manager.move([str(source / "clip.mp4")], str(autre))

        manager.undo()  # annule le second déplacement
        assert (source / "clip.mp4").exists()
        assert not (source / "photo.jpg").exists()

        manager.undo()  # annule le premier
        assert (source / "photo.jpg").exists()
        assert manager.can_undo() is False

    def test_restauration_si_le_nom_est_repris(self, manager, source, cible):
        """Si un homonyme occupe la place d'origine, la restauration suffixe."""
        src = str(source / "photo.jpg")
        manager.move([src], str(cible))
        (source / "photo.jpg").write_bytes(b"un autre fichier entre-temps")

        changes = manager.undo()
        assert os.path.basename(changes[0].dst) == "photo_1.jpg"
        assert (source / "photo.jpg").read_bytes() == b"un autre fichier entre-temps"


class TestJournal:
    """Journal des opérations et export."""

    def test_entree_par_operation(self, manager, source, cible):
        manager.move([str(source / "photo.jpg")], str(cible))
        assert len(manager.log) == 1
        entree = manager.log[0]
        assert entree.action == "Déplacer"
        assert entree.source.endswith("photo.jpg")
        assert entree.timestamp

    @pytest.mark.parametrize(
        "operation,action",
        [("move", "Déplacer"), ("copy", "Copier")],
    )
    def test_libelles_d_action(self, manager, source, cible, operation, action):
        getattr(manager, operation)([str(source / "photo.jpg")], str(cible))
        assert manager.log[0].action == action

    def test_journal_de_la_corbeille(self, manager, source, fake_trash):
        manager.trash([str(source / "photo.jpg")])
        assert manager.log[0].action == "Corbeille"
        assert manager.log[0].destination == ""

    def test_log_edit(self, manager):
        """Les éditions sont journalisées sans entrer dans la pile d'annulation."""
        manager.log_edit("Pivoter", "a.jpg", "a.jpg")
        assert manager.log[0].action == "Pivoter"
        assert manager.can_undo() is False

    def test_annulation_journalisee(self, manager, source, cible):
        manager.move([str(source / "photo.jpg")], str(cible))
        manager.undo()
        assert manager.log[-1].action == "Annuler (déplacer)"

    def test_export(self, manager, source, cible, tmp_path):
        manager.move([str(source / "photo.jpg")], str(cible))
        destination = tmp_path / "journal.txt"
        manager.export_log(str(destination))

        lignes = destination.read_text(encoding="utf-8").splitlines()
        assert lignes[0] == "Journal des opérations — PicturIt"
        assert lignes[1].split("\t") == [
            "horodatage", "action", "source", "destination"
        ]
        assert lignes[2].split("\t")[1] == "Déplacer"

    def test_export_journal_vide(self, manager, tmp_path):
        destination = tmp_path / "vide.txt"
        manager.export_log(str(destination))
        assert destination.exists()


class TestUniqueDest:
    """Choix d'un nom de destination libre."""

    def test_nom_libre_conserve(self, tmp_path):
        assert operations._unique_dest(str(tmp_path), "a.jpg") == str(
            tmp_path / "a.jpg"
        )

    def test_suffixes_successifs(self, tmp_path):
        (tmp_path / "a.jpg").write_bytes(b"1")
        (tmp_path / "a_1.jpg").write_bytes(b"2")
        assert operations._unique_dest(str(tmp_path), "a.jpg") == str(
            tmp_path / "a_2.jpg"
        )

    def test_extension_preservee(self, tmp_path):
        (tmp_path / "film.mp4").write_bytes(b"1")
        assert operations._unique_dest(str(tmp_path), "film.mp4").endswith("_1.mp4")
