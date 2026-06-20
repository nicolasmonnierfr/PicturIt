"""Opérations de tri : déplacer / copier / corbeille + annulation + journal.

- Toutes les opérations sont journalisées en mémoire (horodatées).
- Le déplacement et la copie sont **annulables** (pile d'annulation multi-niveaux).
- La suppression passe **toujours** par la corbeille Windows (send2trash) et
  n'est pas annulable côté application (cf. SPEC 4.6 / 5.1).
- Aucune écriture disque hors des opérations de tri elles-mêmes ; le journal
  n'est écrit que sur demande explicite (export, cf. SPEC 5.2 / 8).

Chaque opération renvoie une liste de ``FileChange`` décrivant ce qui a changé,
afin que la galerie se mette à jour de façon incrémentale.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from datetime import datetime

try:
    from send2trash import send2trash
except Exception:  # noqa: BLE001 — l'app ne doit pas planter si l'import échoue
    send2trash = None


# Genres de changement signalés à la galerie.
MOVED = "moved"      # src -> dst (le fichier a changé d'emplacement)
COPIED = "copied"    # src reste, une copie dst est créée
REMOVED = "removed"  # src n'existe plus à cet emplacement


@dataclass(slots=True)
class FileChange:
    """Décrit un changement à répercuter dans la galerie."""

    kind: str
    src: str
    dst: str | None = None


@dataclass(slots=True)
class LogEntry:
    """Une ligne du journal des opérations (horodatée)."""

    timestamp: str
    action: str
    source: str
    destination: str


@dataclass(slots=True)
class _Batch:
    """Lot d'opérations annulables (inverse à rejouer pour annuler)."""

    label: str
    inverse: list[FileChange] = field(default_factory=list)


def _unique_dest(dest_dir: str, filename: str) -> str:
    """Renvoie un chemin de destination libre dans *dest_dir* (suffixe _1, _2…)."""
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(dest_dir, filename)
    i = 1
    while os.path.exists(candidate):
        candidate = os.path.join(dest_dir, f"{base}_{i}{ext}")
        i += 1
    return candidate


class OperationManager:
    """Gère les opérations de tri, leur journal et l'annulation."""

    def __init__(self) -> None:
        self.log: list[LogEntry] = []
        self._undo_stack: list[_Batch] = []

    # --- État ---
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def last_undo_label(self) -> str:
        return self._undo_stack[-1].label if self._undo_stack else ""

    # --- Opérations ---
    def move(self, paths: list[str], dest_dir: str) -> list[FileChange]:
        """Déplace *paths* vers *dest_dir*. Renvoie les changements effectués."""
        changes: list[FileChange] = []
        inverse: list[FileChange] = []
        dest_norm = os.path.normcase(os.path.abspath(dest_dir))

        for src in paths:
            if not os.path.exists(src):
                continue
            # Inutile de déplacer un fichier déjà dans le dossier cible.
            if os.path.normcase(os.path.abspath(os.path.dirname(src))) == dest_norm:
                continue
            dst = _unique_dest(dest_dir, os.path.basename(src))
            try:
                os.makedirs(dest_dir, exist_ok=True)
                shutil.move(src, dst)
            except OSError:
                continue
            changes.append(FileChange(MOVED, src, dst))
            inverse.append(FileChange(MOVED, dst, src))  # inverse : ramener dst -> src
            self._log("Déplacer", src, dst)

        if inverse:
            self._undo_stack.append(_Batch("Déplacement", list(reversed(inverse))))
        return changes

    def copy(self, paths: list[str], dest_dir: str) -> list[FileChange]:
        """Copie *paths* vers *dest_dir*. Renvoie les changements effectués."""
        changes: list[FileChange] = []
        inverse: list[FileChange] = []

        for src in paths:
            if not os.path.exists(src):
                continue
            dst = _unique_dest(dest_dir, os.path.basename(src))
            try:
                os.makedirs(dest_dir, exist_ok=True)
                shutil.copy2(src, dst)
            except OSError:
                continue
            changes.append(FileChange(COPIED, src, dst))
            inverse.append(FileChange(REMOVED, dst))  # inverse : supprimer la copie
            self._log("Copier", src, dst)

        if inverse:
            self._undo_stack.append(_Batch("Copie", list(reversed(inverse))))
        return changes

    def rename(self, items: list[tuple[str, str]]) -> list[FileChange]:
        """Renomme des fichiers dans leur dossier d'origine (annulable + journalisé).

        *items* est une liste de couples ``(chemin_source, nouveau_nom_de_base)``.
        Un renommage est un déplacement dans le même dossier ; il réutilise donc
        la même mécanique d'annulation que :meth:`move`.
        """
        changes: list[FileChange] = []
        inverse: list[FileChange] = []
        for src, new_name in items:
            if not os.path.exists(src) or not new_name:
                continue
            dst = _unique_dest(os.path.dirname(src), new_name)
            if os.path.normcase(dst) == os.path.normcase(src):
                continue  # nom inchangé
            try:
                os.rename(src, dst)
            except OSError:
                continue
            changes.append(FileChange(MOVED, src, dst))
            inverse.append(FileChange(MOVED, dst, src))
            self._log("Renommer", src, dst)
        if inverse:
            self._undo_stack.append(_Batch("Renommage", list(reversed(inverse))))
        return changes

    def trash(self, paths: list[str]) -> list[FileChange]:
        """Envoie *paths* à la corbeille Windows (non annulable côté app)."""
        changes: list[FileChange] = []
        for src in paths:
            if not os.path.exists(src):
                continue
            if not self._to_trash(src):
                continue
            changes.append(FileChange(REMOVED, src))
            self._log("Corbeille", src, "")
        return changes

    def undo(self) -> list[FileChange]:
        """Annule le dernier lot de tri. Renvoie les changements pour la galerie."""
        if not self._undo_stack:
            return []
        batch = self._undo_stack.pop()
        changes: list[FileChange] = []

        for ch in batch.inverse:
            if ch.kind == MOVED:
                # ch.src = emplacement actuel, ch.dst = emplacement d'origine à restaurer.
                if not os.path.exists(ch.src):
                    continue
                dst = _unique_dest(os.path.dirname(ch.dst), os.path.basename(ch.dst))
                try:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.move(ch.src, dst)
                except OSError:
                    continue
                changes.append(FileChange(MOVED, ch.src, dst))
                self._log("Annuler (déplacer)", ch.src, dst)
            elif ch.kind == REMOVED:
                # Annulation d'une copie : envoyer la copie à la corbeille.
                if not os.path.exists(ch.src) or not self._to_trash(ch.src):
                    continue
                changes.append(FileChange(REMOVED, ch.src))
                self._log("Annuler (supprimer copie)", ch.src, "")

        return changes

    # --- Édition d'images (journalisée, non annulable via Ctrl+Z) ---
    def log_edit(self, action: str, source: str, destination: str) -> None:
        """Journalise une opération d'édition (rotation/recadrage/conversion)."""
        self._log(action, source, destination)

    # --- Journal ---
    def export_log(self, path: str) -> None:
        """Écrit le journal dans un fichier texte (seule écriture hors tri)."""
        lines = [
            f"{e.timestamp}\t{e.action}\t{e.source}\t{e.destination}"
            for e in self.log
        ]
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("Journal des opérations — PicturIt\n")
            fh.write("horodatage\taction\tsource\tdestination\n")
            fh.write("\n".join(lines))
            fh.write("\n")

    def _log(self, action: str, source: str, destination: str) -> None:
        self.log.append(
            LogEntry(
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                action=action,
                source=source,
                destination=destination,
            )
        )

    def _to_trash(self, path: str) -> bool:
        """Envoie un fichier à la corbeille. Renvoie False en cas d'échec."""
        if send2trash is None:
            return False
        try:
            send2trash(os.path.abspath(path))
            return True
        except Exception:  # noqa: BLE001 — fichier verrouillé, etc. : pas de crash
            return False
