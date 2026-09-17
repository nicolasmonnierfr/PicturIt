"""Fenêtre plein écran qui héberge le panneau d'aperçu existant.

Plutôt que de dupliquer l'UI, le plein écran **réutilise le même PreviewPanel**
(reparenté dans cette fenêtre) : on retrouve donc exactement les mêmes boutons
déjà câblés (pivoter / recadrer / convertir, switch Remplacer/Copier), le zoom
et les métadonnées. Cette fenêtre n'ajoute que la navigation (touches ←/→ ou
boutons ‹ › flottants sur les bords) et la fermeture (Échap ou ✕).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QToolButton, QVBoxLayout, QWidget

# Marge du bouton de fermeture par rapport au coin haut-droit.
_CLOSE_MARGIN = 12

# Flèches de navigation : taille et écart par rapport aux bords latéraux.
_NAV_SIZE = 52
_NAV_MARGIN = 16


class FullScreenViewer(QWidget):
    """Conteneur plein écran ; ←/→ ou boutons ‹ › pour naviguer, Échap/✕ pour fermer."""

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

        # Flèches de défilement : les raccourcis ←/→ existaient déjà mais rien
        # ne les signalait, et la navigation était donc invisible à la souris.
        self._prev_button = self._nav_button("‹", "Média précédent (←)", -1)
        self._next_button = self._nav_button("›", "Média suivant (→)", 1)

        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self.close)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self,
                  activated=lambda: self.nav.emit(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self,
                  activated=lambda: self.nav.emit(1))
        QShortcut(QKeySequence(Qt.Key.Key_Space), self,
                  activated=lambda: self.nav.emit(1))

    def _nav_button(self, glyphe: str, infobulle: str, delta: int) -> QToolButton:
        """Flèche de navigation flottante, sur le bord gauche ou droit."""
        bouton = QToolButton(self)
        bouton.setText(glyphe)
        bouton.setToolTip(infobulle)
        bouton.setAccessibleName(infobulle)
        bouton.setFixedSize(_NAV_SIZE, _NAV_SIZE)
        bouton.setCursor(Qt.CursorShape.PointingHandCursor)
        bouton.setStyleSheet(
            "QToolButton { color:white; background:rgba(0,0,0,120); border:none;"
            f" border-radius:{_NAV_SIZE // 2}px; font-size:30px; }}"
            "QToolButton:hover { background:rgba(40,118,232,210); }"
        )
        bouton.clicked.connect(lambda: self.nav.emit(delta))
        return bouton

    def set_content(self, widget: QWidget) -> None:
        """Place (reparente) le widget d'aperçu dans la fenêtre plein écran."""
        self._layout.addWidget(widget)
        # Garder les boutons flottants au-dessus du contenu fraîchement ajouté.
        self._reposition_overlays()

    def _reposition_overlays(self) -> None:
        """Place les boutons flottants et les passe au premier plan."""
        self._close_button.move(
            self.width() - self._close_button.width() - _CLOSE_MARGIN, _CLOSE_MARGIN
        )
        milieu = (self.height() - _NAV_SIZE) // 2
        self._prev_button.move(_NAV_MARGIN, milieu)
        self._next_button.move(self.width() - _NAV_SIZE - _NAV_MARGIN, milieu)
        for bouton in (self._close_button, self._prev_button, self._next_button):
            bouton.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self._reposition_overlays()

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        self.closed.emit()
        super().closeEvent(event)
