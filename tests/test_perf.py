"""Tests de ``core.perf`` — les mesures ne doivent rien coûter quand elles dorment.

L'instrumentation est disséminée dans des chemins très chauds (une fois par
photo, parfois plusieurs). Deux exigences : ne rien faire du tout tant que la
journalisation n'est pas en DEBUG, et ne jamais laisser une exception remonter
depuis une mesure — un outil de diagnostic ne doit pas casser ce qu'il observe.
"""

from __future__ import annotations

import logging

import pytest

from core import logs, perf


@pytest.fixture(autouse=True)
def _agregats_neufs():
    perf.reset()
    yield
    perf.reset()


@pytest.fixture
def actif(monkeypatch):
    """Active les mesures sans écrire de fichier (le logger n'a pas de handler)."""
    monkeypatch.setattr(logs, "_perf_enabled", True)
    logging.getLogger(logs.LOGGER_NAME).setLevel(logging.DEBUG)


@pytest.fixture
def inactif(monkeypatch):
    monkeypatch.setattr(logs, "_perf_enabled", False)


class TestDesactive:
    """Sans journalisation en DEBUG, rien n'est collecté."""

    def test_enabled_suit_les_logs(self, inactif):
        assert perf.enabled() is False

    def test_measure_ne_collecte_rien(self, inactif):
        with perf.measure("essai"):
            pass
        assert perf.snapshot() == []

    def test_step_ne_collecte_rien(self, inactif):
        with perf.step("essai"):
            pass
        assert perf.snapshot() == []

    def test_report_silencieux(self, inactif):
        with perf.measure("essai"):
            pass
        perf.report("bilan")  # ne doit rien lever

    def test_note_silencieuse(self, inactif):
        perf.note("message %d", 42)


class TestCollecte:
    """Une fois actives, les mesures s'agrègent."""

    def test_measure_compte_les_appels(self, actif):
        for _ in range(3):
            with perf.measure("operation"):
                pass
        (nom, appels, total, maximum), = perf.snapshot()
        assert nom == "operation"
        assert appels == 3
        assert total >= 0
        assert maximum >= 0

    def test_step_compte_aussi(self, actif):
        with perf.step("scan"):
            pass
        assert perf.snapshot()[0][0] == "scan"

    def test_operations_distinctes(self, actif):
        with perf.measure("a"):
            pass
        with perf.measure("b"):
            pass
        assert {ligne[0] for ligne in perf.snapshot()} == {"a", "b"}

    def test_tri_par_temps_cumule(self, actif, monkeypatch):
        """Le bilan doit montrer les postes les plus coûteux en premier."""
        perf._add("rapide", 1.0)
        perf._add("lent", 100.0)
        perf._add("moyen", 10.0)
        assert [ligne[0] for ligne in perf.snapshot()] == ["lent", "moyen", "rapide"]

    def test_maximum_conserve(self, actif):
        perf._add("op", 5.0)
        perf._add("op", 50.0)
        perf._add("op", 2.0)
        (_, appels, total, maximum), = perf.snapshot()
        assert appels == 3
        assert maximum == pytest.approx(50.0)
        assert total == pytest.approx(57.0)


class TestRobustesse:
    """Une mesure ne doit jamais masquer ni provoquer d'erreur."""

    def test_exception_propagee(self, actif):
        """L'erreur de l'opération observée doit remonter intacte."""
        with pytest.raises(ValueError, match="panne"), perf.measure("op"):
            raise ValueError("panne")

    def test_mesure_enregistree_malgre_l_exception(self, actif):
        with pytest.raises(ValueError), perf.measure("op"):
            raise ValueError("panne")
        assert perf.snapshot()[0][1] == 1

    def test_exception_propagee_hors_mesure(self, inactif):
        with pytest.raises(ValueError), perf.measure("op"):
            raise ValueError("panne")

    def test_reset_vide_tout(self, actif):
        with perf.measure("op"):
            pass
        perf.reset()
        assert perf.snapshot() == []


class TestReport:
    """Le bilan s'écrit dans le journal, sans jamais lever."""

    def test_sans_mesure(self, actif):
        perf.report("vide")

    def test_avec_mesures(self, actif, caplog):
        perf._add("operation", 12.5)
        with caplog.at_level(logging.DEBUG, logger=f"{logs.LOGGER_NAME}.perf"):
            perf.report("Bilan")
        texte = caplog.text
        assert "Bilan" in texte
        assert "operation" in texte
