"""Tests de ``core.duplicates`` — doublons identiques et fichiers similaires.

Rappel des règles (SPEC 4.7) :
- **identiques** = même hash SHA-256 **ou** (même taille **et** même date) ;
- **similaires** = dates à moins de 2 s **et** positions à moins de 50 m.

Le second critère des « identiques » est contre-intuitif et documenté comme un
piège (ARCHITECTURE §6.4) : il est testé explicitement ci-dessous.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta

import pytest

from core import duplicates

from .conftest import pad_to, write_photo

_DATE = datetime(2023, 7, 15, 10, 20, 30)
_LIEU = (48.8584, 2.2945)
# Environ 220 m au nord du premier lieu : au-delà du seuil de 50 m.
_LIEU_LOIN = (48.8604, 2.2945)


def _analyse(*paths):
    return duplicates.analyze(list(paths))


class TestSeuils:
    """Les seuils sont des constantes nommées, conformes à la SPEC."""

    def test_valeurs(self):
        assert duplicates.SIMILAR_TIME_SECONDS == 2.0
        assert duplicates.SIMILAR_GPS_METERS == 50.0


class TestIdentiques:
    """Détection des doublons stricts."""

    def test_copie_binaire(self, tmp_path):
        """Deux fichiers au contenu identique forment un groupe rouge."""
        original = write_photo(tmp_path / "a.jpg", dt=_DATE)
        copie = str(tmp_path / "b.jpg")
        shutil.copy2(original, copie)

        index = _analyse(original, copie)
        assert index.color_of[original] == duplicates.COLOR_IDENTICAL
        assert index.color_of[copie] == duplicates.COLOR_IDENTICAL
        assert index.group_of[original] == index.group_of[copie]
        assert index.members_of(original) == {original, copie}

    def test_meme_taille_et_meme_date(self, tmp_path):
        """Contenus différents mais taille + date identiques : marqués identiques.

        Comportement conforme à la SPEC, surprenant sur des jeux de test
        artificiels (cf. ARCHITECTURE §6.4).
        """
        gros = write_photo(tmp_path / "gros.jpg", dt=_DATE, size=(120, 90))
        petit = write_photo(
            tmp_path / "petit.jpg", dt=_DATE, size=(64, 48), color=(10, 200, 40)
        )
        pad_to(petit, os.path.getsize(gros))

        index = _analyse(gros, petit)
        assert index.color_of[gros] == duplicates.COLOR_IDENTICAL
        assert index.group_of[gros] == index.group_of[petit]

    def test_meme_date_mais_tailles_differentes(self, tmp_path):
        """La date seule ne suffit pas : il faut aussi la même taille."""
        un = write_photo(tmp_path / "un.jpg", dt=_DATE, size=(64, 48))
        deux = write_photo(
            tmp_path / "deux.jpg", dt=_DATE, size=(200, 150), color=(9, 9, 200)
        )
        assert os.path.getsize(un) != os.path.getsize(deux)

        index = _analyse(un, deux)
        assert index.grouped_paths() == set()

    def test_meme_taille_sans_date(self, tmp_path):
        """Sans date de prise de vue, la règle taille + date ne s'applique pas."""
        un = write_photo(tmp_path / "un.jpg", size=(64, 48))
        deux = write_photo(tmp_path / "deux.jpg", size=(64, 48), color=(200, 9, 9))
        pad_to(deux, os.path.getsize(un))
        assert os.path.getsize(un) == os.path.getsize(deux)

        index = _analyse(un, deux)
        assert index.grouped_paths() == set()

    def test_trois_copies_dans_un_seul_groupe(self, tmp_path):
        """L'union-find regroupe toute la famille, pas seulement des paires."""
        original = write_photo(tmp_path / "a.jpg", dt=_DATE)
        copies = []
        for nom in ("b.jpg", "c.jpg"):
            chemin = str(tmp_path / nom)
            shutil.copy2(original, chemin)
            copies.append(chemin)

        index = _analyse(original, *copies)
        assert index.members_of(original) == {original, *copies}
        assert len(index.groups) == 1


class TestSimilaires:
    """Détection des fichiers similaires (temps proche et lieu proche)."""

    def test_meme_lieu_une_seconde_apart(self, tmp_path):
        un = write_photo(tmp_path / "un.jpg", dt=_DATE, gps=_LIEU, size=(64, 48))
        deux = write_photo(
            tmp_path / "deux.jpg",
            dt=_DATE + timedelta(seconds=1),
            gps=_LIEU,
            size=(90, 70),
            color=(10, 190, 60),
        )
        index = _analyse(un, deux)
        assert index.color_of[un] == duplicates.COLOR_SIMILAR
        assert index.color_of[deux] == duplicates.COLOR_SIMILAR
        assert index.group_of[un] == index.group_of[deux]

    def test_ecart_temporel_trop_grand(self, tmp_path):
        un = write_photo(tmp_path / "un.jpg", dt=_DATE, gps=_LIEU, size=(64, 48))
        deux = write_photo(
            tmp_path / "deux.jpg",
            dt=_DATE + timedelta(seconds=5),
            gps=_LIEU,
            size=(90, 70),
            color=(10, 190, 60),
        )
        assert _analyse(un, deux).grouped_paths() == set()

    def test_lieu_trop_eloigne(self, tmp_path):
        un = write_photo(tmp_path / "un.jpg", dt=_DATE, gps=_LIEU, size=(64, 48))
        deux = write_photo(
            tmp_path / "deux.jpg",
            dt=_DATE + timedelta(seconds=1),
            gps=_LIEU_LOIN,
            size=(90, 70),
            color=(10, 190, 60),
        )
        assert _analyse(un, deux).grouped_paths() == set()

    def test_gps_manquant(self, tmp_path):
        """Sans GPS, aucun rapprochement « similaire » n'est possible."""
        un = write_photo(tmp_path / "un.jpg", dt=_DATE, size=(64, 48))
        deux = write_photo(
            tmp_path / "deux.jpg",
            dt=_DATE + timedelta(seconds=1),
            size=(90, 70),
            color=(10, 190, 60),
        )
        assert _analyse(un, deux).grouped_paths() == set()


class TestPriorite:
    """« Identique » prime toujours sur « similaire »."""

    def test_identique_non_ecrase_par_similaire(self, tmp_path):
        original = write_photo(tmp_path / "a.jpg", dt=_DATE, gps=_LIEU)
        copie = str(tmp_path / "b.jpg")
        shutil.copy2(original, copie)
        voisine = write_photo(
            tmp_path / "c.jpg",
            dt=_DATE + timedelta(seconds=1),
            gps=_LIEU,
            size=(90, 70),
            color=(10, 190, 60),
        )

        index = _analyse(original, copie, voisine)
        assert index.color_of[original] == duplicates.COLOR_IDENTICAL
        assert index.color_of[copie] == duplicates.COLOR_IDENTICAL
        assert index.color_of[voisine] == duplicates.COLOR_SIMILAR
        assert index.group_of[original] != index.group_of[voisine]


class TestCasLimites:
    """Entrées dégénérées : l'analyse ne doit jamais lever."""

    def test_liste_vide(self):
        index = duplicates.analyze([])
        assert index.grouped_paths() == set()
        assert index.groups == {}

    def test_fichier_unique(self, tmp_path):
        seul = write_photo(tmp_path / "seul.jpg", dt=_DATE)
        assert _analyse(seul).grouped_paths() == set()

    def test_fichier_illisible(self, tmp_path):
        """Un fichier supprimé entre le scan et l'analyse est simplement ignoré."""
        absent = str(tmp_path / "disparu.jpg")
        present = write_photo(tmp_path / "present.jpg", dt=_DATE)
        index = _analyse(absent, present)
        assert present not in index.grouped_paths()

    def test_members_of_hors_groupe(self, tmp_path):
        seul = write_photo(tmp_path / "seul.jpg")
        assert _analyse(seul).members_of(seul) == set()

    def test_members_of_chemin_inconnu(self):
        assert duplicates.DupIndex().members_of("n/importe/quoi.jpg") == set()


class TestUnionFind:
    """Structure interne : correction du regroupement transitif."""

    def test_transitivite(self):
        uf = duplicates._UnionFind(["a", "b", "c", "d"])
        uf.union("a", "b")
        uf.union("b", "c")
        composants = {frozenset(v) for v in uf.components().values()}
        assert frozenset({"a", "b", "c"}) in composants
        assert frozenset({"d"}) in composants

    def test_union_idempotente(self):
        uf = duplicates._UnionFind(["a", "b"])
        uf.union("a", "b")
        uf.union("a", "b")
        assert len(uf.components()) == 1

    def test_sans_union(self):
        uf = duplicates._UnionFind(["a", "b", "c"])
        assert len(uf.components()) == 3


class TestHash:
    """Calcul du SHA-256 par blocs."""

    def test_meme_contenu_meme_empreinte(self, tmp_path):
        un, deux = tmp_path / "un.bin", tmp_path / "deux.bin"
        un.write_bytes(b"contenu identique")
        deux.write_bytes(b"contenu identique")
        assert duplicates._sha256(str(un)) == duplicates._sha256(str(deux))

    def test_contenu_different(self, tmp_path):
        un, deux = tmp_path / "un.bin", tmp_path / "deux.bin"
        un.write_bytes(b"contenu A")
        deux.write_bytes(b"contenu B")
        assert duplicates._sha256(str(un)) != duplicates._sha256(str(deux))

    def test_fichier_illisible(self, tmp_path):
        assert duplicates._sha256(str(tmp_path / "absent.bin")) is None


class TestPadToFixture:
    """Garde-fou sur l'outil de test lui-même."""

    def test_refuse_de_reduire(self, tmp_path):
        path = write_photo(tmp_path / "a.jpg")
        with pytest.raises(ValueError):
            pad_to(path, 1)
