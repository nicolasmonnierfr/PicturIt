"""Génération des vignettes en arrière-plan + chargement d'images.

- Vignettes photos via Pillow ; vignettes vidéos = **1re frame** extraite par
  ffmpeg (cf. SPEC 6.4). Tout est calculé dans un pool de threads (QThreadPool)
  pour ne jamais figer l'UI.
- Le worker renvoie aussi la **présence de GPS** (pour le badge en galerie).
- Cache RAM (durée de session uniquement, aucune écriture disque).
- Overlays composés à l'affichage : icône « play » (vidéo) et badge « sans GPS ».

Aucune persistance disque (cf. SPEC 2).
"""

from __future__ import annotations

import io

from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QObject,
    QPointF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)
from PIL import Image, ImageOps

from core import fftools, metadata

# Enregistre le décodeur HEIC/HEIF comme opener Pillow (cf. SPEC 6.1).
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except Exception:  # noqa: BLE001 — support HEIC optionnel, ne doit pas planter
    pass

# Taille (côté max) des vignettes générées.
THUMB_SIZE = 160


def load_qimage(path: str, max_side: int | None = None) -> QImage | None:
    """Charge une image via Pillow et la renvoie en ``QImage``.

    Passe par Pillow pour gérer HEIC, l'orientation EXIF et les formats
    exotiques. Renvoie None si le fichier est illisible/corrompu.
    """
    try:
        with Image.open(path) as img:
            img = ImageOps.exif_transpose(img)
            if max_side is not None:
                img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            img = img.convert("RGBA")
            buffer = io.BytesIO()
            img.save(buffer, format="PNG")
            qimg = QImage()
            qimg.loadFromData(buffer.getvalue(), "PNG")
            return qimg if not qimg.isNull() else None
    except Exception:  # noqa: BLE001 — fichier corrompu/illisible : pas de crash
        return None


def extract_video_frame(path: str, max_side: int | None = None) -> QImage | None:
    """Extrait la 1re frame d'une vidéo via ffmpeg. None si impossible."""
    ffmpeg = fftools.ffmpeg_path()
    if ffmpeg is None:
        return None
    result = fftools.run(
        [ffmpeg, "-v", "error", "-i", path, "-frames:v", "1",
         "-f", "image2pipe", "-vcodec", "png", "-"]
    )
    if result is None or result.returncode != 0 or not result.stdout:
        return None
    qimg = QImage()
    if not qimg.loadFromData(result.stdout, "PNG"):
        return None
    if max_side is not None and not qimg.isNull():
        qimg = qimg.scaled(
            max_side,
            max_side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return qimg


class _ThumbnailSignals(QObject):
    """Signaux émis par un worker (un QRunnable ne peut pas émettre seul)."""

    # (chemin, image, a_gps, lat, lon) en cas de succès (lat/lon ignorés si !a_gps).
    finished = Signal(str, QImage, bool, float, float)
    # (chemin, a_gps, lat, lon) en cas d'échec de génération de vignette.
    failed = Signal(str, bool, float, float)


class _ThumbnailWorker(QRunnable):
    """Tâche de génération d'une vignette (photo ou vidéo), hors thread UI."""

    def __init__(self, path: str, is_video: bool, size: int) -> None:
        super().__init__()
        self._path = path
        self._is_video = is_video
        self._size = size
        self.signals = _ThumbnailSignals()

    def run(self) -> None:
        if self._is_video:
            qimg = extract_video_frame(self._path, self._size)
        else:
            qimg = load_qimage(self._path, self._size)

        # Métadonnées GPS (badge + marqueur carte), tolérantes aux erreurs.
        lat, lon, has_gps = 0.0, 0.0, False
        try:
            meta = metadata.read(self._path)
            has_gps = meta.has_gps
            if has_gps:
                lat, lon = meta.latitude, meta.longitude
        except Exception:  # noqa: BLE001
            pass

        if qimg is None:
            self.signals.failed.emit(self._path, has_gps, lat, lon)
        else:
            self.signals.finished.emit(self._path, qimg, has_gps, lat, lon)


class ThumbnailManager(QObject):
    """Coordonne la génération asynchrone des vignettes et le cache RAM."""

    # (chemin, pixmap brut, a_gps) émis sur le thread UI.
    thumbnail_ready = Signal(str, QPixmap, bool)
    # (chemin, a_gps) émis quand la vignette n'a pas pu être générée.
    thumbnail_failed = Signal(str, bool)
    # (chemin, lat, lon) émis pour chaque média géolocalisé (marqueur carte).
    geo_point = Signal(str, float, float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._cache: dict[str, QPixmap] = {}             # vignette brute (sans overlay)
        self._gps: dict[str, bool] = {}                  # présence GPS connue
        self._coords: dict[str, tuple[float, float]] = {}  # (lat, lon) si GPS
        self._pending: set[str] = set()

    def clear(self) -> None:
        """Vide le cache et les tâches en attente (nouveau dossier source)."""
        self._cache.clear()
        self._gps.clear()
        self._coords.clear()
        self._pending.clear()

    def thumb_data_url(self, path: str, size: int = 72) -> str:
        """Renvoie la vignette en cache encodée en data URL base64 (pour la carte)."""
        pixmap = self._cache.get(path)
        if pixmap is None or pixmap.isNull():
            return ""
        small = pixmap.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        data = QByteArray()
        buffer = QBuffer(data)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        small.save(buffer, "PNG")
        buffer.close()
        return "data:image/png;base64," + bytes(data.toBase64()).decode("ascii")

    def invalidate(self, path: str) -> None:
        """Oublie la vignette en cache d'un fichier (son contenu a changé)."""
        self._cache.pop(path, None)
        self._gps.pop(path, None)
        self._coords.pop(path, None)
        self._pending.discard(path)

    def rekey(self, old_path: str, new_path: str) -> None:
        """Réaffecte les données en cache à un nouveau chemin (fichier déplacé)."""
        pixmap = self._cache.pop(old_path, None)
        if pixmap is not None:
            self._cache[new_path] = pixmap
        if old_path in self._gps:
            self._gps[new_path] = self._gps.pop(old_path)
        if old_path in self._coords:
            self._coords[new_path] = self._coords.pop(old_path)

    def duplicate(self, old_path: str, new_path: str) -> None:
        """Associe les mêmes données à un nouveau chemin (fichier copié)."""
        if old_path in self._cache:
            self._cache[new_path] = self._cache[old_path]
        if old_path in self._gps:
            self._gps[new_path] = self._gps[old_path]
        if old_path in self._coords:
            self._coords[new_path] = self._coords[old_path]

    def request(self, path: str, is_video: bool, size: int = THUMB_SIZE) -> None:
        """Demande la vignette d'un média.

        Si elle est déjà en cache, ré-émet immédiatement ``thumbnail_ready``
        (et ``geo_point`` si connu) ; sinon lance la génération en arrière-plan.
        """
        if path in self._cache and path in self._gps:
            self.thumbnail_ready.emit(path, self._cache[path], self._gps[path])
            if path in self._coords:
                lat, lon = self._coords[path]
                self.geo_point.emit(path, lat, lon)
            return
        if path in self._pending:
            return
        self._pending.add(path)
        worker = _ThumbnailWorker(path, is_video, size)
        worker.signals.finished.connect(self._on_finished)
        worker.signals.failed.connect(self._on_failed)
        self._pool.start(worker)

    def _on_finished(
        self, path: str, qimg: QImage, has_gps: bool, lat: float, lon: float
    ) -> None:
        pixmap = QPixmap.fromImage(qimg)
        self._cache[path] = pixmap
        self._gps[path] = has_gps
        self._pending.discard(path)
        self.thumbnail_ready.emit(path, pixmap, has_gps)
        if has_gps:
            self._coords[path] = (lat, lon)
            self.geo_point.emit(path, lat, lon)

    def _on_failed(self, path: str, has_gps: bool, lat: float, lon: float) -> None:
        self._gps[path] = has_gps
        self._pending.discard(path)
        self.thumbnail_failed.emit(path, has_gps)
        if has_gps:
            self._coords[path] = (lat, lon)
            self.geo_point.emit(path, lat, lon)


# --- Pixmaps utilitaires et overlays ---
def make_placeholder_pixmap(text: str, size: int = THUMB_SIZE) -> QPixmap:
    """Crée une vignette générique (fond gris + texte centré)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(60, 60, 60))
    painter = QPainter(pixmap)
    painter.setPen(QColor(200, 200, 200))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)
    painter.end()
    return pixmap


def overlay_play(pixmap: QPixmap) -> QPixmap:
    """Superpose une icône « play » au centre (distingue les vidéos)."""
    result = QPixmap(pixmap)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    w, h = result.width(), result.height()
    radius = min(w, h) * 0.20
    cx, cy = w / 2, h / 2
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 140))
    painter.drawEllipse(QPointF(cx, cy), radius, radius)
    triangle = QPolygonF(
        [
            QPointF(cx - radius * 0.35, cy - radius * 0.55),
            QPointF(cx - radius * 0.35, cy + radius * 0.55),
            QPointF(cx + radius * 0.6, cy),
        ]
    )
    painter.setBrush(QColor(255, 255, 255, 235))
    painter.drawPolygon(triangle)
    painter.end()
    return result


def overlay_border(pixmap: QPixmap, color: QColor, width: int = 6) -> QPixmap:
    """Entoure la vignette d'une bordure colorée (code doublon/similaire)."""
    result = QPixmap(pixmap)
    painter = QPainter(result)
    pen = QPen(color, width)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    painter.setPen(pen)
    half = width // 2
    painter.drawRect(half, half, result.width() - width, result.height() - width)
    painter.end()
    return result


def overlay_nogps(pixmap: QPixmap) -> QPixmap:
    """Superpose un badge « sans GPS » en bas à gauche (cf. SPEC 4.2)."""
    result = QPixmap(pixmap)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    w, h = result.width(), result.height()
    size = min(w, h)
    radius = size * 0.12
    margin = size * 0.06
    cx, cy = margin + radius, h - margin - radius
    painter.setPen(QPen(QColor(20, 20, 20), max(1.0, size * 0.01)))
    painter.setBrush(QColor(235, 145, 30))  # ambre = avertissement « hors carte »
    painter.drawEllipse(QPointF(cx, cy), radius, radius)
    # Barre oblique blanche signifiant « pas de localisation ».
    painter.setPen(QPen(QColor(255, 255, 255), max(2.0, size * 0.022)))
    painter.drawLine(
        QPointF(cx - radius * 0.6, cy + radius * 0.6),
        QPointF(cx + radius * 0.6, cy - radius * 0.6),
    )
    painter.end()
    return result
