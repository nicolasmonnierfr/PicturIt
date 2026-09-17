"""Point d'entrée de l'application PicturIt.

Lance l'application Qt et affiche la fenêtre principale.

Aucune écriture disque, **sauf** si la journalisation de diagnostic est demandée
explicitement (``--log`` / ``--log-perf``, cf. ``core.logs``) : c'est la seule
entorse assumée à la règle « zéro persistance » (SPEC 2), et elle est débrayée
par défaut.
"""

import argparse
import logging
import os
import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from core import logs

# QtWebEngine (carte Leaflet) requiert le partage des contextes OpenGL ;
# l'attribut doit être posé AVANT la création de QApplication.
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

# Import volontairement tardif : il entraîne la création de widgets QtWebEngine
# et doit donc suivre l'attribut ci-dessus (E402 neutralisé dans ruff.toml).
from ui.main_window import MainWindow


def _resource(name: str) -> str:
    """Chemin d'une ressource : bundle PyInstaller (_MEIPASS) ou racine projet."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "resources", name)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    """Options de diagnostic. Sans option, l'application n'écrit rien sur disque."""
    parser = argparse.ArgumentParser(
        prog="PicturIt",
        description="Trieur de photos et vidéos.",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--log",
        action="store_true",
        help="journaliser l'activité dans un fichier (désactivé par défaut)",
    )
    parser.add_argument(
        "--log-perf",
        action="store_true",
        help="journaliser en détail, avec les mesures de performance",
    )
    parser.add_argument(
        "--log-console",
        action="store_true",
        help="afficher aussi le journal sur la sortie d'erreur",
    )
    # Les arguments inconnus sont laissés à Qt (ex. -platform offscreen).
    return parser.parse_known_args(argv[1:])[0]


def main() -> int:
    """Crée l'application Qt et affiche la fenêtre principale."""
    options = _parse_args(sys.argv)

    # Journalisation : ligne de commande prioritaire, sinon variable
    # d'environnement, sinon rien du tout (aucun fichier créé).
    niveau = None
    if options.log_perf:
        niveau = logging.DEBUG
    elif options.log:
        niveau = logging.INFO
    if logs.configure(niveau, to_console=options.log_console):
        logs.get_logger().info("Démarrage de PicturIt")

    app = QApplication(sys.argv)
    app.setApplicationName("PicturIt")
    app.setWindowIcon(QIcon(_resource("app.ico")))

    window = MainWindow()
    window.showMaximized()  # ouverture systématique en plein écran (fenêtre maximisée)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
