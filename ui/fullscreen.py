"""Fenêtre plein écran qui héberge le panneau d'aperçu existant.

Plutôt que de dupliquer l'UI, le plein écran **réutilise le même PreviewPanel**
(reparenté dans cette fenêtre) : on retrouve donc exactement les mêmes boutons
déjà câblés (pivoter / recadrer / convertir, switch Remplacer/Copier), le zoom
et les métadonnées. Cette fenêtre n'ajoute que la navigation ←/→ et Échap.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QToolButton, QVBoxLayout, QWidget

# Marge du bouton de fermeture par rapport au coin haut-droit.
_CLOSE_MARGIN = 12


class FullScreenViewer(QWidget):
    """Conteneur plein écran ; ←/→ pour naviguer, Échap ou ✕ pour fermer."""

    nav = Signal(int)   # delta de navigation (-1 / +1)
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Window)
        self.setStyleSheet("background:#1e1e1e;")
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)

        # Bouton de fermeture flottant (pas de barre de titre en plein écran) :
        # enfant direct de la fenêtre, positionné par-dessus le contenu.
        self._close_button = QToolButton(self)
        self._close_button.setText("✕")
        self._close_button.setToolTip("Fermer le plein écran (Échap)")
        self._close_button.setAccessibleName("Fermer le plein écran")
        self._close_button.setFixedSize(36, 36)
        self._close_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._close_button.setStyleSheet(
            "QToolButton { color:white; background:rgba(0,0,0,140); border:none;"
            " border-radius:18px; font-size:18px; }"
            "QToolButton:hover { background:rgba(200,40,40,210); }"
        )
        self._close_button.clicked.connect(self.close)

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
        # Garder le bouton ✕ au-dessus du contenu fraîchement ajouté.
        self._reposition_close()

    def _reposition_close(self) -> None:
        """Place le bouton ✕ en haut à droite et le passe au premier plan."""
        self._close_button.move(
            self.width() - self._close_button.width() - _CLOSE_MARGIN, _CLOSE_MARGIN
        )
        self._close_button.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self._reposition_close()

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self.closed.emit()
        super().closeEvent(event)
