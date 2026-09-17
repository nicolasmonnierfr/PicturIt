"""Colonne gauche — Navigation.

Divisée en deux parties :
- Haut : bouton « Inclure les sous-dossiers » + arborescence de dossiers
  (explorateur Windows) via QFileSystemModel.
  - Clic gauche sur un dossier → le charge comme dossier source (contenu
    direct seulement ; le parcours récursif est un geste explicite).
  - Clic droit sur un dossier → menu « Épingler » (ajout à l'accès rapide).
  - Glisser-déposer de fichiers depuis la galerie → tri vers ce dossier.
- Bas : accès rapide (dossiers cibles épinglés, raccourcis 1-9).
  - Affiche chaque cible avec son numéro de raccourci.
  - Clic droit sur une cible → « Désépingler ».
  - Clic / raccourci 1-9 / glisser-déposer → active la cible.

Convention de tri : déplacer par défaut, **Shift = copier** (clic, raccourci
ou glisser-déposer).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QDir, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFileSystemModel,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSplitter,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ui.gallery_view import MIME_PATHS

# Nombre maximum de dossiers cibles épinglés (limite des raccourcis 1-9).
MAX_PINNED = 9

# Bouton « Inclure les sous-dossiers » : enfoncé quand le parcours récursif
# est actif. L'ambre reprend celui des autres états à conséquence de l'app
# (badge « sans GPS », mode « Remplacer ») — ici un scan potentiellement long.
_RECURSIVE_BUTTON_STYLE = (
    "QPushButton:checked { color:#eb911e; font-weight:bold; }"
)

# Rôle de données stockant le chemin absolu sur un item d'accès rapide.
_PATH_ROLE = Qt.ItemDataRole.UserRole + 1


def _mime_paths(mime) -> list[str]:
    """Extrait la liste des chemins transportés par un drag de la galerie."""
    raw = bytes(mime.data(MIME_PATHS)).decode("utf-8")
    return [p for p in raw.split("\n") if p]


def _is_copy_modifier() -> bool:
    """True si Shift est maintenu (copier au lieu de déplacer)."""
    return bool(
        QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier
    )


class _DropTreeView(QTreeView):
    """Arborescence acceptant le dépôt de fichiers sur un dossier."""

    # (paths, dossier cible, copier ?)
    files_dropped = Signal(list, str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)

    def _dest_dir(self, pos) -> str | None:
        index = self.indexAt(pos)
        model = self.model()
        if index.isValid() and model is not None and model.isDir(index):
            return model.filePath(index)
        return None

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if event.mimeData().hasFormat(MIME_PATHS):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if event.mimeData().hasFormat(MIME_PATHS) and self._dest_dir(
            event.position().toPoint()
        ):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (API Qt)
        dest = self._dest_dir(event.position().toPoint())
        if not event.mimeData().hasFormat(MIME_PATHS) or dest is None:
            event.ignore()
            return
        self.files_dropped.emit(_mime_paths(event.mimeData()), dest, _is_copy_modifier())
        event.acceptProposedAction()


class _DropQuickList(QListWidget):
    """Accès rapide acceptant le dépôt de fichiers sur une cible épinglée."""

    # (paths, dossier cible, copier ?)
    files_dropped = Signal(list, str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self.setDropIndicatorShown(True)

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (API Qt)
        if event.mimeData().hasFormat(MIME_PATHS):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (API Qt)
        item = self.itemAt(event.position().toPoint())
        if event.mimeData().hasFormat(MIME_PATHS) and item is not None:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # noqa: N802 (API Qt)
        item = self.itemAt(event.position().toPoint())
        if not event.mimeData().hasFormat(MIME_PATHS) or item is None:
            event.ignore()
            return
        dest = item.data(_PATH_ROLE)
        self.files_dropped.emit(_mime_paths(event.mimeData()), dest, _is_copy_modifier())
        event.acceptProposedAction()


class NavPanel(QWidget):
    """Panneau de navigation : arborescence + accès rapide."""

    # Émis quand l'utilisateur choisit un dossier source :
    # (chemin absolu, inclure les sous-dossiers ?).
    # Le clic simple n'explore **jamais** les sous-dossiers : parcourir
    # l'arborescence doit rester instantané, y compris sur une racine de disque.
    source_changed = Signal(str, bool)
    # Émis quand une cible est activée (clic ou raccourci) : (path, copier).
    target_activated = Signal(str, bool)
    # Émis lors d'un glisser-déposer de fichiers : (paths, cible, copier).
    files_dropped = Signal(list, str, bool)
    # Bouton « Inclure les sous-dossiers » : la fenêtre principale décide
    # quoi en faire (elle seule connaît l'état de la galerie).
    recursive_toggled = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Liste ordonnée des chemins épinglés (index + 1 = numéro de raccourci).
        self._pinned: list[str] = []

        splitter = QSplitter(Qt.Orientation.Vertical, self)

        # --- Haut : arborescence de dossiers ---
        tree_container = QWidget()
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(4, 4, 4, 4)
        tree_layout.addWidget(QLabel("Arborescence"))

        # Le parcours récursif est placé juste au-dessus de l'arborescence,
        # là où se fait le choix du dossier, plutôt que dans la barre du haut.
        # Bouton à deux états plutôt qu'à libellé changeant : il reste enfoncé
        # tant que les sous-dossiers sont inclus, et son intitulé ne bouge pas.
        self.recursive_button = QPushButton("Inclure les sous-dossiers")
        self.recursive_button.setCheckable(True)
        self.recursive_button.setStyleSheet(_RECURSIVE_BUTTON_STYLE)
        self.recursive_button.setToolTip(
            "Parcourir toute l'arborescence du dossier source.\n"
            "Peut prendre plusieurs minutes sur un disque entier ; "
            "l'analyse reste interruptible."
        )
        self.recursive_button.setEnabled(False)  # aucun dossier source au départ
        self.recursive_button.clicked.connect(self.recursive_toggled)
        tree_layout.addWidget(self.recursive_button)

        self._fs_model = QFileSystemModel(self)
        self._fs_model.setRootPath("")
        self._fs_model.setFilter(
            QDir.Filter.Dirs | QDir.Filter.Drives | QDir.Filter.NoDotAndDotDot
        )

        self.tree_view = _DropTreeView()
        self.tree_view.setModel(self._fs_model)
        for col in range(1, self._fs_model.columnCount()):
            self.tree_view.hideColumn(col)
        self.tree_view.setHeaderHidden(True)
        self.tree_view.clicked.connect(self._on_tree_clicked)
        # Accessibilité clavier : Entrée sur un dossier le charge aussi comme source.
        self.tree_view.activated.connect(self._on_tree_clicked)
        self.tree_view.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.tree_view.customContextMenuRequested.connect(self._on_tree_menu)
        self.tree_view.files_dropped.connect(self.files_dropped)
        tree_layout.addWidget(self.tree_view)
        splitter.addWidget(tree_container)

        # --- Bas : accès rapide (dossiers cibles épinglés) ---
        quick_container = QWidget()
        quick_layout = QVBoxLayout(quick_container)
        quick_layout.setContentsMargins(4, 4, 4, 4)
        quick_layout.addWidget(QLabel("Accès rapide (cibles de tri)"))
        self.quick_list = _DropQuickList()
        self.quick_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.quick_list.customContextMenuRequested.connect(self._on_quick_menu)
        self.quick_list.itemClicked.connect(self._on_quick_clicked)
        self.quick_list.files_dropped.connect(self.files_dropped)
        quick_layout.addWidget(self.quick_list)

        self._new_target_button = QPushButton("+ Nouveau dossier cible…")
        self._new_target_button.setToolTip(
            "Choisir/créer un dossier et l'épingler comme cible de tri"
        )
        self._new_target_button.clicked.connect(self._on_new_target)
        quick_layout.addWidget(self._new_target_button)
        splitter.addWidget(quick_container)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

    # --- Bouton de parcours récursif ---
    def set_recursive_available(self, available: bool) -> None:
        """Active le bouton une fois qu'un dossier source est choisi."""
        self.recursive_button.setEnabled(available)

    def set_recursive_state(self, recursive: bool) -> None:
        """Reflète l'état réel de la galerie sur le bouton.

        ``setChecked`` n'émet pas ``clicked`` : appeler cette méthode depuis la
        fenêtre principale ne relance donc aucun scan.
        """
        self.recursive_button.setChecked(recursive)

    # --- Arborescence (source + épinglage) ---
    def _on_tree_clicked(self, index) -> None:
        """Clic gauche sur un dossier → le charge, **sans** ses sous-dossiers.

        Explorer récursivement est une action coûteuse (plusieurs minutes sur
        un disque entier) : elle doit rester un geste délibéré, jamais un effet
        de bord de la navigation.
        """
        if self._fs_model.isDir(index):
            self.source_changed.emit(self._fs_model.filePath(index), False)

    def _on_tree_menu(self, pos) -> None:
        """Menu contextuel de l'arborescence : épingler ou charger récursivement."""
        index = self.tree_view.indexAt(pos)
        if not index.isValid() or not self._fs_model.isDir(index):
            return
        path = self._fs_model.filePath(index)
        menu = QMenu(self)
        action_recursif = menu.addAction("Charger avec les sous-dossiers")
        action_recursif.setToolTip(
            "Parcourt toute l'arborescence : peut être long sur un disque entier"
        )
        menu.addSeparator()
        action = menu.addAction("Épingler comme cible de tri")
        if path in self._pinned:
            action.setEnabled(False)
            action.setText("Déjà épinglé")
        elif len(self._pinned) >= MAX_PINNED:
            action.setEnabled(False)
            action.setText("Épingler (maximum de 9 cibles atteint)")
        chosen = menu.exec(self.tree_view.viewport().mapToGlobal(pos))
        if chosen is action:
            self.pin(path)
        elif chosen is action_recursif:
            self.source_changed.emit(path, True)

    def select_path(self, path: str) -> None:
        """Sélectionne et déplie *path* dans l'arborescence (depuis Parcourir)."""
        index = self._fs_model.index(path)
        if index.isValid():
            self.tree_view.setCurrentIndex(index)
            self.tree_view.scrollTo(index)
            self.tree_view.expand(index)

    # --- Accès rapide (épinglage / désépinglage) ---
    def pin(self, path: str) -> bool:
        """Épingle *path* comme cible. Renvoie False si refusé (doublon/limite)."""
        if path in self._pinned or len(self._pinned) >= MAX_PINNED:
            return False
        self._pinned.append(path)
        self._rebuild_quick_list()
        return True

    def unpin(self, path: str) -> None:
        """Retire *path* des cibles épinglées et renumérote l'accès rapide."""
        if path in self._pinned:
            self._pinned.remove(path)
            self._rebuild_quick_list()

    def pinned_paths(self) -> list[str]:
        """Renvoie la liste ordonnée des cibles épinglées (copie)."""
        return list(self._pinned)

    def _rebuild_quick_list(self) -> None:
        """Reconstruit l'affichage de l'accès rapide avec la numérotation 1-9."""
        self.quick_list.clear()
        for i, path in enumerate(self._pinned, start=1):
            item = QListWidgetItem(f"{i}   {os.path.basename(path) or path}")
            item.setData(_PATH_ROLE, path)
            item.setToolTip(path)
            self.quick_list.addItem(item)

    def _on_quick_menu(self, pos) -> None:
        """Menu contextuel d'une cible : désépingler."""
        item = self.quick_list.itemAt(pos)
        if item is None:
            return
        menu = QMenu(self)
        action = menu.addAction("Désépingler")
        chosen = menu.exec(self.quick_list.viewport().mapToGlobal(pos))
        if chosen is action:
            self.unpin(item.data(_PATH_ROLE))

    def _on_quick_clicked(self, item: QListWidgetItem) -> None:
        """Clic sur une cible → l'active (Shift = copier au lieu de déplacer)."""
        self.target_activated.emit(item.data(_PATH_ROLE), _is_copy_modifier())

    def activate_target(self, number: int, copier: bool) -> None:
        """Active la cible n° *number* (1-9) si elle existe (raccourci clavier)."""
        if 1 <= number <= len(self._pinned):
            self.target_activated.emit(self._pinned[number - 1], copier)

    def _on_new_target(self) -> None:
        """Choisit/crée un dossier (le dialogue permet d'en créer un) et l'épingle."""
        if len(self._pinned) >= MAX_PINNED:
            return
        folder = QFileDialog.getExistingDirectory(
            self, "Choisir ou créer un dossier cible"
        )
        if folder:
            self.pin(folder)
