"""Tests de la file d'attente de ``ThumbnailManager``.

C'est elle qui décide **dans quel ordre** les vignettes sont produites : ce que
l'utilisateur a sous les yeux d'abord, les vidéos en dernier. Elle existe
précisément parce qu'une tâche confiée à ``QThreadPool`` a sa priorité figée et
qu'un défilement ne pourrait plus la faire remonter.

Le pool réel est remplacé par un double qui se contente d'enregistrer l'ordre
des lancements : on teste l'ordonnancement, pas le décodage d'images. Aucune
QApplication n'est créée.
"""

from __future__ import annotations

import pytest

from core import thumbnails
from core.thumbnails import (
    PRIORITY_PHOTO,
    PRIORITY_VIDEO,
    PRIORITY_VISIBLE_PHOTO,
    PRIORITY_VISIBLE_VIDEO,
    ThumbnailManager,
    priority_for,
)


class _FauxPool:
    """Double du QThreadPool : mémorise l'ordre des lancements, n'exécute rien."""

    def __init__(self, threads: int = 4) -> None:
        self.lances: list[str] = []
        self._threads = threads

    def start(self, worker, priority=0) -> None:
        self.lances.append(worker._path)

    def maxThreadCount(self) -> int:  # noqa: N802 — API Qt
        return self._threads

    def setMaxThreadCount(self, valeur: int) -> None:  # noqa: N802 — API Qt
        self._threads = valeur


@pytest.fixture
def manager():
    """Gestionnaire dont le pool est remplacé et qui ne lance qu'une tâche à la fois.

    Un seul créneau rend l'ordre entièrement déterminé par les priorités, ce qui
    est exactement ce qu'on veut observer.
    """
    gestionnaire = ThumbnailManager()
    gestionnaire._pool = _FauxPool()
    gestionnaire._max_inflight = 1
    return gestionnaire


def _terminer(manager) -> None:
    """Simule la fin de la tâche en cours et laisse partir la suivante."""
    manager._pending.clear()
    manager._pump()


def _vider(manager, maximum: int = 50) -> None:
    for _ in range(maximum):
        if not manager._heap and not manager._waiting:
            break
        _terminer(manager)


class TestPriorites:
    """Barème : ce qui est visible prime, les vidéos ferment la marche."""

    @pytest.mark.parametrize(
        "is_video,visible,attendu",
        [
            (False, True, PRIORITY_VISIBLE_PHOTO),
            (True, True, PRIORITY_VISIBLE_VIDEO),
            (False, False, PRIORITY_PHOTO),
            (True, False, PRIORITY_VIDEO),
        ],
    )
    def test_bareme(self, is_video, visible, attendu):
        assert priority_for(is_video, visible) == attendu

    def test_ordre_strict(self):
        assert (
            PRIORITY_VISIBLE_PHOTO
            > PRIORITY_VISIBLE_VIDEO
            > PRIORITY_PHOTO
            > PRIORITY_VIDEO
        )


class TestOrdreDeService:
    """La file sert toujours la demande la plus prioritaire disponible."""

    def test_photos_avant_videos(self, manager):
        """Parmi les demandes **en attente**, les photos passent devant.

        La première demande occupe aussitôt le créneau libre : elle ne peut pas
        être devancée, puisqu'on ne réordonne jamais une tâche déjà confiée au
        pool. C'est l'ordre des suivantes que la file décide.
        """
        manager.request("amorce.jpg", False, 160)  # occupe le seul créneau
        manager.request("clip.mp4", True, 160)
        manager.request("photo.jpg", False, 160)
        _vider(manager)
        assert manager._pool.lances == ["amorce.jpg", "photo.jpg", "clip.mp4"]

    def test_ordre_d_arrivee_a_priorite_egale(self, manager):
        for i in range(4):
            manager.request(f"p{i}.jpg", False, 160)
        _vider(manager)
        assert manager._pool.lances == ["p0.jpg", "p1.jpg", "p2.jpg", "p3.jpg"]

    def test_le_visible_double_la_file(self, manager):
        for i in range(6):
            manager.request(f"p{i}.jpg", False, 160)
        # p0 est déjà parti ; l'utilisateur fait défiler jusqu'à p4/p5.
        manager.prioritize(["p4.jpg", "p5.jpg"], PRIORITY_VISIBLE_PHOTO)
        _terminer(manager)
        _terminer(manager)
        assert manager._pool.lances[:3] == ["p0.jpg", "p4.jpg", "p5.jpg"]

    def test_video_visible_avant_photo_hors_champ(self, manager):
        manager.request("clip.mp4", True, 160)
        manager.request("photo.jpg", False, 160)
        manager.prioritize(["clip.mp4"], PRIORITY_VISIBLE_VIDEO)
        _vider(manager)
        assert manager._pool.lances == ["clip.mp4", "photo.jpg"]


class TestRienNEstAbandonne:
    """La carte et les statistiques ont besoin de **tous** les médias."""

    def test_tout_finit_par_etre_traite(self, manager):
        demandes = [f"p{i}.jpg" for i in range(10)] + ["v.mp4"]
        for chemin in demandes:
            manager.request(chemin, chemin.endswith(".mp4"), 160)
        manager.prioritize(["p7.jpg"], PRIORITY_VISIBLE_PHOTO)
        _vider(manager)
        assert sorted(manager._pool.lances) == sorted(demandes)

    def test_aucun_doublon(self, manager):
        for i in range(5):
            manager.request(f"p{i}.jpg", False, 160)
        manager.prioritize(["p3.jpg"], PRIORITY_VISIBLE_PHOTO)
        manager.prioritize(["p3.jpg"], PRIORITY_VISIBLE_PHOTO)
        _vider(manager)
        assert len(manager._pool.lances) == len(set(manager._pool.lances))


class TestRepriorisation:
    """``prioritize`` ne doit agir que sur ce qui n'est pas encore lancé."""

    def test_sans_effet_sur_une_tache_en_cours(self, manager):
        manager.request("p0.jpg", False, 160)
        assert manager._pool.lances == ["p0.jpg"]
        manager.prioritize(["p0.jpg"], PRIORITY_VISIBLE_PHOTO)
        assert manager._pool.lances == ["p0.jpg"], "pas de relance"

    def test_chemin_inconnu_ignore(self, manager):
        manager.request("p0.jpg", False, 160)
        manager.prioritize(["jamais_demande.jpg"], PRIORITY_VISIBLE_PHOTO)
        _vider(manager)
        assert manager._pool.lances == ["p0.jpg"]

    def test_ne_retrograde_jamais(self, manager):
        for i in range(3):
            manager.request(f"p{i}.jpg", False, 160)
        manager.prioritize(["p2.jpg"], PRIORITY_VISIBLE_PHOTO)
        manager.prioritize(["p2.jpg"], PRIORITY_VIDEO)  # priorité plus basse
        _terminer(manager)
        assert manager._pool.lances[1] == "p2.jpg", "la priorité haute doit tenir"

    def test_liste_vide(self, manager):
        manager.request("p0.jpg", False, 160)
        manager.prioritize([], PRIORITY_VISIBLE_PHOTO)  # ne doit pas lever


class TestLimiteDeTachesSimultanees:
    """On ne confie au pool que ce qu'il traite de front."""

    def test_respect_du_plafond(self):
        gestionnaire = ThumbnailManager()
        gestionnaire._pool = _FauxPool()
        gestionnaire._max_inflight = 3
        for i in range(10):
            gestionnaire.request(f"p{i}.jpg", False, 160)
        assert len(gestionnaire._pool.lances) == 3
        assert len(gestionnaire._waiting) == 7

    def test_un_creneau_libere_relance(self):
        gestionnaire = ThumbnailManager()
        gestionnaire._pool = _FauxPool()
        gestionnaire._max_inflight = 2
        for i in range(5):
            gestionnaire.request(f"p{i}.jpg", False, 160)
        assert len(gestionnaire._pool.lances) == 2

        gestionnaire._pending.discard("p0.jpg")
        gestionnaire._pump()
        assert len(gestionnaire._pool.lances) == 3


class TestDemandesRedondantes:
    """Une même vignette ne doit pas être demandée deux fois."""

    def test_deuxieme_demande_ignoree(self, manager):
        manager.request("p0.jpg", False, 160)
        manager.request("p0.jpg", False, 160)
        _vider(manager)
        assert manager._pool.lances == ["p0.jpg"]

    def test_demande_pendant_l_attente(self):
        gestionnaire = ThumbnailManager()
        gestionnaire._pool = _FauxPool()
        gestionnaire._max_inflight = 1
        gestionnaire.request("p0.jpg", False, 160)
        gestionnaire.request("p1.jpg", False, 160)
        gestionnaire.request("p1.jpg", False, 160)  # déjà en file
        assert len(gestionnaire._waiting) == 1


class TestVidage:
    """Changer de dossier remet tout à zéro."""

    def test_clear_vide_la_file(self, manager):
        for i in range(5):
            manager.request(f"p{i}.jpg", False, 160)
        manager.clear()
        assert manager._waiting == {}
        assert manager._heap == []

    def test_clear_reinitialise_la_strategie(self, manager):
        for _ in range(thumbnails._HeaderStrategy._ECHANTILLON):
            thumbnails.HEADER_STRATEGY.record(False)
        assert thumbnails.HEADER_STRATEGY.should_try() is False
        manager.clear()
        assert thumbnails.HEADER_STRATEGY.should_try() is True


class TestArret:
    """Fermeture de l'application : les workers ne doivent plus rien émettre.

    Qt détruit les objets récepteurs pendant que des workers tournent encore.
    La fenêtre ne peut pas se contenter de les attendre : sur un partage
    réseau, plusieurs dizaines de lectures de centaines de millisecondes sont
    en vol. Sans ce garde-fou, la fermeture crachait des « Signal source has
    been deleted ».
    """

    @pytest.fixture(autouse=True)
    def _drapeau_neuf(self):
        thumbnails._shutting_down.clear()
        yield
        thumbnails._shutting_down.clear()

    def test_drapeau_baisse_par_defaut(self):
        assert thumbnails.is_shutting_down() is False

    def test_request_shutdown_leve_le_drapeau(self):
        thumbnails.request_shutdown()
        assert thumbnails.is_shutting_down() is True

    def test_le_worker_renonce(self):
        """Un worker lancé après la demande d'arrêt n'émet rien."""
        worker = thumbnails._ThumbnailWorker("photo.jpg", False, 160)
        recus = []
        worker.signals.finished.connect(lambda *a: recus.append(a))
        worker.signals.failed.connect(lambda *a: recus.append(a))

        thumbnails.request_shutdown()
        worker.run()
        assert recus == []

    def test_le_worker_emet_normalement_sinon(self, tmp_path):
        """Hors arrêt, un fichier illisible produit bien un signal d'échec."""
        chemin = tmp_path / "corrompu.jpg"
        chemin.write_bytes(b"pas une image")
        worker = thumbnails._ThumbnailWorker(str(chemin), False, 160)
        recus = []
        worker.signals.failed.connect(lambda *a: recus.append(a))

        worker.run()
        assert len(recus) == 1


class TestDimensionnementDuPool:
    """Le nombre de threads vise l'attente réseau, pas le calcul."""

    def test_deux_fois_les_coeurs(self, monkeypatch):
        monkeypatch.delenv("PICTURIT_THREADS", raising=False)
        monkeypatch.setattr(thumbnails.os, "cpu_count", lambda: 8)
        assert thumbnails._pool_threads() == 16

    def test_plafonne(self, monkeypatch):
        monkeypatch.delenv("PICTURIT_THREADS", raising=False)
        monkeypatch.setattr(thumbnails.os, "cpu_count", lambda: 64)
        assert thumbnails._pool_threads() == 32

    def test_plancher(self, monkeypatch):
        monkeypatch.delenv("PICTURIT_THREADS", raising=False)
        monkeypatch.setattr(thumbnails.os, "cpu_count", lambda: 1)
        assert thumbnails._pool_threads() == 8

    def test_cpu_count_indisponible(self, monkeypatch):
        monkeypatch.delenv("PICTURIT_THREADS", raising=False)
        monkeypatch.setattr(thumbnails.os, "cpu_count", lambda: None)
        assert thumbnails._pool_threads() == 8

    @pytest.mark.parametrize("valeur,attendu", [("4", 4), ("48", 48), ("999", 64)])
    def test_reglage_manuel(self, monkeypatch, valeur, attendu):
        monkeypatch.setenv("PICTURIT_THREADS", valeur)
        assert thumbnails._pool_threads() == attendu

    @pytest.mark.parametrize("valeur", ["0", "-2", "beaucoup", ""])
    def test_reglage_invalide_ignore(self, monkeypatch, valeur):
        monkeypatch.setenv("PICTURIT_THREADS", valeur)
        monkeypatch.setattr(thumbnails.os, "cpu_count", lambda: 8)
        assert thumbnails._pool_threads() == 16
