"""Vérification à froid : l'interface complète se construit-elle encore ?

Le harnais ``tests/`` ne couvre que ``core/`` (sans Qt). Ce script complète en
instanciant réellement la fenêtre principale — donc tous les panneaux et le
câblage des signaux — dans un environnement sans écran.

Il rattrape les erreurs qu'un test de logique métier ne peut pas voir : import
cassé, signal connecté à un slot disparu, widget renommé.

Usage (local ou CI) :

    .\\.venv\\Scripts\\python.exe scripts/verifier_ui.py

Sortie : code 0 si la fenêtre se construit, 1 sinon (avec la trace).
"""

from __future__ import annotations

import os
import sys

# Rend le projet importable quel que soit le dossier d'appel.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Aucun écran en CI : rendu hors écran.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, Qt

# QtWebEngine (carte Leaflet) exige cet attribut AVANT QApplication.
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.main_window import MainWindow  # noqa: E402


def _verifier(condition: bool, message: str) -> None:
    """Contrôle explicite (pas d'``assert`` : il disparaît avec ``python -O``)."""
    if not condition:
        raise SystemExit(f"ECHEC : {message}")


def main() -> int:
    app = QApplication([])
    window = MainWindow()

    # Quelques points de câblage sensibles, vérifiés sans interaction.
    _verifier(window.gallery_view is not None, "galerie absente")
    _verifier(window.map_panel is not None, "panneau carte absent")
    _verifier(
        window.preview_panel.current_path() is None,
        "l'aperçu ne devrait afficher aucun média au démarrage",
    )
    _verifier(
        window.gallery_view.selected_paths() == [],
        "la galerie ne devrait rien avoir de sélectionné au démarrage",
    )
    _verifier(
        window.gallery_view.geo_points() == [],
        "aucun point GPS ne devrait exister avant le chargement d'un dossier",
    )

    print("OK : MainWindow et ses panneaux se construisent.")
    del window
    app.quit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
