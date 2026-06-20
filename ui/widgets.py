"""Petits widgets/icônes réutilisables de l'interface PicturIt.

- ToggleSlider : mini-curseur à deux positions (remplace les boutons « switch »).
- make_crop_icon : icône de recadrage dessinée (style barre d'outils Office).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QSlider,
    QWidget,
)


class ToggleSlider(QWidget):
    """Bascule compacte à deux états : [gauche] (•—) [droite].

    API compatible avec un bouton checkable : isChecked()/setChecked()/toggled.
    Coché (True) = position droite.
    """

    toggled = Signal(bool)

    def __init__(self, left: str, right: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._left = QLabel(left)
        self._right = QLabel(right)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 1)
        self._slider.setPageStep(1)
        self._slider.setFixedWidth(38)
        self._slider.setFixedHeight(18)
        self._slider.valueChanged.connect(self._on_value)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(5)
        layout.addWidget(self._left)
        layout.addWidget(self._slider)
        layout.addWidget(self._right)
        self._update_emphasis()

    def isChecked(self) -> bool:  # noqa: N802 (API façon QAbstractButton)
        return self._slider.value() == 1

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        self._slider.setValue(1 if checked else 0)

    def _on_value(self, value: int) -> None:
        self._update_emphasis()
        self.toggled.emit(value == 1)

    def _update_emphasis(self) -> None:
        """Met en gras le libellé du côté actif (repère visuel clair)."""
        right_active = self.isChecked()
        self._left.setStyleSheet("" if right_active else "font-weight:bold;")
        self._right.setStyleSheet("font-weight:bold;" if right_active else "")


def make_crop_icon(size: int = 20, color: QColor | None = None) -> QIcon:
    """Dessine une icône de recadrage (deux équerres opposées) adaptée au thème."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen_color = color or QApplication.palette().windowText().color()
    painter.setPen(QPen(pen_color, max(1.5, size * 0.09)))

    s = size
    # Équerre haut-gauche (coin à 0.3 ; bras vers le bas et la droite).
    painter.drawLine(int(0.30 * s), int(0.10 * s), int(0.30 * s), int(0.72 * s))
    painter.drawLine(int(0.10 * s), int(0.30 * s), int(0.72 * s), int(0.30 * s))
    # Équerre bas-droite (coin à 0.7 ; bras vers le haut et la gauche).
    painter.drawLine(int(0.70 * s), int(0.30 * s), int(0.70 * s), int(0.90 * s))
    painter.drawLine(int(0.30 * s), int(0.70 * s), int(0.90 * s), int(0.70 * s))
    painter.end()
    return QIcon(pixmap)
