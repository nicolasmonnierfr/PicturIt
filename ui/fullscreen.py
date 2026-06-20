"""Fenêtre plein écran qui héberge le panneau d'aperçu existant.

Plutôt que de dupliquer l'UI, le plein écran **réutilise le même PreviewPanel**
(reparenté dans cette fenêtre) : on retrouve donc exactement les mêmes boutons
déjà câblés (pivoter / recadrer / convertir, switch Remplacer/Copier), le zoom
et les métadonnées. Cette fenêtre n'ajoute que la navigation ←/→ et Échap.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QVBoxLayout, QWidget


class FullScreenViewer(QWidget):
    """Conteneur plein écran ; ←/→ pour naviguer, Échap pour fermer."""

    nav = Signal(int)   # delta de navigation (-1 / +1)
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setStyleSheet("background:#1e1e1e;")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self,
                  activated=lambda: self.nav.emit(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self,
                  activated=lambda: self.nav.emit(1))
        QShortcut(QKeySequence(Qt.Key.Key_Space), self,
                  activated=lambda: self.nav.emit(1))

    def set_content(self, widget: QWidget) -> None:
        """Place (reparente) le widget d'aperçu dans la fenêtre plein écran."""
        self._layout.addWidget(widget)

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self.closed.emit()
        super().closeEvent(event)
