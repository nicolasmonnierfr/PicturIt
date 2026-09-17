"""Petits widgets/icônes réutilisables de l'interface PicturIt.

- ToggleSlider : bascule à deux états, libellé de chaque côté d'un interrupteur
  dessiné — piste en pilule et poignée blanche ronde, d'après
  ``resources/switch.avif``, mais en aplats plutôt qu'en dégradé.
- make_crop_icon : icône de recadrage dessinée (style barre d'outils Office).
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QWidget,
)

# Ambre d'avertissement, identique au badge « sans GPS » de la galerie.
_WARNING_STYLE = "color:#eb911e; font-weight:bold;"

# Couleurs de la piste, en aplat. Reprises de la palette déjà en place :
# le bleu de la sélection en galerie, l'ambre du badge « sans GPS », et le gris
# des boutons d'outils pour l'état de repos.
_TRACK_ON = QColor(0x2D, 0x6C, 0xDF)
_TRACK_OFF = QColor(0x5A, 0x5F, 0x66)
_TRACK_WARN = QColor(0xEB, 0x91, 0x1E)


class _SwitchTrack(QWidget):
    """Interrupteur dessiné : piste en pilule et poignée ronde qui coulisse.

    Peint à la main plutôt que construit à partir d'un ``QSlider`` : un curseur
    standard ne donne ni la pilule à coins pleins, ni la poignée blanche ombrée
    du visuel de référence.
    """

    toggled = Signal(bool)

    _WIDTH, _HEIGHT = 42, 22
    _MARGIN = 2.5  # espace entre la poignée et le bord de la piste

    def __init__(self, warning: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._checked = False
        self._warning = warning
        self._offset = 0.0  # 0 = poignée à gauche, 1 = à droite
        self.setFixedSize(self._WIDTH, self._HEIGHT)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._animation = QPropertyAnimation(self, b"offset", self)
        self._animation.setDuration(130)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

    # --- État ---
    def isChecked(self) -> bool:  # noqa: N802 (API façon QAbstractButton)
        return self._checked

    def setChecked(self, checked: bool, *, animate: bool = True) -> None:  # noqa: N802
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        cible = 1.0 if checked else 0.0
        if animate:
            self._animation.stop()
            self._animation.setStartValue(self._offset)
            self._animation.setEndValue(cible)
            self._animation.start()
        else:
            self._set_offset(cible)
        self.toggled.emit(checked)

    # --- Propriété animable (position de la poignée) ---
    def _get_offset(self) -> float:
        return self._offset

    def _set_offset(self, valeur: float) -> None:
        self._offset = valeur
        self.update()

    offset = Property(float, _get_offset, _set_offset)

    # --- Interaction ---
    def mousePressEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802 (API Qt)
        """Accessibilité clavier : Espace et flèches basculent l'interrupteur."""
        touche = event.key()
        if touche in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.setChecked(not self._checked)
        elif touche in (Qt.Key.Key_Left, Qt.Key.Key_Home):
            self.setChecked(False)
        elif touche in (Qt.Key.Key_Right, Qt.Key.Key_End):
            self.setChecked(True)
        else:
            super().keyPressEvent(event)

    # --- Rendu ---
    def paintEvent(self, event) -> None:  # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        piste = QRectF(0, 0, self.width(), self.height())
        rayon = piste.height() / 2

        # Piste en aplat : ambre si l'état actif engage, sinon bleu à droite et
        # gris à gauche — la couleur suffit à distinguer les deux positions.
        if self._warning:
            couleur = _TRACK_WARN
        else:
            couleur = _TRACK_ON if self._offset >= 0.5 else _TRACK_OFF

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(couleur)
        painter.drawRoundedRect(piste, rayon, rayon)

        # Poignée : cercle blanc posé sur la piste, avec une ombre discrète.
        diametre = piste.height() - 2 * self._MARGIN
        course = piste.width() - diametre - 2 * self._MARGIN
        x = self._MARGIN + course * self._offset
        poignee = QRectF(x, self._MARGIN, diametre, diametre)

        painter.setBrush(QColor(0, 0, 0, 45))
        painter.drawEllipse(poignee.translated(0, 1.2))
        painter.setBrush(QColor(255, 255, 255))
        painter.drawEllipse(poignee)

        # Liseré de focus, pour que la navigation au clavier reste visible.
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(255, 255, 255, 150), 1.4))
            painter.drawRoundedRect(piste.adjusted(0.7, 0.7, -0.7, -0.7), rayon, rayon)
        painter.end()


class ToggleSlider(QWidget):
    """Bascule à deux états : [gauche] (interrupteur) [droite].

    API compatible avec un bouton checkable : isChecked()/setChecked()/toggled.
    Coché (True) = position droite.

    *warning_side* (``"left"`` ou ``"right"``) signale un état à conséquence :
    ce côté s'affiche en ambre quand il est actif, et l'interrupteur adopte la
    même teinte. Utilisé pour le mode « Remplacer », qui écrit sur le fichier
    d'origine sans retour possible.
    """

    toggled = Signal(bool)

    def __init__(
        self,
        left: str,
        right: str,
        parent: QWidget | None = None,
        warning_side: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._warning_side = warning_side
        self._left = QLabel(left)
        self._right = QLabel(right)

        self._switch = _SwitchTrack(warning=False)
        self._switch.setAccessibleName(f"{left} ou {right}")
        self._switch.toggled.connect(self._on_toggled)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(6)
        layout.addWidget(self._left)
        layout.addWidget(self._switch)
        layout.addWidget(self._right)
        self._update_emphasis()

    def isChecked(self) -> bool:  # noqa: N802 (API façon QAbstractButton)
        return self._switch.isChecked()

    def setChecked(self, checked: bool) -> None:  # noqa: N802
        self._switch.setChecked(checked)

    def setToolTip(self, texte: str) -> None:  # noqa: N802 (API Qt)
        """Applique l'infobulle aux trois éléments, pas au seul conteneur."""
        super().setToolTip(texte)
        for widget in (self._left, self._right, self._switch):
            widget.setToolTip(texte)

    def _on_toggled(self, checked: bool) -> None:
        self._update_emphasis()
        self.toggled.emit(checked)

    def _update_emphasis(self) -> None:
        """Met en valeur le libellé du côté actif, en ambre s'il est à risque."""
        right_active = self.isChecked()
        cote_actif = "right" if right_active else "left"
        for cote, label, actif in (
            ("left", self._left, not right_active),
            ("right", self._right, right_active),
        ):
            if not actif:
                label.setStyleSheet("")
            elif cote == self._warning_side:
                label.setStyleSheet(_WARNING_STYLE)
            else:
                label.setStyleSheet("font-weight:bold;")
        # L'interrupteur lui-même vire à l'ambre quand l'état actif est celui
        # qui engage : la couleur porte l'alerte, pas seulement le texte.
        self._switch._warning = cote_actif == self._warning_side
        self._switch.update()


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
