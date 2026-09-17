"""Tests de ``core.logs`` — la journalisation doit rester **débrayée** par défaut.

C'est la seule entorse à la règle « zéro persistance » (SPEC 2), et elle n'est
tolérée que parce qu'elle est explicitement demandée. Un défaut ici ferait
écrire l'application dans le dos de l'utilisateur : d'où des tests qui vérifient
autant ce qui **n'est pas** créé que ce qui l'est.
"""

from __future__ import annotations

import logging

import pytest

from core import logs


@pytest.fixture(autouse=True)
def _isoler_journalisation(monkeypatch, tmp_path):
    """Remet le module à neuf : état global, handlers et variables d'environnement."""
    logger = logging.getLogger(logs.LOGGER_NAME)

    def _detacher():
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()

    _detacher()
    monkeypatch.setattr(logs, "_configured", False)
    monkeypatch.setattr(logs, "_perf_enabled", False)
    monkeypatch.delenv("PICTURIT_LOG", raising=False)
    # Par défaut, tout log éventuel atterrit dans le tmp_path du test, jamais
    # dans le vrai %LOCALAPPDATA% de la machine qui exécute la suite.
    monkeypatch.setenv("PICTURIT_LOG_FILE", str(tmp_path / "picturit.log"))
    yield
    _detacher()


@pytest.fixture
def journal(tmp_path):
    """Chemin du fichier de log utilisé par les tests."""
    return tmp_path / "picturit.log"


class TestDesactiveParDefaut:
    """Sans demande explicite, l'application n'écrit **rien**."""

    def test_configure_sans_argument_ne_fait_rien(self, journal):
        assert logs.configure() is False
        assert not journal.exists()

    @pytest.mark.parametrize("valeur", ["", "0", "false", "off", "no"])
    def test_valeurs_qui_desactivent(self, monkeypatch, journal, valeur):
        monkeypatch.setenv("PICTURIT_LOG", valeur)
        assert logs.configure() is False
        assert not journal.exists()

    def test_aucun_fichier_cree(self, journal):
        logs.configure()
        logs.get_logger("test").info("message ignoré")
        assert not journal.exists()

    def test_mesures_de_perf_inactives(self):
        logs.configure()
        assert logs.perf_enabled() is False


class TestActivation:
    """Activation par variable d'environnement ou par niveau explicite."""

    @pytest.mark.parametrize(
        "valeur,niveau",
        [
            ("1", logging.INFO),
            ("true", logging.INFO),
            ("info", logging.INFO),
            ("debug", logging.DEBUG),
            ("perf", logging.DEBUG),
            ("warning", logging.WARNING),
            ("error", logging.ERROR),
        ],
    )
    def test_niveaux_reconnus(self, monkeypatch, valeur, niveau):
        monkeypatch.setenv("PICTURIT_LOG", valeur)
        assert logs.configure() is True
        assert logging.getLogger(logs.LOGGER_NAME).level == niveau

    def test_valeur_inconnue_retombe_sur_info(self, monkeypatch):
        monkeypatch.setenv("PICTURIT_LOG", "n'importe quoi")
        assert logs.configure() is True
        assert logging.getLogger(logs.LOGGER_NAME).level == logging.INFO

    def test_niveau_explicite_prioritaire(self, monkeypatch):
        """La ligne de commande l'emporte sur l'environnement."""
        monkeypatch.setenv("PICTURIT_LOG", "error")
        logs.configure(logging.DEBUG)
        assert logging.getLogger(logs.LOGGER_NAME).level == logging.DEBUG

    def test_perf_active_uniquement_en_debug(self, monkeypatch):
        logs.configure(logging.INFO)
        assert logs.perf_enabled() is False
        monkeypatch.setattr(logs, "_configured", False)
        logs.configure(logging.DEBUG)
        assert logs.perf_enabled() is True


class TestEcriture:
    """Une fois activée, la journalisation écrit bien dans le fichier."""

    def test_fichier_cree_et_alimente(self, journal):
        logs.configure(logging.INFO)
        logs.get_logger("essai").info("bonjour %s", "monde")
        for handler in logging.getLogger(logs.LOGGER_NAME).handlers:
            handler.flush()

        assert journal.exists()
        contenu = journal.read_text(encoding="utf-8")
        assert "bonjour monde" in contenu
        assert "Journalisation active" in contenu

    def test_niveau_filtre_les_messages(self, journal):
        logs.configure(logging.WARNING)
        logs.get_logger("essai").debug("ne doit pas apparaître")
        logs.get_logger("essai").warning("doit apparaître")
        for handler in logging.getLogger(logs.LOGGER_NAME).handlers:
            handler.flush()

        contenu = journal.read_text(encoding="utf-8")
        assert "ne doit pas apparaître" not in contenu
        assert "doit apparaître" in contenu

    def test_dossier_parent_cree(self, monkeypatch, tmp_path):
        """Le dossier %LOCALAPPDATA%\\PicturIt n'existe pas au premier lancement."""
        cible = tmp_path / "absent" / "encore" / "picturit.log"
        monkeypatch.setenv("PICTURIT_LOG_FILE", str(cible))
        logs.configure(logging.INFO)
        assert cible.exists()

    def test_echec_d_ecriture_non_fatal(self, monkeypatch, tmp_path):
        """Disque plein ou droits manquants : l'application continue sans journal."""
        monkeypatch.setenv("PICTURIT_LOG_FILE", str(tmp_path / "x.log"))

        def _refuser(*_args, **_kwargs):
            raise OSError("accès refusé")

        monkeypatch.setattr(logs.os, "makedirs", _refuser)
        assert logs.configure(logging.INFO) is True  # activée malgré tout
        logs.get_logger().info("ne doit pas faire planter")


class TestIdempotence:
    """Configurer deux fois ne doit pas empiler les handlers."""

    def test_pas_de_doublon_de_handler(self):
        logs.configure(logging.INFO)
        nombre = len(logging.getLogger(logs.LOGGER_NAME).handlers)
        logs.configure(logging.INFO)
        assert len(logging.getLogger(logs.LOGGER_NAME).handlers) == nombre

    def test_second_appel_renvoie_true(self):
        logs.configure(logging.INFO)
        assert logs.configure(logging.INFO) is True


class TestCheminDuJournal:
    """Emplacement du fichier."""

    def test_variable_d_environnement_prioritaire(self, monkeypatch, tmp_path):
        impose = str(tmp_path / "ailleurs.log")
        monkeypatch.setenv("PICTURIT_LOG_FILE", impose)
        assert logs.default_log_path() == impose

    def test_defaut_sous_localappdata(self, monkeypatch, tmp_path):
        """Jamais à côté de l'exécutable : Program Files est en lecture seule."""
        monkeypatch.delenv("PICTURIT_LOG_FILE", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        chemin = logs.default_log_path()
        assert chemin.startswith(str(tmp_path))
        assert chemin.endswith("picturit.log")
        assert "PicturIt" in chemin


class TestGetLogger:
    """Nommage hiérarchique des loggers."""

    def test_racine(self):
        assert logs.get_logger().name == logs.LOGGER_NAME

    def test_avec_suffixe(self):
        assert logs.get_logger("image").name == f"{logs.LOGGER_NAME}.image"

    def test_les_enfants_heritent_des_handlers(self, journal):
        logs.configure(logging.INFO)
        logs.get_logger("perf").info("issu d'un sous-logger")
        for handler in logging.getLogger(logs.LOGGER_NAME).handlers:
            handler.flush()
        assert "issu d'un sous-logger" in journal.read_text(encoding="utf-8")
