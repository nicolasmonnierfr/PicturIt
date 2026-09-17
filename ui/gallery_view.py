"""Colonne centre — Galerie de vignettes.

Affiche les photos/vidéos du dossier source de façon **récursive**, regroupées
par sous-dossier (séparateurs de section), avec :
- chargement et génération des vignettes **en arrière-plan** ;
- vignette générique « play » pour les vidéos (1re frame = incrément 5) ;
- sélection simple / Ctrl (multiple) / Shift (plage) ;
- **glisser-déposer** des fichiers vers une cible (accès rapide ou arborescence) ;
- mises à jour **incrémentales** après une opération de tri (cf. SPEC 4.5) ;
- un switch « Afficher doublons / similaires » (logique = incrément 7).

Implémentation des sections : un ``QListView`` (mode icônes) par sous-dossier,
empilés dans une zone défilante, chacun s'ajustant en hauteur à son contenu.
"""

from __future__ import annotations

import bisect
import math
import os
from datetime import timedelta

from PySide6.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    QMimeData,
    QObject,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QIcon, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QMenu,
    QPushButton,
    QScrollArea,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from core import duplicates, metadata, scanner
from core.thumbnails import (
    THUMB_SIZE,
    ThumbnailManager,
    make_placeholder_pixmap,
    overlay_border,
    overlay_nogps,
    overlay_play,
)

# Couleurs du code doublons/similaires (cf. SPEC 4.7).
_COLOR_IDENTICAL = QColor(220, 50, 50)   # rouge
_COLOR_SIMILAR = QColor(235, 145, 30)    # orange

# Noms de mois en français (regroupement « par mois »).
_MONTHS_FR = (
    "", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
    "août", "septembre", "octobre", "novembre", "décembre",
)
# Libellé de section pour les médias sans date de prise de vue (regroup. date).
_NO_DATE_LABEL = "Sans date"


def _human_size(num: int) -> str:
    """Formate une taille en octets de façon lisible."""
    value = float(num)
    for unit in ("o", "Ko", "Mo", "Go"):
        if value < 1024 or unit == "Go":
            return f"{value:.0f} {unit}" if unit == "o" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} Go"

# Rôle de données stockant le chemin absolu du fichier sur chaque item.
PATH_ROLE = Qt.ItemDataRole.UserRole + 1

# Format MIME transportant les chemins glissés (lu par les cibles de drop).
MIME_PATHS = "application/x-picturit-paths"

# Marges de la cellule autour de la vignette (place pour le nom de fichier).
_CELL_PADDING_W = 24
_CELL_PADDING_H = 44


class _SectionListView(QListView):
    """Vue icônes d'une section : hauteur ajustée au contenu + drag fichiers."""

    def __init__(
        self, thumb_size: int = THUMB_SIZE, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.setIconSize(QSize(thumb_size, thumb_size))
        self.setGridSize(
            QSize(thumb_size + _CELL_PADDING_W, thumb_size + _CELL_PADDING_H)
        )
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # Le glisser sert à exporter des fichiers vers une cible, pas à réordonner.
        self.setDragEnabled(True)
        self.setDragDropMode(QListView.DragDropMode.DragOnly)
        # Sélection bien visible (le grisage par défaut est trop discret) : fond
        # bleu vif + bordure, y compris quand la vue n'a pas le focus (les autres
        # sections perdent le focus mais doivent garder une sélection lisible).
        self.setStyleSheet(
            "QListView::item:selected,"
            "QListView::item:selected:!active {"
            " background: rgba(40,118,232,210);"
            " border: 2px solid #7db4ff;"
            " border-radius: 4px;"
            " color: white; }"
        )

    def refresh_height(self) -> None:
        """Fixe la hauteur de la vue pour afficher toutes ses lignes."""
        model = self.model()
        if model is None or model.rowCount() == 0:
            self.setFixedHeight(0)
            return
        grid = self.gridSize()
        width = max(self.viewport().width(), grid.width())
        cols = max(1, width // grid.width())
        rows = math.ceil(model.rowCount() / cols)
        self.setFixedHeight(rows * grid.height() + 4)

    def resizeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        super().resizeEvent(event)
        self.refresh_height()

    def startDrag(self, supported_actions) -> None:  # noqa: N802 (API Qt)
        """Démarre un glisser transportant les chemins des items sélectionnés."""
        paths = [
            idx.data(PATH_ROLE)
            for idx in self.selectedIndexes()
            if idx.data(PATH_ROLE)
        ]
        if not paths:
            return
        mime = QMimeData()
        mime.setData(MIME_PATHS, "\n".join(paths).encode("utf-8"))
        # On fournit aussi les URLs de fichiers (drop vers l'explorateur externe).
        mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
        drag = QDrag(self)
        drag.setMimeData(mime)
        # Move/Copy autorisés ; la cible décide selon la touche Shift au drop.
        drag.exec(
            Qt.DropAction.MoveAction | Qt.DropAction.CopyAction,
            Qt.DropAction.MoveAction,
        )


class _Section:
    """Regroupe les widgets d'une section (en-tête + vue + modèle)."""

    __slots__ = ("header", "model", "name", "view")

    def __init__(self, name, header, view, model) -> None:
        self.name = name
        self.header = header
        self.view = view
        self.model = model


class _DuplicatesSignals(QObject):
    finished = Signal(object)  # core.duplicates.DupIndex


class _DuplicatesWorker(QRunnable):
    """Analyse des doublons/similaires en arrière-plan (peut hacher des fichiers)."""

    def __init__(self, paths: list[str]) -> None:
        super().__init__()
        self._paths = paths
        self.signals = _DuplicatesSignals()

    def run(self) -> None:
        try:
            index = duplicates.analyze(self._paths)
        except Exception:  # noqa: BLE001 — ne jamais planter sur un fichier exotique
            index = duplicates.DupIndex()
        self.signals.finished.emit(index)


class GalleryView(QWidget):
    """Galerie centrale : sections de vignettes + barre d'outils."""

    # Émis lorsqu'une photo/vidéo devient la sélection courante (aperçu).
    media_selected = Signal(str)
    # Émis quand la sélection change (liste des chemins sélectionnés).
    selection_changed = Signal(list)
    # Émis (débattu) avec la liste des points géolocalisés {id, lat, lon}.
    geo_points_changed = Signal(list)
    # Progression du chargement des vignettes (faits, total).
    loading_progress = Signal(int, int)
    # Émis quand toutes les vignettes sont chargées (ou en erreur).
    loading_finished = Signal()
    # Message d'état (affiché dans la barre de statut par la fenêtre principale).
    status = Signal(str)
    # Demande de renommage de la sélection : mode "prefix" ou "datetime".
    rename_requested = Signal(str)
    # Demande de rotation de la sélection depuis la galerie (True = horaire).
    rotate_selection_requested = Signal(bool)
    # Résumé du dossier / de la sélection (pour la barre de statut permanente).
    summary_changed = Signal(str)
    # Double-clic sur une vignette → demande d'affichage plein écran (chemin).
    fullscreen_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._thumbnails = ThumbnailManager(self)
        self._thumbnails.thumbnail_ready.connect(self._on_thumbnail_ready)
        self._thumbnails.thumbnail_failed.connect(self._on_thumbnail_failed)
        self._thumbnails.geo_point.connect(self._on_geo_point)

        # Points géolocalisés (chemin -> (lat, lon)) + émission débattue vers la carte.
        self._geo: dict[str, tuple[float, float]] = {}
        self._geo_timer = QTimer(self)
        self._geo_timer.setSingleShot(True)
        self._geo_timer.timeout.connect(self._flush_geo)

        # Taille courante des vignettes (réglable, cf. backlog).
        self._thumb_size = THUMB_SIZE
        # Liste des médias actuellement affichés (pour régénérer à une autre taille).
        self._current_display: list[scanner.MediaFile] = []
        # Timer anti-rebond pour le curseur de taille.
        self._size_timer = QTimer(self)
        self._size_timer.setSingleShot(True)
        self._size_timer.timeout.connect(self._apply_thumb_size)

        # Vignettes d'attente (régénérées si la taille change).
        self._rebuild_pending_icons()

        self._source_root = ""
        # Sections vivantes, indexées par nom, + ordre d'affichage.
        self._sections: dict[str, _Section] = {}
        self._section_order: list[str] = []
        # Clé de tri de chaque section (dépend du mode de regroupement).
        self._section_keys: dict[str, tuple] = {}
        # path -> QStandardItem et path -> nom de section.
        self._items: dict[str, QStandardItem] = {}
        self._item_section: dict[str, str] = {}
        self._total = 0
        self._done = 0
        self._loading = False

        # Médias du dossier source (source de vérité pour le filtrage doublons).
        # L'ordre d'insertion = ordre du scan (préservé par le dict).
        self._media_by_path: dict[str, scanner.MediaFile] = {}
        # Tri / filtre / recherche de la galerie.
        self._sort_key = "name"     # name | date | size
        self._sort_desc = False     # ordre décroissant si True
        self._filter = "all"        # all | photos | videos | gps | nogps
        self._group_by = "dir"      # dir | day | week | month (regroupement)
        self._search = ""
        # Restriction d'affichage à un sous-ensemble de chemins (clic carte).
        self._path_filter: set[str] | None = None
        self._date_cache: dict[str, object] = {}  # cache des dates (tri par date)
        # État du mode doublons/similaires.
        self._dup_index: duplicates.DupIndex | None = None
        self._dup_color: dict[str, str] = {}
        self._active_group: str | None = None
        # Catégories affichées en mode doublons : {COLOR_IDENTICAL, COLOR_SIMILAR}.
        self._dup_categories: set[str] = set()
        # Analyse doublons en cours (évite de lancer plusieurs workers).
        self._dup_pending = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        # --- Barre d'outils de la galerie (compacte : libellés en infobulles) ---
        toolbar = QHBoxLayout()
        toolbar.setSpacing(6)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Rechercher…")
        self._search_edit.setToolTip("Rechercher par nom de fichier")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.setFixedWidth(150)
        self._search_edit.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self._search_edit)

        self._sort_combo = QComboBox()
        self._sort_combo.setToolTip("Trier par")
        self._sort_combo.addItem("Nom", "name")
        self._sort_combo.addItem("Date", "date")
        self._sort_combo.addItem("Taille", "size")
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        toolbar.addWidget(self._sort_combo)

        # Bouton bascule de l'ordre de tri (croissant ▲ / décroissant ▼).
        self._order_button = QToolButton()
        self._order_button.setCheckable(True)
        self._order_button.setText("▲")
        self._order_button.setToolTip("Ordre croissant (cliquer pour décroissant)")
        self._order_button.setAccessibleName("Ordre de tri croissant ou décroissant")
        self._order_button.toggled.connect(self._on_order_toggled)
        toolbar.addWidget(self._order_button)

        # Regroupement des sections : par dossier (défaut) ou par date.
        self._group_combo = QComboBox()
        self._group_combo.setToolTip("Grouper par")
        self._group_combo.addItem("Dossier", "dir")
        self._group_combo.addItem("Jour", "day")
        self._group_combo.addItem("Semaine", "week")
        self._group_combo.addItem("Mois", "month")
        self._group_combo.currentIndexChanged.connect(self._on_group_changed)
        toolbar.addWidget(self._group_combo)

        self._filter_combo = QComboBox()
        self._filter_combo.setToolTip("Filtrer")
        self._filter_combo.addItem("Tous", "all")
        self._filter_combo.addItem("Photos", "photos")
        self._filter_combo.addItem("Vidéos", "videos")
        self._filter_combo.addItem("Avec GPS", "gps")
        self._filter_combo.addItem("Sans GPS", "nogps")
        self._filter_combo.currentIndexChanged.connect(self._on_filter_changed)
        toolbar.addWidget(self._filter_combo)

        toolbar.addStretch(1)

        # Taille des vignettes : regroupée dans un bouton à menu (gain de place).
        self._size_slider = QSlider(Qt.Orientation.Horizontal)
        self._size_slider.setMinimum(48)  # vignettes très petites (cf. backlog)
        self._size_slider.setMaximum(320)
        self._size_slider.setSingleStep(16)
        self._size_slider.setValue(self._thumb_size)
        self._size_slider.setFixedWidth(140)
        self._size_slider.valueChanged.connect(self._on_size_slider)
        size_menu = QMenu(self)
        size_holder = QWidget()
        size_layout = QHBoxLayout(size_holder)
        size_layout.setContentsMargins(8, 4, 8, 4)
        size_layout.addWidget(QLabel("Taille :"))
        size_layout.addWidget(self._size_slider)
        size_action = QWidgetAction(size_menu)
        size_action.setDefaultWidget(size_holder)
        size_menu.addAction(size_action)
        self._size_button = QToolButton()
        self._size_button.setText("Taille ▾")
        self._size_button.setToolTip("Taille des vignettes")
        self._size_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._size_button.setMenu(size_menu)
        toolbar.addWidget(self._size_button)

        # Deux coches indépendantes : voir les doublons (rouge) et/ou les
        # similaires (orange), séparément ou ensemble.
        self.duplicates_switch = QCheckBox("Doublons")
        self.duplicates_switch.setToolTip("Afficher les doublons identiques (rouge)")
        self.duplicates_switch.toggled.connect(self._on_dup_filter_changed)
        toolbar.addWidget(self.duplicates_switch)
        self.similars_switch = QCheckBox("Similaires")
        self.similars_switch.setToolTip("Afficher les fichiers similaires (orange)")
        self.similars_switch.toggled.connect(self._on_dup_filter_changed)
        toolbar.addWidget(self.similars_switch)
        layout.addLayout(toolbar)

        # --- Bannière de filtre (affichée quand un filtrage carte est actif) ---
        self._filter_banner = QFrame()
        self._filter_banner.setStyleSheet(
            "background:#2d4a5a; border-radius:3px;"
        )
        banner_layout = QHBoxLayout(self._filter_banner)
        banner_layout.setContentsMargins(8, 3, 6, 3)
        self._filter_banner_label = QLabel()
        banner_layout.addWidget(self._filter_banner_label)
        banner_layout.addStretch(1)
        self._clear_filter_button = QPushButton("✕ Supprimer le filtre")
        self._clear_filter_button.setToolTip(
            "Revenir à l'affichage complet du contenu"
        )
        self._clear_filter_button.clicked.connect(self._clear_path_filter)
        banner_layout.addWidget(self._clear_filter_button)
        self._filter_banner.setVisible(False)
        layout.addWidget(self._filter_banner)

        # --- Zone défilante contenant les sections ---
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._container = QWidget()
        self._sections_layout = QVBoxLayout(self._container)
        self._sections_layout.setContentsMargins(0, 0, 0, 0)
        self._sections_layout.setSpacing(8)
        self._scroll.setWidget(self._container)
        layout.addWidget(self._scroll)

        # Message affiché tant qu'aucun dossier source n'est chargé.
        # Toujours à l'index 0 du layout ; les sections s'insèrent après.
        self._placeholder = QLabel(
            "Sélectionnez un dossier dans l'arborescence pour afficher les photos."
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sections_layout.addWidget(self._placeholder)
        self._sections_layout.addStretch(1)

    # --- Chargement d'un dossier source ---
    def load_media(self, source_dir: str) -> None:
        """Scanne *source_dir* et reconstruit la galerie (récursif)."""
        # Réinitialisation complète (cache vignettes + points GPS + doublons).
        self._thumbnails.clear()
        self._geo.clear()
        self._schedule_geo()
        self._exit_duplicates_state()
        # Décoche les coches doublons/similaires sans relancer d'analyse.
        for switch in (self.duplicates_switch, self.similars_switch):
            switch.blockSignals(True)
            switch.setChecked(False)
            switch.blockSignals(False)
        self._source_root = source_dir

        sections = scanner.scan(source_dir)
        self._media_by_path = {
            media.path: media for _, files in sections for media in files
        }

        self._date_cache.clear()
        self._reset_filters()

        if not self._media_by_path:
            self._clear_view()
            self._placeholder.setText("Aucune photo ou vidéo trouvée dans ce dossier.")
            self._placeholder.show()
            self.loading_finished.emit()
            return

        self._refresh_view()

    def _reset_filters(self) -> None:
        """Réinitialise tri/ordre/regroupement/filtre/recherche (sans réaffichage)."""
        self._sort_key, self._filter, self._search = "name", "all", ""
        self._sort_desc = False
        self._group_by = "dir"
        self._path_filter = None
        self._filter_banner.setVisible(False)
        widgets = (self._sort_combo, self._filter_combo, self._search_edit,
                   self._order_button, self._group_combo)
        for widget in widgets:
            widget.blockSignals(True)
        self._sort_combo.setCurrentIndex(0)
        self._filter_combo.setCurrentIndex(0)
        self._group_combo.setCurrentIndex(0)
        self._order_button.setChecked(False)
        self._order_button.setText("▲")
        self._search_edit.clear()
        for widget in widgets:
            widget.blockSignals(False)

    def _display(self, media_list: list[scanner.MediaFile]) -> None:
        """(Re)construit les sections à partir d'une liste de médias donnée."""
        self._clear_view()
        if not media_list:
            self._placeholder.setText("Aucun élément à afficher.")
            self._placeholder.show()
            self.loading_finished.emit()
            return
        self._placeholder.hide()
        self._current_display = list(media_list)

        self._total = len(media_list)
        self._done = 0
        self._loading = True
        self.loading_progress.emit(self._done, self._total)

        # Regroupe par section selon le mode de regroupement (dossier ou date).
        by_section: dict[str, list[scanner.MediaFile]] = {}
        keys: dict[str, tuple] = {}
        for media in media_list:
            label, key = self._section_of(media)
            by_section.setdefault(label, []).append(media)
            keys[label] = key
        for name in sorted(by_section, key=lambda n: keys[n]):
            self._create_section(name, keys[name])
            for media in by_section[name]:
                self._append_item(name, media.path, media.is_video)

    def _clear_view(self) -> None:
        """Vide les sections affichées (sans toucher au cache ni aux points GPS)."""
        for section in self._sections.values():
            section.header.setParent(None)
            section.header.deleteLater()
            section.view.setParent(None)
            section.view.deleteLater()
        self._sections.clear()
        self._section_order.clear()
        self._section_keys.clear()
        self._items.clear()
        self._item_section.clear()
        self._total = 0
        self._done = 0
        self._loading = False

    # --- Gestion des sections (création/insertion ordonnée) ---
    def _create_section(self, name: str, sort_key: tuple) -> _Section:
        """Crée une section (en-tête + vue) insérée à sa place triée."""
        header = QLabel()
        header.setStyleSheet(
            "background:#3a3a3a; padding:4px; font-weight:bold; border-radius:3px;"
        )

        model = QStandardItemModel(self)
        view = _SectionListView(self._thumb_size)
        view.setModel(model)
        view.clicked.connect(self._on_item_clicked)
        view.doubleClicked.connect(self._on_item_double_clicked)
        view.selectionModel().currentChanged.connect(self._on_current_changed)
        view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        view.customContextMenuRequested.connect(self._on_view_context_menu)

        # Position ordonnée parmi les sections existantes (selon la clé de tri).
        self._section_keys[name] = sort_key
        keys = [self._section_keys[n] for n in self._section_order]
        pos = bisect.bisect_left(keys, sort_key)
        self._section_order.insert(pos, name)

        # Layout = [placeholder][header0][view0]…[stretch] ; placeholder à l'index 0.
        header_index = 1 + 2 * pos
        self._sections_layout.insertWidget(header_index, header)
        self._sections_layout.insertWidget(header_index + 1, view)

        section = _Section(name, header, view, model)
        self._sections[name] = section
        self._update_header(name)
        self._placeholder.hide()
        return section

    def _remove_section(self, name: str) -> None:
        """Supprime une section devenue vide."""
        section = self._sections.pop(name, None)
        if section is None:
            return
        self._section_order.remove(name)
        self._section_keys.pop(name, None)
        section.header.setParent(None)
        section.header.deleteLater()
        section.view.setParent(None)
        section.view.deleteLater()
        if not self._sections:
            self._placeholder.setText("Galerie vide.")
            self._placeholder.show()

    def _update_header(self, name: str) -> None:
        section = self._sections[name]
        section.header.setText(f"  {name}   ({section.model.rowCount()})")

    # --- Gestion des items ---
    def _append_item(self, section_name: str, path: str, is_video: bool) -> None:
        """Ajoute un item dans une section et déclenche sa vignette si besoin."""
        item = QStandardItem()
        item.setEditable(False)
        item.setText(os.path.basename(path))
        item.setData(path, PATH_ROLE)
        item.setToolTip(path)
        item.setIcon(self._video_icon if is_video else self._pending_icon)

        section = self._sections[section_name]
        section.model.appendRow(item)
        self._items[path] = item
        self._item_section[path] = section_name
        self._update_header(section_name)
        section.view.refresh_height()

        # Génération (ou récupération en cache) de la vraie vignette.
        self._thumbnails.request(path, is_video, self._thumb_size)

    def _remove_item(self, path: str) -> None:
        """Retire un item de la galerie (et sa section si elle devient vide)."""
        # Le fichier n'existe plus à cet emplacement : oublier ses métadonnées.
        self._media_by_path.pop(path, None)
        name = self._item_section.pop(path, None)
        item = self._items.pop(path, None)
        if name is None or item is None:
            return
        section = self._sections.get(name)
        if section is None:
            return
        section.model.removeRow(item.row())
        if path in self._geo:
            del self._geo[path]
            self._schedule_geo()
        if section.model.rowCount() == 0:
            self._remove_section(name)
        else:
            self._update_header(name)
            section.view.refresh_height()

    def _add_path(self, path: str) -> None:
        """Ajoute un fichier déjà présent sur disque, dans la bonne section."""
        dir_name = self._section_for_path(path)
        is_video = scanner.is_video(path)
        # Met à jour la source de vérité (utile pour le mode doublons ultérieur).
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        media = scanner.MediaFile(
            path=path, section=dir_name, is_video=is_video, size=size
        )
        self._media_by_path[path] = media
        # Le libellé de section dépend du mode de regroupement (dossier ou date).
        name, key = self._section_of(media)
        if name not in self._sections:
            self._create_section(name, key)
        self._append_item(name, path, is_video)

    def _section_for_path(self, path: str) -> str:
        rel = os.path.relpath(os.path.dirname(path), self._source_root)
        return scanner.ROOT_SECTION_LABEL if rel == "." else rel

    def _is_within_source(self, path: str) -> bool:
        """True si *path* est dans l'arborescence du dossier source."""
        if not self._source_root:
            return False
        root = os.path.normcase(os.path.abspath(self._source_root))
        target = os.path.normcase(os.path.abspath(path))
        try:
            return os.path.commonpath([root, target]) == root
        except ValueError:
            return False  # lecteurs différents

    # --- Édition d'images (rotation / recadrage / conversion) ---
    def invalidate_thumbnail(self, path: str) -> None:
        """Force la régénération de la vignette d'un fichier modifié sur disque."""
        self._thumbnails.invalidate(path)
        item = self._items.get(path)
        if item is not None:
            item.setIcon(
                self._video_icon if scanner.is_video(path) else self._pending_icon
            )
            self._thumbnails.request(path, scanner.is_video(path), self._thumb_size)

    def add_existing_file(self, path: str) -> None:
        """Ajoute à la galerie un fichier déjà présent sur disque (s'il manque)."""
        if path not in self._items and self._is_within_source(path):
            self._add_path(path)

    def remove_file(self, path: str) -> None:
        """Retire de la galerie un fichier (sans toucher au disque)."""
        self._remove_item(path)

    # --- Mises à jour après opérations de tri (cf. SPEC 4.5) ---
    def apply_changes(self, changes) -> None:
        """Répercute une liste de FileChange (move/copy/trash/undo)."""
        from core.operations import COPIED, MOVED, REMOVED

        for ch in changes:
            if ch.kind == REMOVED:
                self._remove_item(ch.src)
            elif ch.kind == MOVED:
                self._remove_item(ch.src)
                if self._is_within_source(ch.dst):
                    self._thumbnails.rekey(ch.src, ch.dst)
                    self._add_path(ch.dst)
            elif ch.kind == COPIED and self._is_within_source(ch.dst):
                self._thumbnails.duplicate(ch.src, ch.dst)
                self._add_path(ch.dst)

    # --- Sélection ---
    def selected_paths(self) -> list[str]:
        """Renvoie tous les chemins sélectionnés, toutes sections confondues."""
        paths: list[str] = []
        for section in self._sections.values():
            for idx in section.view.selectedIndexes():
                path = idx.data(PATH_ROLE)
                if path:
                    paths.append(path)
        return paths

    def _on_item_clicked(self, index) -> None:
        """Clic sans modificateur : vide la sélection des autres sections.

        En mode doublons (sans modificateur), filtre en plus la galerie sur le
        groupe de la vignette cliquée (cf. SPEC 4.7).
        """
        modifiers = QApplication.keyboardModifiers()
        if modifiers & (
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        ):
            return
        clicked_view = self.sender()
        for section in self._sections.values():
            if section.view is not clicked_view:
                section.view.clearSelection()

        # Filtrage par groupe (différé pour ne pas détruire la vue pendant son signal).
        if self._dup_index is not None and self._active_group is None:
            path = index.data(PATH_ROLE)
            if path and path in self._dup_index.group_of:
                QTimer.singleShot(0, lambda p=path: self._filter_to_group(p))

    def _on_view_context_menu(self, pos) -> None:
        """Menu contextuel d'une vignette : ouvrir / copier / pivoter / renommer."""
        view = self.sender()
        index = view.indexAt(pos)
        if index.isValid() and not view.selectionModel().isSelected(index):
            # Clic droit hors sélection : sélectionner d'abord cet élément.
            self.select_paths([index.data(PATH_ROLE)])
        paths = self.selected_paths()
        if not paths:
            return

        menu = QMenu(self)
        act_open = menu.addAction("Ouvrir avec l'application par défaut")
        act_reveal = menu.addAction("Ouvrir dans l'Explorateur")
        act_copy = menu.addAction("Copier le chemin")
        menu.addSeparator()
        act_rot_l = menu.addAction("Pivoter à gauche")
        act_rot_r = menu.addAction("Pivoter à droite")
        menu.addSeparator()
        act_prefix = menu.addAction("Renommer : ajouter un préfixe…")
        act_date = menu.addAction("Renommer depuis la date de prise de vue")
        act_dup = None
        if self._dup_index is not None and self._active_group is None:
            menu.addSeparator()
            act_dup = menu.addAction("Sélectionner tous sauf le 1er de chaque groupe")

        chosen = menu.exec(view.viewport().mapToGlobal(pos))
        if chosen is act_open:
            self._open_default(paths[0])
        elif chosen is act_reveal:
            self._reveal_in_explorer(paths[0])
        elif chosen is act_copy:
            QApplication.clipboard().setText("\n".join(paths))
            self.status.emit(f"{len(paths)} chemin(s) copié(s) dans le presse-papiers.")
        elif chosen is act_rot_l:
            self.rotate_selection_requested.emit(False)
        elif chosen is act_rot_r:
            self.rotate_selection_requested.emit(True)
        elif chosen is act_prefix:
            self.rename_requested.emit("prefix")
        elif chosen is act_date:
            self.rename_requested.emit("datetime")
        elif chosen is not None and chosen is act_dup:
            self.select_all_but_first_per_group()

    @staticmethod
    def _open_default(path: str) -> None:
        """Ouvre un fichier avec l'application par défaut (Windows)."""
        try:
            os.startfile(path)  # noqa: S606 — ouverture utilisateur explicite
        except OSError:
            pass

    @staticmethod
    def _reveal_in_explorer(path: str) -> None:
        """Révèle un fichier dans l'Explorateur Windows (sélectionné)."""
        import subprocess

        try:
            subprocess.Popen(  # noqa: S603 — explorateur Windows
                ["explorer", "/select,", os.path.normpath(path)]  # noqa: S607
            )
        except OSError:
            pass

    def _on_item_double_clicked(self, index) -> None:
        """Double-clic → demande l'affichage plein écran de la vignette."""
        path = index.data(PATH_ROLE)
        if path:
            self.fullscreen_requested.emit(path)

    def _on_current_changed(self, current, _previous) -> None:
        """Met à jour l'aperçu avec le média devenu courant."""
        if not current.isValid():
            return
        path = current.data(PATH_ROLE)
        if path:
            self.media_selected.emit(path)

    def _on_selection_changed(self, *_args) -> None:
        """Propage tout changement de sélection (carte + résumé de statut)."""
        paths = self.selected_paths()
        self.selection_changed.emit(paths)
        self.summary_changed.emit(self.selection_summary(paths))

    def select_all(self) -> None:
        """Sélectionne toutes les vignettes affichées (Ctrl+A)."""
        for section in self._sections.values():
            section.view.selectAll()

    def clear_all_selection(self) -> None:
        """Désélectionne tout (Échap)."""
        for section in self._sections.values():
            section.view.clearSelection()
        self.summary_changed.emit(self.folder_summary())

    def ordered_paths(self) -> list[str]:
        """Chemins affichés dans l'ordre des sections (pour la navigation)."""
        paths: list[str] = []
        for name in self._section_order:
            model = self._sections[name].model
            for row in range(model.rowCount()):
                p = model.index(row, 0).data(PATH_ROLE)
                if p:
                    paths.append(p)
        return paths

    def select_relative(self, delta: int) -> str | None:
        """Sélectionne le média *delta* positions après le courant. Renvoie son chemin."""
        ordered = self.ordered_paths()
        if not ordered:
            return None
        current = self.selected_paths()
        if current:
            try:
                idx = ordered.index(current[-1])
            except ValueError:
                idx = 0
            idx = max(0, min(len(ordered) - 1, idx + delta))
        else:
            idx = 0
        target = ordered[idx]
        self.select_paths([target])
        return target

    def select_all_but_first_per_group(self) -> None:
        """En mode doublons : sélectionne tous les membres sauf le 1er de chaque groupe.

        Prépare la mise en corbeille manuelle (jamais d'auto-suppression, SPEC 4.7).
        """
        if self._dup_index is None:
            return
        to_select: list[str] = []
        for key, members in self._dup_index.groups.items():
            cat = (duplicates.COLOR_IDENTICAL if key.startswith("ident:")
                   else duplicates.COLOR_SIMILAR)
            if cat not in self._dup_categories:
                continue
            # Ne garder que les membres réellement colorés dans cette catégorie
            # (un groupe similaire peut lister des chemins déjà « identiques »).
            colored = [p for p in members
                       if self._dup_index.color_of.get(p) == cat]
            ordered = sorted(colored, key=lambda p: os.path.basename(p).lower())
            to_select.extend(ordered[1:])  # garde le premier de chaque groupe
        self.select_paths(to_select)
        self.status.emit(
            f"{len(to_select)} fichier(s) sélectionné(s) "
            "(1er de chaque groupe conservé). "
            "Suppr pour les envoyer à la corbeille."
        )

    # --- Résumés (barre de statut) ---
    def folder_summary(self) -> str:
        """Résumé du dossier : nb photos/vidéos, sans GPS, taille totale."""
        media = list(self._media_by_path.values())
        if not media:
            return ""
        photos = sum(1 for m in media if not m.is_video)
        videos = sum(1 for m in media if m.is_video)
        total = sum(m.size for m in media)
        nogps = sum(1 for m in media if m.path not in self._geo)
        return (
            f"{photos} photo(s) · {videos} vidéo(s) · {nogps} sans GPS · "
            f"{_human_size(total)}"
        )

    def selection_summary(self, paths) -> str:
        """Résumé de la sélection, ou résumé du dossier si rien n'est sélectionné."""
        if not paths:
            return self.folder_summary()
        total = sum(
            self._media_by_path[p].size for p in paths if p in self._media_by_path
        )
        nogps = sum(1 for p in paths if p not in self._geo)
        return f"{len(paths)} sélectionné(s) · {nogps} sans GPS · {_human_size(total)}"

    def select_paths(self, paths) -> None:
        """Sélectionne dans la galerie les items dont le chemin est dans *paths*.

        Utilisé par la carte (clic sur marqueur/cluster → sélection galerie).
        """
        target = set(paths)
        first_view = None
        first_index = None
        for section in self._sections.values():
            selection = QItemSelection()
            for row in range(section.model.rowCount()):
                idx = section.model.index(row, 0)
                if idx.data(PATH_ROLE) in target:
                    selection.select(idx, idx)
                    if first_index is None:
                        first_view, first_index = section.view, idx
            section.view.selectionModel().select(
                selection, QItemSelectionModel.SelectionFlag.ClearAndSelect
            )
        if first_view is not None and first_index is not None:
            first_view.scrollTo(first_index)
            first_view.selectionModel().setCurrentIndex(
                first_index, QItemSelectionModel.SelectionFlag.Current
            )

    # --- Points géolocalisés (pour la carte) ---
    def _on_geo_point(self, path: str, lat: float, lon: float) -> None:
        self._geo[path] = (lat, lon)
        self._schedule_geo()

    def _schedule_geo(self) -> None:
        """Planifie une émission groupée des points (évite le spam au chargement)."""
        self._geo_timer.start(200)

    def _geo_payload(self) -> list[dict]:
        """Construit la liste des points carte avec nom et vignette (base64)."""
        return [
            {
                "id": path,
                "lat": lat,
                "lon": lon,
                "name": os.path.basename(path),
                "thumb": self._thumbnails.thumb_data_url(path),
            }
            for path, (lat, lon) in self._geo.items()
        ]

    def _flush_geo(self) -> None:
        self.geo_points_changed.emit(self._geo_payload())

    def geo_points(self) -> list[dict]:
        """Points géolocalisés courants : {id, lat, lon, name, thumb}."""
        return self._geo_payload()

    # --- Réception des vignettes ---
    def _decorate(self, path: str, pixmap, has_gps: bool):
        """Compose la vignette : play (vidéo) + badge sans GPS + bordure doublon."""
        if scanner.is_video(path):
            pixmap = overlay_play(pixmap)
        if not has_gps:
            pixmap = overlay_nogps(pixmap)
        color = self._dup_color.get(path)
        if color is not None:
            qcolor = (
                _COLOR_IDENTICAL if color == duplicates.COLOR_IDENTICAL
                else _COLOR_SIMILAR
            )
            pixmap = overlay_border(pixmap, qcolor)
        return pixmap

    def _gps_tooltip(self, path: str, has_gps: bool) -> str:
        """Infobulle incluant le statut GPS (accessible, pas seulement la couleur)."""
        return f"{path}\n{'GPS présent' if has_gps else '⚠ Sans coordonnées GPS'}"

    def _on_thumbnail_ready(self, path: str, pixmap, has_gps: bool) -> None:
        item = self._items.get(path)
        if item is not None:
            item.setIcon(QIcon(self._decorate(path, pixmap, has_gps)))
            item.setToolTip(self._gps_tooltip(path, has_gps))
        self._advance_progress()

    def _on_thumbnail_failed(self, path: str, has_gps: bool) -> None:
        item = self._items.get(path)
        if item is not None:
            label = "vidéo" if scanner.is_video(path) else "illisible"
            base = make_placeholder_pixmap(label, self._thumb_size)
            item.setIcon(QIcon(self._decorate(path, base, has_gps)))
            item.setToolTip(self._gps_tooltip(path, has_gps))
        self._advance_progress()

    def _advance_progress(self) -> None:
        if not self._loading:
            return
        self._done += 1
        self.loading_progress.emit(self._done, self._total)
        if self._done >= self._total:
            self._loading = False
            self.loading_finished.emit()

    # --- Tri / filtre / recherche ---
    def _base_media(self) -> list[scanner.MediaFile]:
        """Médias servant de base à l'affichage (groupés en mode doublons).

        En mode doublons, ne retient que les catégories cochées (doublons
        identiques et/ou similaires).
        """
        if self._dup_index is not None:
            cats = self._dup_categories
            return [
                self._media_by_path[p]
                for p in self._dup_index.grouped_paths()
                if p in self._media_by_path
                and self._dup_index.color_of.get(p) in cats
            ]
        return list(self._media_by_path.values())

    def _date_of(self, path: str):
        """Date de prise de vue (mise en cache pour le tri par date)."""
        if path not in self._date_cache:
            self._date_cache[path] = metadata.read(path).datetime_original
        return self._date_cache[path]

    def _compute_view(self) -> list[scanner.MediaFile]:
        """Applique filtre carte + filtre + recherche + tri à la base courante."""
        media = self._base_media()

        # Restriction à un sous-ensemble (clic sur la carte).
        if self._path_filter is not None:
            media = [m for m in media if m.path in self._path_filter]

        if self._filter == "photos":
            media = [m for m in media if not m.is_video]
        elif self._filter == "videos":
            media = [m for m in media if m.is_video]
        elif self._filter == "gps":
            media = [m for m in media if m.path in self._geo]
        elif self._filter == "nogps":
            media = [m for m in media if m.path not in self._geo]

        if self._search:
            needle = self._search.lower()
            media = [m for m in media if needle in os.path.basename(m.path).lower()]

        desc = self._sort_desc
        if self._sort_key == "size":
            media.sort(key=lambda m: m.size, reverse=desc)
        elif self._sort_key == "date":
            media.sort(key=lambda m: (self._date_of(m.path) is None,
                                      self._date_of(m.path) or 0), reverse=desc)
        else:  # name
            media.sort(key=lambda m: os.path.basename(m.path).lower(), reverse=desc)
        return media

    def _refresh_view(self) -> None:
        """Recalcule et réaffiche la galerie selon tri/filtre/recherche."""
        self._active_group = None
        self._display(self._compute_view())

    def _on_search_changed(self, text: str) -> None:
        self._search = text.strip()
        self._refresh_view()

    def _on_sort_changed(self, _index: int) -> None:
        self._sort_key = self._sort_combo.currentData()
        self._refresh_view()

    def _on_filter_changed(self, _index: int) -> None:
        self._filter = self._filter_combo.currentData()
        self._refresh_view()

    def _on_order_toggled(self, checked: bool) -> None:
        """Bascule l'ordre de tri (croissant ▲ / décroissant ▼)."""
        self._sort_desc = checked
        self._order_button.setText("▼" if checked else "▲")
        self._order_button.setToolTip(
            "Ordre décroissant (cliquer pour croissant)" if checked
            else "Ordre croissant (cliquer pour décroissant)"
        )
        self._refresh_view()

    def _on_group_changed(self, _index: int) -> None:
        """Change le mode de regroupement des sections (dossier ou date)."""
        self._group_by = self._group_combo.currentData()
        self._refresh_view()

    def _section_of(self, media: scanner.MediaFile) -> tuple[str, tuple]:
        """Libellé + clé de tri de la section d'un média (selon le regroupement).

        - 'dir'   : sous-dossier (comportement par défaut historique) ;
        - date    : jour / semaine (lundi) / mois de la date de prise de vue.
        Les médias sans date sont regroupés à part, toujours en dernier.
        """
        if self._group_by == "dir":
            return media.section, scanner.section_key(media.section)
        dt = self._date_of(media.path)
        if dt is None:
            return _NO_DATE_LABEL, (1, "")  # sans date : toujours en dernier
        if self._group_by == "day":
            return dt.strftime("%d/%m/%Y"), (0, dt.strftime("%Y%m%d"))
        if self._group_by == "week":
            monday = dt - timedelta(days=dt.weekday())
            return (f"Semaine du {monday.strftime('%d/%m/%Y')}",
                    (0, monday.strftime("%Y%m%d")))
        # month
        return (f"{_MONTHS_FR[dt.month]} {dt.year}",
                (0, f"{dt.year}{dt.month:02d}"))

    # --- Filtrage par clic sur la carte (masque les autres médias) ---
    def filter_to_paths(self, paths) -> None:
        """Restreint la galerie aux *paths* (clic carte) : masque tout le reste.

        Une bannière « Supprimer le filtre » permet de revenir à la vue complète.
        """
        wanted = [p for p in paths if p in self._media_by_path]
        if not wanted:
            return
        self._path_filter = set(wanted)
        self._refresh_view()
        self._filter_banner_label.setText(
            f"Filtré depuis la carte : {len(self._path_filter)} photo(s) affichée(s)"
        )
        self._filter_banner.setVisible(True)

    def _clear_path_filter(self) -> None:
        """Retire le filtrage issu de la carte et revient à l'affichage complet."""
        if self._path_filter is None:
            return
        self._path_filter = None
        self._filter_banner.setVisible(False)
        self._refresh_view()

    # --- Taille des vignettes (réglable, cf. backlog) ---
    def _rebuild_pending_icons(self) -> None:
        """(Re)génère les vignettes d'attente à la taille courante."""
        self._video_icon = QIcon(make_placeholder_pixmap("▶ vidéo…", self._thumb_size))
        self._pending_icon = QIcon(make_placeholder_pixmap("…", self._thumb_size))

    def _on_size_slider(self, value: int) -> None:
        """Réagit au curseur de taille (anti-rebond pour éviter les régénérations)."""
        self._size_timer.start(250)

    def _apply_thumb_size(self) -> None:
        """Applique la nouvelle taille : régénère les vignettes et réaffiche."""
        new_size = self._size_slider.value()
        if new_size == self._thumb_size:
            return
        self._thumb_size = new_size
        self._rebuild_pending_icons()
        # Le cache RAM dépend de la taille : on le purge et on régénère.
        self._thumbnails.clear()
        self._display(list(self._current_display))

    # --- Mode doublons / similaires (cf. SPEC 4.7) ---
    def _current_dup_categories(self) -> set[str]:
        """Catégories actuellement cochées (identiques et/ou similaires)."""
        cats: set[str] = set()
        if self.duplicates_switch.isChecked():
            cats.add(duplicates.COLOR_IDENTICAL)
        if self.similars_switch.isChecked():
            cats.add(duplicates.COLOR_SIMILAR)
        return cats

    def _on_dup_filter_changed(self, _checked: bool = False) -> None:
        """Réagit à un changement des coches Doublons / Similaires.

        L'analyse n'est lancée qu'une fois ; cocher/décocher une catégorie
        ensuite ne fait que re-filtrer la vue (pas de recalcul).
        """
        cats = self._current_dup_categories()
        self._dup_categories = cats
        if not cats:
            self._exit_duplicates_state()
            self._refresh_view()
            self.status.emit("")
            return
        if not self._media_by_path:
            for switch in (self.duplicates_switch, self.similars_switch):
                switch.blockSignals(True)
                switch.setChecked(False)
                switch.blockSignals(False)
            self._dup_categories = set()
            return
        if self._dup_index is None:
            if not self._dup_pending:  # une seule analyse à la fois
                self._dup_pending = True
                self.status.emit("Analyse des doublons et similaires…")
                worker = _DuplicatesWorker(list(self._media_by_path.keys()))
                worker.signals.finished.connect(self._on_duplicates_ready)
                QThreadPool.globalInstance().start(worker)
        else:
            self._active_group = None
            self._refresh_dup_view()

    def _on_duplicates_ready(self, index) -> None:
        self._dup_pending = False
        # L'utilisateur a pu tout décocher entre-temps : ignorer le résultat.
        if not self._current_dup_categories():
            return
        self._dup_index = index
        self._dup_color = dict(index.color_of)
        self._active_group = None
        self._refresh_dup_view()

    def _refresh_dup_view(self) -> None:
        """(Ré)affiche la vue doublons selon les catégories cochées + statut."""
        view = self._compute_view()
        if not view:
            self._display([])
            self._placeholder.setText(self._dup_empty_label())
            self._placeholder.show()
        else:
            self._display(view)
        self.status.emit(self._dup_status_message())

    def _dup_empty_label(self) -> str:
        cats = self._dup_categories
        if cats == {duplicates.COLOR_IDENTICAL}:
            return "Aucun doublon identique détecté."
        if cats == {duplicates.COLOR_SIMILAR}:
            return "Aucun fichier similaire détecté."
        return "Aucun doublon ni similaire détecté."

    def _dup_status_message(self) -> str:
        index = self._dup_index
        if index is None:
            return ""
        n_ident = sum(1 for c in index.color_of.values()
                      if c == duplicates.COLOR_IDENTICAL)
        n_sim = sum(1 for c in index.color_of.values()
                    if c == duplicates.COLOR_SIMILAR)
        parts = []
        if duplicates.COLOR_IDENTICAL in self._dup_categories:
            parts.append(f"{n_ident} identiques (rouge)")
        if duplicates.COLOR_SIMILAR in self._dup_categories:
            parts.append(f"{n_sim} similaires (orange)")
        return ("Doublons : " + ", ".join(parts)
                + ". Cliquez une vignette pour isoler son groupe.")

    def _filter_to_group(self, path: str) -> None:
        """Filtre la galerie sur le seul groupe de *path* (clic en mode doublons)."""
        if self._dup_index is None:
            return
        members = self._dup_index.members_of(path)
        if not members:
            return
        self._active_group = self._dup_index.group_of.get(path)
        media = [
            self._media_by_path[p] for p in members if p in self._media_by_path
        ]
        self._display(media)
        self.status.emit(
            f"Groupe isolé ({len(media)} fichiers). "
            "Décochez la case pour revenir à la vue complète."
        )

    def _exit_duplicates_state(self) -> None:
        """Réinitialise l'état du mode doublons (sans réafficher)."""
        self._dup_index = None
        self._dup_color = {}
        self._active_group = None
        self._dup_categories = set()
        self._dup_pending = False
