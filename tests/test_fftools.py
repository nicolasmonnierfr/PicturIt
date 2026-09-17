"""Tests de ``core.fftools`` — localisation des binaires externes.

Le harnais ne suppose **pas** que ffmpeg/ffprobe sont installés : les tests
valident le contrat (« un chemin existant, ou None »), pas leur présence.
"""

from __future__ import annotations

import os
import sys

import pytest

from core import fftools


class TestLocalisation:
    """ffmpeg_path / ffprobe_path ne doivent jamais lever."""

    @pytest.mark.parametrize("fonction", ["ffmpeg_path", "ffprobe_path"])
    def test_chemin_existant_ou_none(self, fonction):
        resultat = getattr(fftools, fonction)()
        assert resultat is None or os.path.isfile(resultat)

    def test_binaire_introuvable(self, monkeypatch):
        """Sans bin/ ni PATH, la recherche renvoie None proprement."""
        monkeypatch.setattr(fftools, "_PROJECT_ROOT", "/chemin/qui/n/existe/pas")
        monkeypatch.setattr(fftools.shutil, "which", lambda _name: None)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        assert fftools.ffmpeg_path() is None

    def test_priorite_au_dossier_bin(self, monkeypatch, tmp_path):
        """Le dossier bin/ du projet passe avant le PATH système."""
        (tmp_path / "bin").mkdir()
        faux = tmp_path / "bin" / "ffmpeg.exe"
        faux.write_bytes(b"binaire factice")
        monkeypatch.setattr(fftools, "_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr(fftools.shutil, "which", lambda _name: "C:\\ailleurs.exe")

        assert fftools.ffmpeg_path() == str(faux)

    def test_repli_sur_le_path(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fftools, "_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr(fftools.shutil, "which", lambda name: f"/usr/bin/{name}")
        assert fftools.ffprobe_path() == "/usr/bin/ffprobe.exe"


class TestRun:
    """Exécution d'un sous-processus, tolérante aux échecs."""

    def test_commande_valide(self):
        """Le contrat est respecté sur un binaire réellement présent."""
        resultat = fftools.run([sys.executable, "-c", "print('bonjour')"])
        assert resultat is not None
        assert resultat.returncode == 0
        assert b"bonjour" in resultat.stdout

    def test_sortie_capturee_et_non_affichee(self):
        resultat = fftools.run(
            [sys.executable, "-c", "import sys; sys.stderr.write('oups')"]
        )
        assert resultat is not None
        assert b"oups" in resultat.stderr

    def test_code_de_retour_non_nul(self):
        """Un échec du binaire n'est pas une erreur d'exécution."""
        resultat = fftools.run([sys.executable, "-c", "raise SystemExit(3)"])
        assert resultat is not None
        assert resultat.returncode == 3

    def test_binaire_inexistant(self):
        assert fftools.run(["binaire-qui-n-existe-pas-du-tout"]) is None

    def test_depassement_de_delai(self):
        """Un binaire bloqué renvoie None au lieu de figer l'appelant."""
        resultat = fftools.run(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5
        )
        assert resultat is None
