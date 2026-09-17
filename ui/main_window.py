"""Fenêtre principale — layout 3 colonnes + barre supérieure + barre inférieure.

Orchestre les panneaux et les opérations de tri :
- Barre supérieure : chemin source + bouton Parcourir (la bascule
  Aperçu/Carte est dans la barre du panneau droit).
- 3 colonnes redimensionnables (QSplitter) : navigation / galerie / panneau droit.
- Barre inférieure : rappel des raccourcis + barre de progression.
- Tri : clic/raccourci 1-9/glisser-déposer → déplacer (Shift = copier).
- Suppr = corbeille, Ctrl+Z = annuler, export du journal à la fermeture.
"""

import os
import shutil

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from core import editing, metadata, scanner
from core.operations import OperationManager
from ui.fullscreen import FullScreenViewer
from ui.gallery_view import GalleryView
from ui.map_panel import MapPanel
from ui.nav_panel import NavPanel
from ui.preview_panel import PreviewPanel


class MainWindow(QMainWindow):
    """Fenêtre principale de l'application."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PicturIt")
        self.resize(1280, 800)

        # Gestionnaire des opérations de tri (move/copy/trash + undo + journal).
        self.operations = OperationManager()
        # Mode « copier » : source -> copie de travail cumulative (_copie).
        self._edit_working: dict[str, str] = {}
        # Avertissement du mode « Remplacer » : montré une seule fois par
        # session (rien n'est persisté sur disque, cf. SPEC 2).
        self._replace_warned = False
        # Plein écran : héberge le PreviewPanel existant (boutons déjà câblés).
        self._fullscreen = FullScreenViewer()
        self._fullscreen.nav.connect(self._on_fullscreen_nav)
        self._fullscreen.closed.connect(self._exit_fullscreen)
        self._fs_active = False

        central = QWidget()
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(6, 6, 6, 6)

        root_layout.addLayout(self._build_top_bar())
        root_layout.addWidget(self._build_columns(), stretch=1)

        self.setCentralWidget(central)
        self._build_bottom_bar()
        self._connect_signals()
        self._setup_shortcuts()

    # --- Barre supérieure ---
    def _build_top_bar(self) -> QHBoxLayout:
        """Construit la barre supérieure : chemin source + switch de panneau."""
        top_bar = QHBoxLayout()

        top_bar.addWidget(QLabel("Source :"))
        self.source_path_edit = QLineEdit()
        self.source_path_edit.setPlaceholderText("Aucun dossier source sélectionné")
        self.source_path_edit.setReadOnly(True)
        top_bar.addWidget(self.source_path_edit, stretch=1)

        self.browse_button = QPushButton("Parcourir…")
        self.browse_button.setToolTip("Choisir le dossier source")
        self.browse_button.clicked.connect(self._on_browse)
        top_bar.addWidget(self.browse_button)

        # La bascule Aperçu/Carte vit désormais dans la barre du panneau droit,
        # au-dessus de ce qu'elle commande (cf. PreviewPanel.panel_switch).
        return top_bar

    # --- 3 colonnes ---
    def _build_columns(self) -> QSplitter:
        """Construit les 3 colonnes redimensionnables."""
        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.nav_panel = NavPanel()
        self.gallery_view = GalleryView()
        self.preview_panel = PreviewPanel()
        self.map_panel = MapPanel()
        # Bascule Aperçu/Carte : construite par le panneau droit, pilotée ici.
        self.map_switch = self.preview_panel.panel_switch

        # Colonne centrale : pile galerie (0) / carte agrandie (1).
        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(self.gallery_view)
        self.center_map_host = QWidget()
        self._center_map_layout = QVBoxLayout(self.center_map_host)
        self._center_map_layout.setContentsMargins(0, 0, 0, 0)
        self.center_stack.addWidget(self.center_map_host)

        # La carte est initialement logée dans le panneau droit (mode Carte).
        self.preview_panel.map_container.layout().addWidget(self.map_panel)

        splitter.addWidget(self.nav_panel)
        splitter.addWidget(self.center_stack)
        splitter.addWidget(self.preview_panel)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 5)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([260, 640, 380])

        self._splitter = splitter  # mémorisé pour le retour de plein écran
        return splitter

    # --- Barre inférieure ---
    def _build_bottom_bar(self) -> None:
        """Affiche le rappel des raccourcis dans la barre de statut."""
        status = QStatusBar()
        status.showMessage(
            "Raccourcis : 1-9 = envoyer vers dossier épinglé  •  "
            "Shift = copier au lieu de déplacer  •  "
            "Suppr = corbeille  •  Ctrl+Z = annuler"
        )
        # Résumé permanent (dossier / sélection).
        self.summary_label = QLabel("")
        status.addPermanentWidget(self.summary_label)
        # Barre de progression du scan/vignettes (cachée au repos).
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(220)
        self.progress_bar.setVisible(False)
        status.addPermanentWidget(self.progress_bar)
        self.setStatusBar(status)

    # --- Câblage des signaux entre panneaux ---
    def _connect_signals(self) -> None:
        self.nav_panel.source_changed.connect(self._set_source)
        self.nav_panel.target_activated.connect(self._on_target_activated)
        self.nav_panel.files_dropped.connect(self._on_files_dropped)
        self.nav_panel.recursive_toggled.connect(self._on_toggle_recursive)
        self.gallery_view.media_selected.connect(self.preview_panel.show_media)
        self.gallery_view.loading_progress.connect(self._on_loading_progress)
        self.gallery_view.loading_finished.connect(self._on_loading_finished)
        self.gallery_view.status.connect(self._on_gallery_status)
        self.gallery_view.rename_requested.connect(self._on_rename_requested)
        self.gallery_view.rotate_selection_requested.connect(self._on_rotate_selection)
        self.gallery_view.summary_changed.connect(self.summary_label.setText)
        self.gallery_view.fullscreen_requested.connect(self._open_fullscreen_at)
        # Édition photo (rotation / recadrage / conversion).
        self.preview_panel.rotate_requested.connect(self._on_edit_rotate)
        self.preview_panel.crop_committed.connect(self._on_edit_crop)
        self.preview_panel.convert_requested.connect(self._on_edit_convert)
        self.map_switch.toggled.connect(self._on_toggle_map)
        # Carte ⇄ galerie (sélection bidirectionnelle + marqueurs).
        self.gallery_view.geo_points_changed.connect(self.map_panel.set_points)
        self.gallery_view.selection_changed.connect(self.map_panel.set_highlight)
        # Clic sur un marqueur/cluster → filtre la galerie sur ces photos
        # (masque les autres) plutôt que de simplement les sélectionner.
        self.map_panel.markers_selected.connect(self.gallery_view.filter_to_paths)
        self.map_panel.enlarge_toggled.connect(self._on_map_enlarge)

    def _setup_shortcuts(self) -> None:
        """Installe les raccourcis : 1-9 (déplacer), Shift+1-9 (copier),
        Suppr (corbeille) et Ctrl+Z (annuler)."""
        for number in range(1, 10):
            QShortcut(
                QKeySequence(str(number)),
                self,
                activated=lambda n=number: self.nav_panel.activate_target(n, False),
            )
            QShortcut(
                QKeySequence(f"Shift+{number}"),
                self,
                activated=lambda n=number: self.nav_panel.activate_target(n, True),
            )
        QShortcut(QKeySequence.StandardKey.Delete, self, activated=self._on_delete)
        QShortcut(QKeySequence.StandardKey.Undo, self, activated=self._on_undo)
        QShortcut(QKeySequence.StandardKey.SelectAll, self,
                  activated=self.gallery_view.select_all)
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self,
                  activated=self.gallery_view.clear_all_selection)
        QShortcut(QKeySequence(Qt.Key.Key_F), self, activated=self._open_fullscreen)

    def _set_source(self, folder: str, recursive: bool = False) -> None:
        """Définit le dossier source et déclenche le chargement de la galerie.

        *recursive* n'est vrai que sur demande explicite (bouton de la barre
        supérieure ou menu contextuel de l'arborescence) : la simple navigation
        ne doit jamais lancer un parcours qui peut durer des minutes.
        """
        self._edit_working.clear()
        self.source_path_edit.setText(folder)
        self.nav_panel.set_recursive_available(True)
        self.nav_panel.set_recursive_state(recursive)
        self.statusBar().showMessage(
            f"Analyse de {folder} et de ses sous-dossiers…"
            if recursive
            else f"Lecture de {folder}…"
        )
        self.gallery_view.load_media(folder, recursive)

    def _on_toggle_recursive(self) -> None:
        """Bouton de l'arborescence : étend le scan, ou revient au dossier seul."""
        folder = self.gallery_view.source_root()
        if not folder:
            return
        # Une analyse en cours est d'abord interrompue (elle deviendrait caduque).
        if self.gallery_view.is_scanning():
            self.gallery_view.cancel_scan()
        self._set_source(folder, not self.gallery_view.is_recursive())

    # --- Slots ---
    def _on_browse(self) -> None:
        """Ouvre un sélecteur de dossier pour choisir la source."""
        folder = QFileDialog.getExistingDirectory(self, "Choisir le dossier source")
        if folder:
            self.nav_panel.select_path(folder)
            self._set_source(folder)

    def _on_loading_progress(self, done: int, total: int) -> None:
        """Met à jour la barre de progression des vignettes."""
        if total <= 0:
            self.progress_bar.setVisible(False)
            return
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(total)
        self.progress_bar.setValue(done)
        self.progress_bar.setFormat("Vignettes %v / %m")

    def _on_loading_finished(self) -> None:
        """Cache la barre de progression une fois le chargement terminé."""
        self.progress_bar.setVisible(False)
        self.statusBar().showMessage("Chargement terminé.", 4000)
        self.summary_label.setText(self.gallery_view.folder_summary())

    def _open_fullscreen_at(self, path: str) -> None:
        """Affiche *path* en plein écran en réutilisant le panneau d'aperçu."""
        # Sélectionne la photo (l'aperçu se met à jour via media_selected).
        self.gallery_view.select_paths([path])
        self._enter_fullscreen()

    def _open_fullscreen(self) -> None:
        """Touche F : plein écran sur la sélection courante (ou le 1er média)."""
        selected = self.gallery_view.selected_paths()
        ordered = self.gallery_view.ordered_paths()
        if not ordered:
            return
        self._open_fullscreen_at(selected[0] if selected else ordered[0])

    def _enter_fullscreen(self) -> None:
        """Reparente le panneau d'aperçu dans la fenêtre plein écran."""
        if self._fs_active:
            return
        # En plein écran on affiche l'aperçu (image), jamais la carte : on
        # repasse donc le switch sur « Aperçu » pour garder la cohérence
        # affichage ⇄ switch (sinon il resterait bloqué sur « Carte »).
        if self.map_switch.isChecked():
            # Déclenche _on_toggle_map(False) : réduit la carte + mode Aperçu.
            self.map_switch.setChecked(False)
        else:
            self.map_panel.collapse()
            self.preview_panel.set_mode(PreviewPanel.MODE_PREVIEW)
        self._fullscreen.set_content(self.preview_panel)  # reparentage
        self._fs_active = True
        self._fullscreen.showFullScreen()

    def _exit_fullscreen(self) -> None:
        """Replace le panneau d'aperçu dans la 3e colonne."""
        if not self._fs_active:
            return
        self._fs_active = False
        self._splitter.addWidget(self.preview_panel)  # réinsère en 3e colonne
        self.preview_panel.show()

    def _on_fullscreen_nav(self, delta: int) -> None:
        """Navigation ←/→ en plein écran : change la sélection (et donc l'aperçu)."""
        self.gallery_view.select_relative(delta)

    def _confirm_rotate_in_place(self, paths: list[str]) -> bool:
        """Confirme la rotation **seulement quand elle dégrade** les fichiers.

        La rotation JPEG ne touche pas aux pixels : elle réécrit le tag EXIF
        Orientation, donc elle est sans perte et s'annule en pivotant dans
        l'autre sens. Demander confirmation dans ce cas ne ferait qu'habituer
        l'utilisateur à cliquer « Oui » sans lire.

        Les autres formats (PNG, BMP…) sont ré-encodés pixel par pixel : là, la
        perte est réelle et définitive.
        """
        reencodes = [
            p for p in paths
            if os.path.splitext(p)[1].lower() not in editing.JPEG_EXTENSIONS
        ]
        if not reencodes:
            return True

        reply = QMessageBox.warning(
            self,
            "Pivoter en place",
            f"{len(reencodes)} fichier(s) sur {len(paths)} ne sont pas des JPEG "
            "et seront ré-encodés : la rotation dégrade l'image et n'est pas "
            "annulable (Ctrl+Z ne couvre pas les éditions).\n\n"
            "Les JPEG, eux, pivotent sans perte.\n\n"
            "Continuer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return reply == QMessageBox.StandardButton.Yes

    def _on_rotate_selection(self, clockwise: bool) -> None:
        """Pivote les photos sélectionnées depuis la galerie, **en place**.

        Ce chemin ne passe pas par le switch Remplacer/Copier de l'aperçu : il
        écrit toujours sur les fichiers d'origine. Il peut en toucher beaucoup
        d'un coup, d'où une confirmation propre, indépendante de celle du mode
        Remplacer.
        """
        paths = [p for p in self.gallery_view.selected_paths()
                 if not scanner.is_video(p)]
        if not paths:
            self.statusBar().showMessage("Aucune photo à pivoter.", 4000)
            return
        if not self._confirm_rotate_in_place(paths):
            return
        done = 0
        for path in paths:
            try:
                editing.rotate(path, clockwise)
                self.operations.log_edit("Pivoter", path, path)
                self.gallery_view.invalidate_thumbnail(path)
                done += 1
            except Exception:  # noqa: BLE001
                continue
        self.statusBar().showMessage(f"{done} photo(s) pivotée(s).", 5000)

    def _on_gallery_status(self, message: str) -> None:
        """Affiche un message d'état émis par la galerie (mode doublons…)."""
        if message:
            self.statusBar().showMessage(message, 8000)

    def _on_target_activated(self, dest: str, copier: bool) -> None:
        """Cible activée (clic/raccourci) → trie la sélection courante."""
        paths = self.gallery_view.selected_paths()
        self._sort_to(paths, dest, copier)

    def _on_files_dropped(self, paths: list, dest: str, copier: bool) -> None:
        """Fichiers glissés-déposés sur une cible (accès rapide ou arborescence)."""
        self._sort_to(list(paths), dest, copier)

    def _sort_to(self, paths: list[str], dest: str, copier: bool) -> None:
        """Déplace (ou copie si *copier*) *paths* vers *dest* et met à jour l'UI."""
        if not paths:
            self.statusBar().showMessage("Aucune sélection à trier.", 4000)
            return
        if copier:
            changes = self.operations.copy(paths, dest)
            verb = "copié(s)"
        else:
            changes = self.operations.move(paths, dest)
            verb = "déplacé(s)"
        self.gallery_view.apply_changes(changes)
        self.statusBar().showMessage(
            f"{len(changes)} fichier(s) {verb} vers {dest}.", 5000
        )

    def _on_rename_requested(self, mode: str) -> None:
        """Renomme la sélection : ajout d'un préfixe, ou nom depuis la date EXIF."""
        paths = self.gallery_view.selected_paths()
        if not paths:
            self.statusBar().showMessage("Aucune sélection à renommer.", 4000)
            return

        items: list[tuple[str, str]] = []
        skipped = 0
        if mode == "prefix":
            prefix, ok = QInputDialog.getText(
                self, "Renommer — préfixe", "Préfixe à ajouter au nom :"
            )
            if not ok or not prefix:
                return
            items = [
                (path, f"{prefix}{os.path.basename(path)}") for path in paths
            ]
        elif mode == "datetime":
            for path in paths:
                dt = metadata.read(path).datetime_original
                if dt is None:
                    skipped += 1
                    continue
                ext = os.path.splitext(path)[1]
                items.append((path, f"{dt.strftime('%Y%m%d_%H%M%S')}{ext}"))

        changes = self.operations.rename(items)
        self.gallery_view.apply_changes(changes)
        message = f"{len(changes)} fichier(s) renommé(s)."
        if skipped:
            message += f" {skipped} ignoré(s) (pas de date de prise de vue)."
        self.statusBar().showMessage(message, 6000)

    # --- Édition photo (rotation / recadrage / conversion) ---
    def _confirm_replace_mode(self) -> bool:
        """Prévient, **une fois par session**, que le mode Remplacer est définitif.

        L'édition n'est pas couverte par Ctrl+Z (seuls move/copy/rename le sont)
        et aucune sauvegarde de l'original n'est faite : un recadrage ou une
        conversion écrase le fichier pour de bon.

        Une seule fois : redemander à chaque rotation rendrait l'outil pénible,
        et l'indicateur ambre du switch reste visible en permanence.
        """
        if self._replace_warned:
            return True
        reply = QMessageBox.warning(
            self,
            "Modifier le fichier d'origine",
            "Le mode « Remplacer » écrit directement sur vos fichiers.\n\n"
            "Ctrl+Z n'annule pas les éditions et aucune copie de l'original "
            "n'est conservée : un recadrage ou une conversion est définitif.\n\n"
            "Passez sur « Copier » pour travailler sur une copie.\n\n"
            "Continuer en mode Remplacer ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return False
        # Averti pour cette session : l'utilisateur sait désormais à quoi s'en tenir.
        self._replace_warned = True
        return True

    def _edit_target(self, src: str) -> str | None:
        """Renvoie le fichier à éditer selon le mode (remplacer/copier).

        En mode copier, crée (une seule fois par source) une copie ``_copie`` sur
        laquelle s'accumulent les éditions, et l'ajoute à la galerie.
        """
        if self.preview_panel.edit_mode() == "replace":
            return src if self._confirm_replace_mode() else None
        # Le fichier courant est déjà une copie de travail : éditer en place
        # (les opérations s'accumulent sur la même copie _copie).
        if src in self._edit_working.values():
            return src
        work = self._edit_working.get(src)
        if work and os.path.exists(work):
            return work
        base, ext = os.path.splitext(src)
        candidate = f"{base}_copie{ext}"
        i = 1
        while os.path.exists(candidate):
            candidate = f"{base}_copie_{i}{ext}"
            i += 1
        try:
            shutil.copy2(src, candidate)
        except OSError as exc:
            QMessageBox.warning(self, "Échec de la copie", str(exc))
            return None
        self._edit_working[src] = candidate
        self.gallery_view.add_existing_file(candidate)
        return candidate

    def _on_edit_rotate(self, clockwise: bool) -> None:
        src = self.preview_panel.current_path()
        if not src:
            return
        target = self._edit_target(src)
        if target is None:
            return
        try:
            editing.rotate(target, clockwise)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Rotation impossible", str(exc))
            return
        self.operations.log_edit("Pivoter", src, target)
        self._after_edit(target, "Image pivotée")

    def _on_edit_crop(self, box) -> None:
        if box is None:
            self.statusBar().showMessage(
                "Sélectionnez d'abord une zone à recadrer (glisser sur l'image).", 5000
            )
            return
        src = self.preview_panel.current_path()
        if not src:
            return
        target = self._edit_target(src)
        if target is None:
            return
        try:
            editing.crop(target, box)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Recadrage impossible", str(exc))
            return
        self.operations.log_edit("Recadrer", src, target)
        self._after_edit(target, "Image recadrée")

    def _on_edit_convert(self, ext: str) -> None:
        src = self.preview_panel.current_path()
        if not src:
            return
        target = self._edit_target(src)
        if target is None:
            return
        try:
            dst = editing.convert(target, ext)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Conversion impossible", str(exc))
            return
        self.operations.log_edit("Convertir", target, dst)

        if os.path.normcase(dst) != os.path.normcase(target):
            # Le fichier d'origine (autre extension) est remplacé : corbeille.
            changes = self.operations.trash([target])
            self.gallery_view.apply_changes(changes)
            self.gallery_view.add_existing_file(dst)
            if self.preview_panel.edit_mode() == "copy":
                self._edit_working[src] = dst
        else:
            self.gallery_view.invalidate_thumbnail(dst)
        self._after_edit(dst, f"Image convertie en {ext[1:].upper()}")

    def _after_edit(self, path: str, message: str) -> None:
        """Rafraîchit la vignette et l'aperçu après une édition."""
        self.gallery_view.invalidate_thumbnail(path)
        self.preview_panel.show_media(path)
        self.statusBar().showMessage(f"{message} : {os.path.basename(path)}", 5000)

    def _on_delete(self) -> None:
        """Suppr → envoie la sélection à la corbeille Windows."""
        paths = self.gallery_view.selected_paths()
        if not paths:
            return
        # Confirmation pour une suppression multiple (geste plus engageant).
        if len(paths) > 1:
            reply = QMessageBox.question(
                self,
                "Envoyer à la corbeille",
                f"Envoyer {len(paths)} fichiers à la corbeille Windows ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        changes = self.operations.trash(paths)
        self.gallery_view.apply_changes(changes)
        self.statusBar().showMessage(
            f"{len(changes)} fichier(s) envoyé(s) à la corbeille.", 5000
        )

    def _on_undo(self) -> None:
        """Ctrl+Z → annule la dernière opération de tri (déplacer/copier)."""
        if not self.operations.can_undo():
            self.statusBar().showMessage("Rien à annuler.", 4000)
            return
        label = self.operations.last_undo_label()
        changes = self.operations.undo()
        self.gallery_view.apply_changes(changes)
        self.statusBar().showMessage(f"Annulé : {label}.", 5000)

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        """À la fermeture : proposer d'exporter le journal des opérations.

        C'est la seule écriture disque autorisée hors opérations de tri
        (cf. SPEC 5.2 / 8). Si l'utilisateur refuse, rien n'est écrit.
        """
        # Laisse les workers de vignettes se terminer pour éviter des erreurs
        # « Signal source has been deleted » à la destruction de Qt.
        from PySide6.QtCore import QThreadPool

        from core import thumbnails

        # Prévenir avant d'attendre : les workers en vol renoncent d'eux-mêmes
        # au lieu d'émettre vers des objets que Qt va détruire.
        thumbnails.request_shutdown()
        QThreadPool.globalInstance().clear()
        QThreadPool.globalInstance().waitForDone(2000)

        if not self.operations.log:
            event.accept()
            return
        reply = QMessageBox.question(
            self,
            "Exporter le journal ?",
            f"{len(self.operations.log)} opération(s) effectuée(s) durant la "
            "session.\nExporter le journal dans un fichier texte ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            path, _ = QFileDialog.getSaveFileName(
                self, "Exporter le journal", "journal_tri.txt", "Texte (*.txt)"
            )
            if path:
                try:
                    self.operations.export_log(path)
                except OSError as exc:
                    QMessageBox.warning(
                        self, "Échec de l'export", f"Impossible d'écrire : {exc}"
                    )
        event.accept()

    def _on_toggle_map(self, checked: bool) -> None:
        """Bascule le panneau droit entre Aperçu et Carte."""
        if checked:
            self.preview_panel.set_mode(PreviewPanel.MODE_MAP)
            # Recalcule la taille de la carte une fois le panneau affiché.
            QTimer.singleShot(60, self.map_panel.refresh)
        else:
            # Si la carte était agrandie, la replier d'abord (galerie au centre).
            self.map_panel.collapse()
            self.preview_panel.set_mode(PreviewPanel.MODE_PREVIEW)

    def _on_map_enlarge(self, enlarged: bool) -> None:
        """Agrandit la carte au centre (par-dessus la galerie) ou la réduit."""
        if enlarged:
            self._center_map_layout.addWidget(self.map_panel)  # reparente au centre
            self.center_stack.setCurrentWidget(self.center_map_host)
        else:
            self.preview_panel.map_container.layout().addWidget(self.map_panel)
            self.center_stack.setCurrentWidget(self.gallery_view)
            # S'assure que le mode Carte reste actif dans le panneau droit.
            if self.map_switch.isChecked():
                self.preview_panel.set_mode(PreviewPanel.MODE_MAP)
        QTimer.singleShot(60, self.map_panel.refresh)
