"""Colonne droite — Panneau bascule Aperçu / Carte.

Mode Aperçu :
- grande image **zoomable et déplaçable** (molette = zoom, glisser = déplacer,
  double-clic = réajuster) — sans aucune modification du fichier ;
- OU lecteur vidéo (QtMultimedia) avec barre de contrôle complète : retour
  au début, ±10 s, lecture/pause, arrêt, progression déplaçable et volume ;
- sous le média : métadonnées (nom, chemin, date, dimensions, taille, GPS).

Mode Carte : conteneur accueillant le MapPanel (injecté par la fenêtre).

Le lecteur vidéo dépend des codecs présents ; tout échec est géré sans planter
(message « lecture impossible », cf. SPEC 6.5).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QRect, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QRubberBand,
    QSlider,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core import metadata
from core.scanner import VIDEO_EXTENSIONS
from core.thumbnails import load_qimage
from ui.widgets import ToggleSlider, make_crop_icon


def _fmt_size(num: int) -> str:
    value = float(num)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} Go"


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "—"
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _fmt_datetime(value) -> str:
    return value.strftime("%d/%m/%Y %H:%M:%S") if value else "—"


# Libellés de temps du lecteur vidéo : discrets mais lisibles sur fond sombre.
_VIDEO_TIME_STYLE = "color:#d0d0d0; font-size:11px; min-width:38px;"


def _fmt_position(ms: int) -> str:
    """Formate une position de lecture : m:ss, ou h:mm:ss au-delà d'une heure."""
    total = max(0, int(ms // 1000))
    heures, reste = divmod(total, 3600)
    minutes, secondes = divmod(reste, 60)
    if heures:
        return f"{heures}:{minutes:02d}:{secondes:02d}"
    return f"{minutes}:{secondes:02d}"


# Style des boutons de la barre d'édition : rendu lisible **y compris** en plein
# écran (le fond sombre de la fenêtre plein écran se propageait aux boutons, qui
# devenaient invisibles). S'applique aux QPushButton et au QToolButton (convertir).
_EDIT_BTN_STYLE = (
    "QPushButton, QToolButton {"
    " background:#5a5f66; color:#f5f5f5; border:1px solid #80868f;"
    " border-radius:4px; font-size:14px; }"
    "QPushButton:hover, QToolButton:hover { background:#6c727a; }"
    "QPushButton:pressed, QToolButton:pressed { background:#4a4e54; }"
    "QPushButton:checked, QToolButton:checked {"
    " background:#2d6cdf; border-color:#7db4ff; }"
    "QPushButton:disabled, QToolButton:disabled {"
    " background:#3a3d42; color:#888; border-color:#55585e; }"
    "QToolButton::menu-indicator { width:0px; }"
)


class _ZoomableImageView(QGraphicsView):
    """Vue image avec zoom (molette) et déplacement (glisser), sans modif fichier."""

    _MIN_SCALE = 0.05
    _MAX_SCALE = 40.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self._item = QGraphicsPixmapItem()
        self._scene.addItem(self._item)

        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing
        )
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._fit = True

        # Recadrage interactif (bande élastique).
        self._crop_mode = False
        self._rubber = QRubberBand(QRubberBand.Shape.Rectangle, self.viewport())
        self._origin = None

    def set_image(self, pixmap: QPixmap) -> None:
        self.set_crop_mode(False)
        self._item.setPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))
        self.reset_view()

    def reset_view(self) -> None:
        """Réajuste l'image à la taille de la vue (vue d'ensemble)."""
        self._fit = True
        self.resetTransform()
        if not self._item.pixmap().isNull():
            self.fitInView(self._item, Qt.AspectRatioMode.KeepAspectRatio)

    def wheelEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if self._item.pixmap().isNull() or self._crop_mode:
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        new_scale = self.transform().m11() * factor
        if new_scale < self._MIN_SCALE or new_scale > self._MAX_SCALE:
            return
        self._fit = False
        self.scale(factor, factor)

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        if self._fit:
            self.reset_view()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if not self._crop_mode:
            self.reset_view()

    # --- Recadrage interactif ---
    def set_crop_mode(self, enabled: bool) -> None:
        self._crop_mode = enabled
        self._rubber.hide()
        self._origin = None
        self.setDragMode(
            QGraphicsView.DragMode.NoDrag
            if enabled
            else QGraphicsView.DragMode.ScrollHandDrag
        )

    def mousePressEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if self._crop_mode and event.button() == Qt.MouseButton.LeftButton:
            self._origin = event.position().toPoint()
            self._rubber.setGeometry(QRect(self._origin, QSize()))
            self._rubber.show()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if self._crop_mode and self._origin is not None:
            self._rubber.setGeometry(
                QRect(self._origin, event.position().toPoint()).normalized()
            )
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if self._crop_mode and self._origin is not None:
            self._origin = None
            return
        super().mouseReleaseEvent(event)

    def crop_box(self) -> tuple[int, int, int, int] | None:
        """Renvoie la zone sélectionnée en coordonnées image, ou None."""
        if self._item.pixmap().isNull() or not self._rubber.isVisible():
            return None
        rect = self._rubber.geometry()
        if rect.width() < 3 or rect.height() < 3:
            return None
        top_left = self.mapToScene(rect.topLeft())
        bottom_right = self.mapToScene(rect.bottomRight())
        return (
            int(top_left.x()),
            int(top_left.y()),
            int(bottom_right.x()),
            int(bottom_right.y()),
        )


class PreviewPanel(QWidget):
    """Panneau droit basculant entre Aperçu et Carte."""

    MODE_PREVIEW = 0
    MODE_MAP = 1

    # Pages du sous-empilement « média » (mode Aperçu).
    _PAGE_IMAGE = 0
    _PAGE_VIDEO = 1
    _PAGE_MESSAGE = 2

    # Signaux d'édition (la fenêtre principale applique l'opération).
    rotate_requested = Signal(bool)        # True = horaire
    crop_committed = Signal(object)        # boîte (l, t, r, b) ou None
    convert_requested = Signal(str)        # extension cible (.jpg/.png/.gif/.bmp)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._current_path: str | None = None
        # True pendant un déplacement manuel de la barre de progression :
        # le lecteur ne doit pas reprendre la main sur la position affichée.
        self._seeking = False

        self.stack = QStackedWidget()

        # --- Mode Aperçu ---
        preview_widget = QWidget()
        preview_layout = QVBoxLayout(preview_widget)
        preview_layout.setContentsMargins(4, 4, 4, 4)

        preview_layout.addLayout(self._build_edit_toolbar())

        # Sous-empilement : image zoomable (0) / vidéo (1) / message (2).
        self._media_stack = QStackedWidget()
        self.image_view = _ZoomableImageView()
        self._media_stack.addWidget(self.image_view)
        self._media_stack.addWidget(self._build_video_widget())
        self._message_label = QLabel("Aperçu\n(sélectionnez une photo ou une vidéo)")
        self._message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._message_label.setWordWrap(True)
        self._media_stack.addWidget(self._message_label)
        self._media_stack.setCurrentIndex(self._PAGE_MESSAGE)
        preview_layout.addWidget(self._media_stack, stretch=3)

        # Astuce zoom (discrète).
        hint = QLabel("Molette = zoom · glisser = déplacer · double-clic = réajuster")
        hint.setStyleSheet("color:#888; font-size:10px;")
        preview_layout.addWidget(hint)

        self.metadata_label = QLabel("Métadonnées")
        self.metadata_label.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        preview_layout.addWidget(self.metadata_label, stretch=1)
        self.stack.addWidget(preview_widget)

        # --- Mode Carte : conteneur qui accueille le MapPanel (injecté par
        # la fenêtre principale, car le même widget carte est partagé avec la
        # vue « agrandie » au centre). ---
        self.map_container = QWidget()
        self._map_layout = QVBoxLayout(self.map_container)
        self._map_layout.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self.map_container)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.stack)

        self._set_edit_enabled(False)  # rien de sélectionné au départ

    def _build_edit_toolbar(self) -> QHBoxLayout:
        """Barre d'outils d'édition : boutons carrés à icône + mini-slider de mode."""
        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(3)

        self._btn_rotate_left = self._tool_button(
            "⟲", "Pivoter à gauche (90°)", "Pivoter à gauche",
            lambda: self.rotate_requested.emit(False))
        self._btn_rotate_right = self._tool_button(
            "⟳", "Pivoter à droite (90°)", "Pivoter à droite",
            lambda: self.rotate_requested.emit(True))

        self._btn_crop = self._tool_button(
            "", "Recadrer (sélectionner la zone)", "Recadrer",
            self._on_crop_clicked, checkable=True)
        self._btn_crop.setIcon(make_crop_icon())

        # La validation n'a de sens qu'une fois le recadrage engagé : elle reste
        # masquée le reste du temps plutôt que d'occuper la barre en grisé.
        self._btn_crop_apply = self._tool_button(
            "✓", "Appliquer le recadrage", "Appliquer le recadrage",
            self._on_crop_apply)
        self._btn_crop_apply.setVisible(False)

        self._btn_convert = QToolButton()
        self._btn_convert.setText("⇄")
        self._btn_convert.setToolTip("Convertir le format")
        self._btn_convert.setAccessibleName("Convertir le format")
        self._btn_convert.setFixedSize(28, 28)
        self._btn_convert.setStyleSheet(_EDIT_BTN_STYLE)
        self._btn_convert.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        convert_menu = QMenu(self._btn_convert)
        for label, ext in (("JPEG", ".jpg"), ("PNG", ".png"),
                           ("GIF", ".gif"), ("BMP", ".bmp")):
            convert_menu.addAction(
                label, lambda e=ext: self.convert_requested.emit(e)
            )
        self._btn_convert.setMenu(convert_menu)

        for b in (self._btn_rotate_left, self._btn_rotate_right, self._btn_crop,
                  self._btn_crop_apply, self._btn_convert):
            bar.addWidget(b)

        bar.addStretch(1)

        # Mini-slider Remplacer / Copier (au lieu d'un bouton switch).
        # « Remplacer » écrase l'original sans retour possible : le côté est
        # signalé en ambre pour qu'on sache toujours dans quel mode on édite.
        self._mode_switch = ToggleSlider("Remplacer", "Copier", warning_side="left")
        self._mode_switch.setToolTip(
            "Remplacer : écrit sur le fichier d'origine — irréversible, "
            "Ctrl+Z ne rattrape pas une édition.\n"
            "Copier : applique les modifications sur une copie « _copie »."
        )
        bar.addWidget(self._mode_switch)

        self._edit_buttons = [
            self._btn_rotate_left, self._btn_rotate_right,
            self._btn_crop, self._btn_crop_apply, self._btn_convert,
        ]
        return bar

    @staticmethod
    def _tool_button(text, tooltip, a11y, slot, checkable=False) -> QPushButton:
        """Crée un bouton d'outil carré (28×28) à icône/glyphe."""
        btn = QPushButton(text)
        btn.setToolTip(tooltip)
        btn.setAccessibleName(a11y)
        btn.setFixedSize(28, 28)
        btn.setStyleSheet(_EDIT_BTN_STYLE)
        btn.setCheckable(checkable)
        if checkable:
            btn.toggled.connect(slot)
        else:
            btn.clicked.connect(slot)
        return btn

    def edit_mode(self) -> str:
        """Mode d'écriture courant : 'replace' ou 'copy'."""
        return "copy" if self._mode_switch.isChecked() else "replace"

    def current_path(self) -> str | None:
        return self._current_path

    def _on_crop_clicked(self, checked: bool) -> None:
        self._on_crop_toggled(checked)

    def _on_crop_toggled(self, checked: bool) -> None:
        self.image_view.set_crop_mode(checked)
        self._btn_crop_apply.setVisible(checked)

    def _on_crop_apply(self) -> None:
        box = self.image_view.crop_box()
        self.crop_committed.emit(box)
        self._btn_crop.setChecked(False)

    def _set_edit_enabled(self, enabled: bool) -> None:
        """Active/désactive l'édition (désactivée pour les vidéos)."""
        for btn in self._edit_buttons:
            btn.setEnabled(enabled)
        if not enabled:
            # setChecked n'émet rien si la case était déjà décochée : on masque
            # donc explicitement, sinon la validation resterait à l'écran en
            # passant d'une photo en cours de recadrage à une vidéo.
            self._btn_crop.setChecked(False)
            self._btn_crop_apply.setVisible(False)

    def _build_video_widget(self) -> QWidget:
        """Construit le lecteur vidéo : surface + barre de contrôle complète.

        Contrôles classiques d'un lecteur : retour au début, recul et avance de
        10 s, lecture/pause, arrêt, barre de progression déplaçable avec les
        temps, et volume avec coupure du son.
        """
        container = QWidget()
        vlayout = QVBoxLayout(container)
        vlayout.setContentsMargins(0, 0, 0, 0)
        vlayout.setSpacing(4)

        self._video_widget = QVideoWidget()
        vlayout.addWidget(self._video_widget, stretch=1)

        self._player = QMediaPlayer()
        self._audio = QAudioOutput()
        self._player.setAudioOutput(self._audio)
        self._player.setVideoOutput(self._video_widget)
        self._player.errorOccurred.connect(self._on_player_error)
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.positionChanged.connect(self._on_position_changed)
        self._player.durationChanged.connect(self._on_duration_changed)

        # --- Ligne 1 : progression (temps écoulé / barre / durée) ---
        ligne_position = QHBoxLayout()
        ligne_position.setSpacing(6)
        self._elapsed_label = QLabel("0:00")
        self._elapsed_label.setStyleSheet(_VIDEO_TIME_STYLE)
        ligne_position.addWidget(self._elapsed_label)

        self._position_slider = QSlider(Qt.Orientation.Horizontal)
        self._position_slider.setRange(0, 0)
        self._position_slider.setToolTip("Position dans la vidéo")
        self._position_slider.setAccessibleName("Position de lecture")
        # sliderMoved n'est émis que par l'utilisateur : se brancher sur
        # valueChanged créerait une boucle avec positionChanged.
        self._position_slider.sliderMoved.connect(self._player.setPosition)
        self._position_slider.sliderPressed.connect(self._on_seek_started)
        self._position_slider.sliderReleased.connect(self._on_seek_finished)
        ligne_position.addWidget(self._position_slider, stretch=1)

        self._duration_label = QLabel("0:00")
        self._duration_label.setStyleSheet(_VIDEO_TIME_STYLE)
        ligne_position.addWidget(self._duration_label)
        vlayout.addLayout(ligne_position)

        # --- Ligne 2 : transport + volume ---
        controls = QHBoxLayout()
        controls.setSpacing(3)

        self._btn_start = self._video_button(
            "⏮", "Revenir au début", lambda: self._player.setPosition(0))
        self._btn_back = self._video_button(
            "⏪", "Reculer de 10 secondes", lambda: self._seek_relative(-10_000))
        self._play_button = self._video_button("▶", "Lecture", self._toggle_play)
        self._btn_forward = self._video_button(
            "⏩", "Avancer de 10 secondes", lambda: self._seek_relative(10_000))
        self._btn_stop = self._video_button("⏹", "Arrêter", self._stop_playback)
        for bouton in (self._btn_start, self._btn_back, self._play_button,
                       self._btn_forward, self._btn_stop):
            controls.addWidget(bouton)

        controls.addStretch(1)

        self._btn_mute = self._video_button("🔊", "Couper le son", self._toggle_mute)
        controls.addWidget(self._btn_mute)
        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setValue(80)
        self._volume_slider.setFixedWidth(90)
        self._volume_slider.setToolTip("Volume")
        self._volume_slider.setAccessibleName("Volume")
        self._volume_slider.valueChanged.connect(self._on_volume_changed)
        controls.addWidget(self._volume_slider)
        self._audio.setVolume(0.8)

        vlayout.addLayout(controls)
        return container

    @staticmethod
    def _video_button(glyphe: str, infobulle: str, slot) -> QPushButton:
        """Bouton carré de la barre de lecture."""
        bouton = QPushButton(glyphe)
        bouton.setToolTip(infobulle)
        bouton.setAccessibleName(infobulle)
        bouton.setFixedSize(30, 26)
        bouton.setStyleSheet(_EDIT_BTN_STYLE)
        bouton.clicked.connect(slot)
        return bouton

    # --- Lecture vidéo ---
    def _seek_relative(self, delta_ms: int) -> None:
        """Avance ou recule, en restant dans les bornes de la vidéo."""
        duree = self._player.duration()
        cible = max(0, self._player.position() + delta_ms)
        self._player.setPosition(min(cible, duree) if duree > 0 else cible)

    def _stop_playback(self) -> None:
        self._player.stop()
        self._position_slider.setValue(0)
        self._elapsed_label.setText(_fmt_position(0))

    def _toggle_mute(self) -> None:
        coupe = not self._audio.isMuted()
        self._audio.setMuted(coupe)
        self._btn_mute.setText("🔇" if coupe else "🔊")
        self._btn_mute.setToolTip("Rétablir le son" if coupe else "Couper le son")

    def _on_volume_changed(self, valeur: int) -> None:
        self._audio.setVolume(valeur / 100)
        if valeur > 0 and self._audio.isMuted():
            self._toggle_mute()  # bouger le volume rétablit le son

    def _on_seek_started(self) -> None:
        """Pendant un déplacement manuel, le lecteur ne pilote plus la barre."""
        self._seeking = True

    def _on_seek_finished(self) -> None:
        self._seeking = False
        self._player.setPosition(self._position_slider.value())

    def _on_position_changed(self, position: int) -> None:
        if not self._seeking:
            self._position_slider.setValue(position)
        self._elapsed_label.setText(_fmt_position(position))

    def _on_duration_changed(self, duree: int) -> None:
        self._position_slider.setRange(0, max(0, duree))
        self._duration_label.setText(_fmt_position(duree))

    # --- API ---
    def set_mode(self, mode: int) -> None:
        """Bascule le panneau entre Aperçu (0) et Carte (1)."""
        self.stack.setCurrentIndex(mode)
        if mode == self.MODE_MAP:
            self._player.pause()

    def show_media(self, path: str) -> None:
        """Affiche le média sélectionné (image zoomable ou lecteur vidéo)."""
        self._current_path = path
        self.metadata_label.setText(self._format_metadata(path))

        if os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS:
            self._show_video(path)
        else:
            self._show_image(path)

    def _show_message(self, text: str) -> None:
        self._message_label.setText(text)
        self._media_stack.setCurrentIndex(self._PAGE_MESSAGE)
        self._set_edit_enabled(False)

    # --- Image ---
    def _show_image(self, path: str) -> None:
        self._player.stop()
        qimg = load_qimage(path)
        if qimg is None:
            self._show_message("Aperçu indisponible\n(fichier illisible)")
            return
        self.image_view.set_image(QPixmap.fromImage(qimg))
        self._media_stack.setCurrentIndex(self._PAGE_IMAGE)
        self._set_edit_enabled(True)  # édition possible pour les photos

    # --- Vidéo ---
    def _show_video(self, path: str) -> None:
        self._media_stack.setCurrentIndex(self._PAGE_VIDEO)
        self._seeking = False
        self._position_slider.setValue(0)
        self._elapsed_label.setText(_fmt_position(0))
        self._set_edit_enabled(False)  # pas d'édition vidéo (hors périmètre)
        self._player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        self._player.play()

    def _toggle_play(self) -> None:
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_playback_state(self, state) -> None:
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._play_button.setText("⏸" if playing else "▶")
        self._play_button.setToolTip("Pause" if playing else "Lecture")

    def _on_player_error(self, _error, message: str) -> None:
        """Échec de lecture (codec manquant, fichier illisible) : message propre."""
        base = "Lecture vidéo impossible\n(codec manquant ?)"
        self._show_message(f"{base}\n\n{message}" if message else base)

    # --- Métadonnées ---
    def _format_metadata(self, path: str) -> str:
        meta = metadata.read(path)
        lines = [
            f"Nom : {os.path.basename(path)}",
            f"Chemin : {path}",
            f"Type : {'Vidéo' if meta.is_video else 'Photo'}",
            f"Date de prise de vue : {_fmt_datetime(meta.datetime_original)}",
        ]
        if meta.width and meta.height:
            lines.append(f"Dimensions : {meta.width} × {meta.height} px")
        lines.append(f"Taille : {_fmt_size(meta.size)}")
        if meta.is_video:
            lines.append(f"Durée : {_fmt_duration(meta.duration)}")
        if meta.has_gps:
            lines.append(f"GPS : {meta.latitude:.5f}, {meta.longitude:.5f}")
        else:
            lines.append("GPS : aucune (absente de la carte)")

        # EXIF étendu (photos) — seulement les champs présents.
        if not meta.is_video:
            extended = []
            if meta.camera:
                extended.append(f"Appareil : {meta.camera}")
            if meta.lens:
                extended.append(f"Objectif : {meta.lens}")
            expo = []
            if meta.aperture:
                expo.append(f"f/{meta.aperture:.1f}")
            if meta.exposure:
                expo.append(
                    f"1/{round(1 / meta.exposure)} s" if meta.exposure < 1
                    else f"{meta.exposure:.0f} s"
                )
            if meta.iso:
                expo.append(f"ISO {meta.iso}")
            if meta.focal_length:
                expo.append(f"{meta.focal_length:.0f} mm")
            if expo:
                extended.append("Prise de vue : " + " · ".join(expo))
            if extended:
                lines.append("")
                lines.extend(extended)
        return "\n".join(lines)
