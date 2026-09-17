"""Point d'entrée de l'application PicturIt.

Lance l'application Qt et affiche la fenêtre principale.
Aucune persistance disque n'est effectuée (cf. SPEC section 2).
"""

import os
import sys

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

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


def main() -> int:
    """Crée l'application Qt et affiche la fenêtre principale."""
    app = QApplication(sys.argv)
    app.setApplicationName("PicturIt")
    app.setWindowIcon(QIcon(_resource("app.ico")))

    window = MainWindow()
    window.showMaximized()  # ouverture systématique en plein écran (fenêtre maximisée)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
