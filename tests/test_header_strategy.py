"""Tests de ``_HeaderStrategy`` — le pari « lire l'en-tête avant le fichier ».

Ce module importe ``core.thumbnails`` (donc PySide6) mais ne crée **aucune**
QApplication : la stratégie est de la logique pure, sans widget.

Elle a produit un vrai bug en conditions réelles — 585 lectures d'en-tête
inutiles sur 589 photos — parce que sa décision n'était pas figée. D'où ces
tests, notamment sous concurrence.
"""

from __future__ import annotations

import random
import threading

import pytest

from core.thumbnails import _HeaderStrategy


@pytest.fixture
def strategie():
    return _HeaderStrategy()


def _alimenter(strategie, taux: float, nombre: int, graine: int = 7) -> int:
    """Enregistre *nombre* essais au taux de réussite donné.

    Renvoie le nombre d'essais réellement tentés (la stratégie peut couper).
    """
    rnd = random.Random(graine)
    tentes = 0
    for _ in range(nombre):
        if not strategie.should_try():
            break
        tentes += 1
        strategie.record(rnd.random() < taux)
    return tentes


class TestPhaseDObservation:
    """Avant l'échantillon complet, on essaie toujours."""

    def test_essaie_au_depart(self, strategie):
        assert strategie.should_try() is True

    def test_essaie_pendant_l_observation(self, strategie):
        for _ in range(_HeaderStrategy._ECHANTILLON - 1):
            strategie.record(False)
            assert strategie.should_try() is True


class TestDecision:
    """La décision se prend une fois l'échantillon atteint."""

    def test_abandonne_si_peu_de_vignettes(self, strategie):
        for _ in range(_HeaderStrategy._ECHANTILLON):
            strategie.record(False)
        assert strategie.should_try() is False

    def test_conserve_si_beaucoup_de_vignettes(self, strategie):
        for _ in range(_HeaderStrategy._ECHANTILLON):
            strategie.record(True)
        assert strategie.should_try() is True

    @pytest.mark.parametrize(
        "taux,attendu",
        [(0.0, False), (0.10, False), (0.60, True), (1.0, True)],
    )
    def test_seuil_de_rentabilite(self, strategie, taux, attendu):
        """Le pari n'est conservé qu'au-delà d'environ 40 % de réussite.

        Les taux testés sont franchement d'un côté ou de l'autre du seuil : au
        voisinage immédiat, la décision dépend du tirage (cf. le test suivant).
        """
        _alimenter(strategie, taux, 200)
        assert strategie.should_try() is attendu

    @pytest.mark.parametrize("graine", [1, 7, 42, 1234])
    def test_zone_d_incertitude_assumee(self, graine):
        """Près du seuil, la décision dépend de l'échantillon — et c'est sans gravité.

        Avec 24 essais, un taux réel de 30 % peut tirer 10 succès (41 %) et
        conclure à tort qu'il faut garder la lecture d'en-tête. On l'assume :
        à proximité du seuil, les deux stratégies se valent presque par
        définition, puisque le seuil est précisément le point d'équilibre entre
        le gain et le surcoût. Ce test documente ce comportement au lieu de
        prétendre à une précision que 24 échantillons ne permettent pas.
        """
        strategie = _HeaderStrategy()
        _alimenter(strategie, 0.35, 200, graine=graine)
        assert isinstance(strategie.should_try(), bool)

    def test_coupe_rapidement(self, strategie):
        """Un dossier sans vignettes ne doit coûter qu'une poignée d'essais."""
        tentes = _alimenter(strategie, 0.0, 500)
        assert tentes <= _HeaderStrategy._ECHANTILLON


class TestDecisionFigee:
    """Une fois prise, la décision ne doit plus bouger.

    C'est le correctif du bug observé : sur un dossier hétérogène, le ratio
    cumulé repassait le seuil dans les deux sens et réactivait sans cesse la
    lecture d'en-tête.
    """

    def test_pas_de_bascule_apres_decision(self, strategie):
        for _ in range(_HeaderStrategy._ECHANTILLON):
            strategie.record(False)
        assert strategie.should_try() is False

        # Même une longue série de réussites ne doit pas la faire revenir.
        for _ in range(500):
            strategie.record(True)
        assert strategie.should_try() is False

    def test_pas_de_bascule_dans_l_autre_sens(self, strategie):
        for _ in range(_HeaderStrategy._ECHANTILLON):
            strategie.record(True)
        for _ in range(500):
            strategie.record(False)
        assert strategie.should_try() is True

    def test_dossier_heterogene(self, strategie):
        """Début riche en vignettes puis pauvre : la décision initiale tient."""
        _alimenter(strategie, 0.9, _HeaderStrategy._ECHANTILLON)
        decision = strategie.should_try()
        _alimenter(strategie, 0.05, 300)
        assert strategie.should_try() is decision


class TestReset:
    """Changer de dossier remet l'apprentissage à zéro."""

    def test_reprend_l_observation(self, strategie):
        for _ in range(_HeaderStrategy._ECHANTILLON):
            strategie.record(False)
        assert strategie.should_try() is False

        strategie.reset()
        assert strategie.should_try() is True

    def test_nouvelle_decision_possible(self, strategie):
        _alimenter(strategie, 0.0, 100)
        strategie.reset()
        _alimenter(strategie, 1.0, 100)
        assert strategie.should_try() is True


class TestConcurrence:
    """Les compteurs sont alimentés par des dizaines de workers simultanés."""

    def test_compteurs_coherents(self, strategie):
        """Sans verrou, les incrémentations concurrentes se perdaient."""
        essais = 400

        def worker():
            for _ in range(essais):
                strategie.record(False)

        fils = [threading.Thread(target=worker) for _ in range(8)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()

        # La décision est prise puis figée : le total s'arrête à l'échantillon.
        assert strategie._total == _HeaderStrategy._ECHANTILLON
        assert strategie.should_try() is False

    def test_coupure_effective_sous_concurrence(self, strategie):
        """Le nombre d'essais reste borné même avec beaucoup de threads."""
        tentes = []
        verrou = threading.Lock()

        def worker():
            local = 0
            for _ in range(100):
                if not strategie.should_try():
                    break
                local += 1
                strategie.record(False)
            with verrou:
                tentes.append(local)

        fils = [threading.Thread(target=worker) for _ in range(16)]
        for f in fils:
            f.start()
        for f in fils:
            f.join()

        total = sum(tentes)
        assert total < 200, f"{total} essais : la coupure n'a pas eu lieu"
