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

import heapq
import io
import itertools
import os
import threading
from dataclasses import dataclass

import piexif
from PIL import Image, ImageOps
from PySide6.QtCore import (
    QBuffer,
    QByteArray,
    QIODevice,
    QObject,
    QPointF,
    QRunnable,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

from core import fftools, imaging, logs, metadata, perf

# Configure Pillow (HEIC + images tronquées) : voir core/imaging.py.
_ = imaging.HEIF_SUPPORTED

# Taille (côté max) des vignettes générées.
THUMB_SIZE = 160

# Arrêt en cours : les workers déjà lancés cessent d'émettre vers des objets
# que Qt s'apprête à détruire. La fenêtre ne peut pas simplement les
# attendre : une lecture sur partage réseau prend des centaines de
# millisecondes, et il y en a plusieurs dizaines en vol.
_shutting_down = threading.Event()


def request_shutdown() -> None:
    """Prévient les workers que l'application se ferme."""
    _shutting_down.set()


def is_shutting_down() -> bool:
    return _shutting_down.is_set()


def _pool_threads() -> int:
    """Nombre de threads de génération des vignettes.

    Le travail est surtout de l'**attente** : lire un fichier sur un partage
    réseau prend des centaines de millisecondes pendant lesquelles le thread
    ne fait rien. Un thread par cœur laisse donc le pool à moitié inoccupé
    (parallélisme effectif mesuré à 7,8 pour 16 threads) ; en doubler le
    nombre masque cette latence.

    Réglable par ``PICTURIT_THREADS`` pour les configurations atypiques
    (disque lent, machine peu puissante).
    """
    brut = os.environ.get("PICTURIT_THREADS", "")
    if brut.isdigit() and int(brut) > 0:
        return min(64, int(brut))
    return max(8, min(32, (os.cpu_count() or 4) * 2))


def load_qimage(
    path: str, max_side: int | None = None, collect_metadata: bool = False
) -> QImage | None:
    """Charge une image via Pillow et la renvoie en ``QImage``.

    Passe par Pillow pour gérer HEIC, l'orientation EXIF et les formats
    exotiques. Renvoie None si le fichier est illisible/corrompu.

    *collect_metadata* : enregistre au passage dimensions, date et GPS dans le
    cache de ``core.metadata``. Le fichier est de toute façon ouvert ici :
    autant éviter à l'appelant de le rouvrir juste pour son EXIF.

    Perf : quand on ne veut qu'une vignette (*max_side* fixé), ``Image.draft``
    laisse le décodeur JPEG décoder à échelle réduite (1/2, 1/4, 1/8…) — gain
    majeur sur les grandes photos. La conversion PIL → QImage est faite en
    direct (octets RGBA bruts), sans aller-retour PNG coûteux.
    """
    try:
        with perf.measure("image: decodage PIL"), Image.open(path) as img:
            if collect_metadata:
                # À lire AVANT toute transformation : ``draft`` change ``size``
                # (il décode à échelle réduite) et ``exif_transpose`` produit une
                # image dont l'EXIF d'orientation a été consommé.
                dimensions = img.size
                try:
                    exif = img.getexif()
                except Exception:  # noqa: BLE001 — EXIF absent ou illisible
                    exif = None
                metadata.store_photo(path, dimensions[0], dimensions[1], exif)

            # Décodage à échelle réduite si on ne produit qu'une vignette
            # (sans effet sur les formats qui ne gèrent pas draft, ex. PNG/HEIC).
            if max_side is not None:
                try:
                    img.draft(None, (max_side, max_side))
                except Exception:  # noqa: BLE001 — draft optionnel
                    pass
            img = ImageOps.exif_transpose(img)
            if max_side is not None:
                img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            img = img.convert("RGBA")
            width, height = img.size
            # Conversion directe vers QImage (octets RGBA), copie pour posséder
            # le tampon avant que les octets Python ne soient libérés.
            qimg = QImage(
                img.tobytes("raw", "RGBA"),
                width,
                height,
                QImage.Format.Format_RGBA8888,
            ).copy()
            return qimg if not qimg.isNull() else None
    except Exception as exc:  # noqa: BLE001 — fichier corrompu/illisible : pas de crash
        # Le repli reste silencieux pour l'utilisateur, mais la cause est
        # journalisée : sans cela, une photo « illisible » est indiagnosticable.
        logs.get_logger("image").debug(
            "lecture impossible de %s : %s: %s",
            path, type(exc).__name__, exc,
        )
        return None


# Octets lus en tête de fichier pour récupérer l'EXIF et sa vignette. Mesuré à
# 13 Ko maximum sur un jeu de photos iPhone réelles ; 64 Ko donne de la marge
# sans jamais approcher le poids du fichier complet (plusieurs Mo).
_EXIF_HEAD_BYTES = 64 * 1024

# Priorités dans le pool de threads : une valeur plus haute passe devant.
# Ce qui est sous les yeux de l'utilisateur prime ; les vidéos ferment la
# marche car chacune coûte deux processus externes (ffprobe puis ffmpeg).
PRIORITY_VISIBLE_PHOTO = 3
PRIORITY_VISIBLE_VIDEO = 2
PRIORITY_PHOTO = 1
PRIORITY_VIDEO = 0


def priority_for(is_video: bool, visible: bool) -> int:
    """Priorité de traitement d'un média selon sa nature et sa visibilité."""
    if visible:
        return PRIORITY_VISIBLE_VIDEO if is_video else PRIORITY_VISIBLE_PHOTO
    return PRIORITY_VIDEO if is_video else PRIORITY_PHOTO

# Rotations correspondant au tag EXIF Orientation. La vignette embarquée ne
# porte pas son propre EXIF : on lui applique l'orientation de l'image
# principale, sans quoi les photos prises en portrait s'afficheraient couchées.
_ORIENTATION_TRANSPOSE = {
    3: Image.Transpose.ROTATE_180,
    6: Image.Transpose.ROTATE_270,
    8: Image.Transpose.ROTATE_90,
}


class _HeaderStrategy:
    """Décide s'il vaut la peine de lire l'en-tête avant le fichier complet.

    Lire les 64 premiers Ko est **un pari** : gagnant si le fichier contient une
    vignette EXIF (on évite de transférer plusieurs Mo), perdant sinon (on aura
    payé une lecture pour rien avant de tout relire).

    Mesuré sur un partage réseau : gain de ~480 ms quand la vignette est là,
    surcoût de ~340 ms quand elle manque. Le pari est donc rentable au-delà
    d'environ 40 % de réussite. Or cela dépend entièrement du dossier : 100 %
    sur des photos d'iPhone, 11 % sur des photos re-compressées.

    D'où cet apprentissage par dossier : on observe les premiers fichiers, puis
    on **fige** la décision. Remis à zéro à chaque changement de dossier.

    Deux écueils, tous deux constatés en mesure avant d'être corrigés :

    - **la décision doit être figée.** Tant qu'on la recalculait à partir du
      ratio cumulé, un dossier hétérogène (des sous-dossiers d'iPhone et
      d'autres de photos re-compressées) le faisait osciller autour du seuil et
      réactivait sans cesse la lecture : 585 lectures d'en-tête observées sur
      589 photos, alors que la stratégie avait « abandonné » dès la 24ᵉ ;
    - **les compteurs ont besoin d'un verrou.** Avec 32 workers, les
      incrémentations concurrentes se perdaient et faussaient la statistique.
      Le coût d'un verrou (de l'ordre de la microseconde) est sans commune
      mesure avec la lecture qu'il arbitre (des centaines de millisecondes).
    """

    _ECHANTILLON = 24      # essais avant de trancher
    _SEUIL_RENTABLE = 0.40  # taux de vignettes au-delà duquel le pari paie

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits = 0
        self._total = 0
        self._decision: bool | None = None  # None = phase d'observation

    def reset(self) -> None:
        with self._lock:
            self._hits = 0
            self._total = 0
            self._decision = None

    def should_try(self) -> bool:
        with self._lock:
            return True if self._decision is None else self._decision

    def record(self, found: bool) -> None:
        with self._lock:
            if self._decision is not None:
                return  # décision déjà prise : ne plus la remettre en cause
            self._total += 1
            if found:
                self._hits += 1
            if self._total < self._ECHANTILLON:
                return
            taux = self._hits / self._total
            self._decision = taux >= self._SEUIL_RENTABLE
            hits, total, decision = self._hits, self._total, self._decision
        logs.get_logger("image").debug(
            "vignettes EXIF : %d/%d (%.0f %%) → lecture d'en-tête %s",
            hits, total, taux * 100,
            "conservée" if decision else "abandonnée",
        )


HEADER_STRATEGY = _HeaderStrategy()


def read_header(path: str) -> bytes | None:
    """Lit les premiers octets du fichier (EXIF + vignette embarquee).

    Une seule lecture reseau sert ensuite a tout : vignette, dimensions,
    date et GPS.
    """
    try:
        with perf.measure("image: lecture en-tete"), open(path, "rb") as fh:
            return fh.read(_EXIF_HEAD_BYTES)
    except OSError:
        return None


def thumbnail_from_header(header: bytes, max_side: int) -> QImage | None:
    """Vignette **embarquée dans l'EXIF**, extraite d'un en-tête déjà lu.

    La plupart des photos d'appareil et de téléphone contiennent une vignette
    JPEG de 160×120 dans leur EXIF. La lire coûte quelques kilo-octets au lieu
    de plusieurs méga-octets : décisif quand les photos sont sur un partage
    réseau, où l'application transférait jusqu'ici le fichier entier pour
    produire une image de 160 pixels.

    Renvoie None si le fichier n'a pas de vignette, ou si elle est trop petite
    pour la taille demandée — l'appelant retombe alors sur la lecture complète.
    """
    try:
        with perf.measure("image: vignette EXIF"):
            exif = piexif.load(header)
            raw = exif.get("thumbnail")
            if not raw:
                return None

            with Image.open(io.BytesIO(raw)) as img:
                img.load()
                # Trop petite : l'agrandir donnerait une vignette floue.
                if max(img.size) < max_side:
                    return None
                orientation = exif.get("0th", {}).get(piexif.ImageIFD.Orientation, 1)
                transpose = _ORIENTATION_TRANSPOSE.get(orientation)
                if transpose is not None:
                    img = img.transpose(transpose)
                img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                img = img.convert("RGBA")
                width, height = img.size
                qimg = QImage(
                    img.tobytes("raw", "RGBA"),
                    width,
                    height,
                    QImage.Format.Format_RGBA8888,
                ).copy()
                return qimg if not qimg.isNull() else None
    except Exception:  # noqa: BLE001 — pas de vignette exploitable : repli normal
        return None


def _has_content(path: str) -> bool:
    """True si le fichier existe et n'est pas vide (0 octet)."""
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


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


@dataclass
class _Queued:
    """Vignette demandée mais pas encore lancée (file interne)."""

    is_video: bool
    size: int
    priority: int


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
        if is_shutting_down():
            return
        meta = None
        if self._is_video:
            qimg = extract_video_frame(self._path, self._size)
        else:
            # Une seule lecture réseau sert à tout : la vignette EXIF ET les
            # métadonnées (dimensions, date, GPS). Auparavant chaque photo était
            # ouverte jusqu'à trois fois, ce qui dominait le temps de chargement
            # sur un partage réseau.
            qimg = None
            header = read_header(self._path) if HEADER_STRATEGY.should_try() else None
            if header:
                qimg = thumbnail_from_header(header, self._size)
                HEADER_STRATEGY.record(qimg is not None)
                meta = metadata.read_from_header(self._path, header)
            if qimg is None:
                # Le décodage ouvre le fichier : on en profite pour récolter
                # les métadonnées plutôt que de le rouvrir ensuite.
                qimg = load_qimage(self._path, self._size, collect_metadata=True)
            if qimg is None and _has_content(self._path):
                # Des fichiers portent une extension photo tout en contenant
                # une vidéo (Live Photos iPhone, conteneurs QuickTime renommés
                # en .JPG). Pillow échoue légitimement ; ffmpeg, lui, sait en
                # extraire une image. Mieux vaut une vignette qu'une tuile
                # « illisible ». Non tenté sur un fichier vide : ffmpeg
                # échouerait de toute façon, et lancer un processus par fichier
                # vide coûte cher sur un dossier réseau.
                qimg = extract_video_frame(self._path, self._size)
                if qimg is not None:
                    logs.get_logger("image").debug(
                        "%s : illisible par Pillow, vignette obtenue via ffmpeg "
                        "(extension trompeuse ?)", self._path,
                    )

        # Métadonnées GPS (badge + marqueur carte), tolérantes aux erreurs.
        lat, lon, has_gps = 0.0, 0.0, False
        try:
            if meta is None:  # en-tête absent ou insuffisant : lecture complète
                meta = metadata.read(self._path)
            has_gps = meta.has_gps
            if has_gps:
                lat, lon = meta.latitude, meta.longitude
        except Exception:  # noqa: BLE001
            pass

        if is_shutting_down():
            return  # l'application ferme : plus personne pour recevoir
        try:
            if qimg is None:
                self.signals.failed.emit(self._path, has_gps, lat, lon)
            else:
                self.signals.finished.emit(self._path, qimg, has_gps, lat, lon)
        except RuntimeError:
            # « Signal source has been deleted » : Qt a détruit le destinataire
            # entre le test ci-dessus et l'émission. Rien à sauver, rien à dire.
            pass


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
        self._pool.setMaxThreadCount(_pool_threads())
        self._cache: dict[str, QPixmap] = {}             # vignette brute (sans overlay)
        self._gps: dict[str, bool] = {}                  # présence GPS connue
        self._coords: dict[str, tuple[float, float]] = {}  # (lat, lon) si GPS
        self._pending: set[str] = set()      # tâches confiées au pool
        self._waiting: dict[str, _Queued] = {}  # demandées, pas encore lancées
        self._heap: list[tuple[int, int, str]] = []
        self._counter = itertools.count()
        # Exactement de quoi occuper le pool, pas davantage : toute tâche
        # confiée à Qt échappe à la repriorisation, donc une file d'avance
        # retarderait d'autant l'effet d'un défilement. Avec ce réglage, un
        # créneau se libère dès qu'une vignette est produite.
        self._max_inflight = max(4, self._pool.maxThreadCount())
        # Vignettes encodées en base64 pour les popups de la carte :
        # (chemin, taille) -> data URL. Coûteux à produire, redemandé à
        # chaque reconstruction du lot de points.
        self._data_urls: dict[tuple[str, int], str] = {}

    def clear(self) -> None:
        """Vide le cache et les tâches en attente (nouveau dossier source)."""
        self._cache.clear()
        self._gps.clear()
        self._coords.clear()
        self._pending.clear()
        self._waiting.clear()
        self._heap.clear()
        self._data_urls.clear()
        HEADER_STRATEGY.reset()
        metadata.clear_cache()

    def thumb_data_url(self, path: str, size: int = 72) -> str:
        """Vignette en cache encodée en data URL base64 (popup de la carte).

        **Mémoïsé** : la galerie reconstruit tout le lot de points à chaque
        nouvelle photo géolocalisée. Sans ce cache, charger 350 médias
        provoquait 1 948 ré-encodages PNG + base64 **sur le thread UI**, soit
        près de 4 s de micro-gels répartis en saccades de 150 à 270 ms.
        """
        cle = (path, size)
        memo = self._data_urls.get(cle)
        if memo is not None:
            return memo
        pixmap = self._cache.get(path)
        if pixmap is None or pixmap.isNull():
            return ""
        with perf.measure("carte: vignette base64"):
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
            url = "data:image/png;base64," + bytes(data.toBase64()).decode("ascii")
            self._data_urls[cle] = url
            return url

    def invalidate(self, path: str) -> None:
        """Oublie la vignette en cache d'un fichier (son contenu a changé)."""
        self._cache.pop(path, None)
        self._gps.pop(path, None)
        self._coords.pop(path, None)
        self._pending.discard(path)
        self._waiting.pop(path, None)
        self._drop_data_urls(path)
        metadata.invalidate(path)

    def _drop_data_urls(self, path: str) -> None:
        """Oublie les data URL d'un chemin (toutes tailles confondues)."""
        for cle in [k for k in self._data_urls if k[0] == path]:
            del self._data_urls[cle]

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

    def request(
        self, path: str, is_video: bool, size: int = THUMB_SIZE,
        priority: int | None = None,
    ) -> None:
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
        if path in self._pending or path in self._waiting:
            return
        if priority is None:
            priority = priority_for(is_video, visible=False)
        self._waiting[path] = _Queued(is_video, size, priority)
        self._enqueue(path, priority)
        self._pump()

    def prioritize(self, paths, priority: int) -> None:
        """Fait passer devant des vignettes **pas encore lancées**.

        Appelé quand l'utilisateur fait défiler la galerie : ce qu'il a sous les
        yeux doit être servi en premier, sans rien abandonner du reste (la carte
        et les statistiques ont besoin de **tous** les médias).

        Les tâches déjà en cours ne sont pas interrompues : ce serait gâcher une
        lecture disque presque terminée.
        """
        remontes = 0
        for path in paths:
            item = self._waiting.get(path)
            if item is not None and priority > item.priority:
                item.priority = priority
                self._enqueue(path, priority)  # l'ancienne entrée sera ignorée
                remontes += 1
        if remontes:
            perf.note(
                "priorite: %d/%d vignette(s) visibles remontees "
                "(file=%d, en cours=%d)",
                remontes, len(paths), len(self._waiting), len(self._pending),
            )
            self._pump()

    def _enqueue(self, path: str, priority: int) -> None:
        """Insère une entrée dans le tas (priorité décroissante, puis ordre d'arrivée)."""
        heapq.heappush(self._heap, (-priority, next(self._counter), path))

    def _pump(self) -> None:
        """Lance des tâches tant que le pool a de la place, les plus urgentes d'abord.

        On ne déverse **pas** toute la file dans QThreadPool : une fois une tâche
        confiée à Qt, sa priorité est figée et le défilement ne pourrait plus la
        faire passer devant. En gardant la file ici, chaque créneau libéré est
        attribué au média le plus utile à cet instant.
        """
        while self._heap and len(self._pending) < self._max_inflight:
            neg_priority, _, path = heapq.heappop(self._heap)
            item = self._waiting.get(path)
            if item is None:
                continue  # déjà lancée par une entrée plus prioritaire
            if -neg_priority != item.priority:
                continue  # entrée périmée : le média a été repriorisé depuis
            del self._waiting[path]
            self._pending.add(path)
            worker = _ThumbnailWorker(path, item.is_video, item.size)
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
        self._pump()
        self.thumbnail_ready.emit(path, pixmap, has_gps)
        if has_gps:
            self._coords[path] = (lat, lon)
            self.geo_point.emit(path, lat, lon)

    def _on_failed(self, path: str, has_gps: bool, lat: float, lon: float) -> None:
        self._gps[path] = has_gps
        self._pending.discard(path)
        self._pump()
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


def pad_to_square(pixmap: QPixmap, size: int) -> QPixmap:
    """Centre la vignette sur un canevas carré de *size* pixels.

    Sans cela, une photo en portrait produit une icône plus étroite qu'une
    photo en paysage : le cadre de sélection épouse l'icône et n'entoure donc
    pas la même surface d'une carte à l'autre. Le canevas carré aligne toutes
    les vignettes sur la même empreinte, quelle que soit leur orientation.

    Le fond reste transparent : c'est la cellule, en dessous, qui porte la
    couleur de sélection.
    """
    if pixmap.isNull():
        return pixmap
    if pixmap.width() == size and pixmap.height() == size:
        return pixmap
    canevas = QPixmap(size, size)
    canevas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canevas)
    painter.drawPixmap(
        (size - pixmap.width()) // 2, (size - pixmap.height()) // 2, pixmap
    )
    painter.end()
    return canevas


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


def _map_pin_path(x: float, y: float, cote: float) -> QPainterPath:
    """Silhouette d'un marqueur de carte inscrite dans un carré.

    Goutte (disque prolongé d'une pointe vers le bas) évidée en son centre,
    comme les marqueurs des applications cartographiques.
    """
    cx = x + cote / 2
    cy = y + cote * 0.36
    rayon = cote * 0.30

    tete = QPainterPath()
    tete.addEllipse(QPointF(cx, cy), rayon, rayon)

    # Pointe : triangle dont la base épouse le bas du disque.
    pointe = QPainterPath()
    pointe.moveTo(cx - rayon * 0.80, cy + rayon * 0.58)
    pointe.lineTo(cx + rayon * 0.80, cy + rayon * 0.58)
    pointe.lineTo(cx, y + cote * 0.97)
    pointe.closeSubpath()

    trou = QPainterPath()
    trou.addEllipse(QPointF(cx, cy), rayon * 0.44, rayon * 0.44)
    return tete.united(pointe).subtracted(trou)


def overlay_nogps(pixmap: QPixmap) -> QPixmap:
    """Superpose un badge « sans GPS » en bas à gauche (cf. SPEC 4.2).

    Un marqueur de carte barré, plutôt qu'un simple rond : le symbole dit de
    lui-même qu'il est question de localisation, d'après ``resources/nogps.png``.

    Dessiné et non chargé depuis un fichier : la vignette va de 48 à 320 px
    selon le zoom, et un tracé suit cette échelle sans se pixelliser. Chaque
    forme est doublée d'un liseré sombre, sans quoi l'ambre se perdrait sur une
    photo claire.
    """
    result = QPixmap(pixmap)
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    cote_image = min(result.width(), result.height())
    cote = cote_image * 0.21  # 30 % plus petit que la première version
    marge = cote_image * 0.05
    x, y = marge, result.height() - marge - cote

    ambre = QColor(235, 145, 30)
    liseré = QColor(20, 20, 20, 190)

    # Marqueur.
    chemin = _map_pin_path(x, y, cote)
    painter.setPen(QPen(liseré, max(1.0, cote * 0.05)))
    painter.setBrush(ambre)
    painter.drawPath(chemin)

    # Barre oblique : tracée en deux passes, la sombre débordant de la claire
    # pour détacher la barre du marqueur qu'elle traverse.
    debut = QPointF(x + cote * 0.12, y + cote * 0.88)
    fin = QPointF(x + cote * 0.88, y + cote * 0.12)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for couleur, epaisseur in ((liseré, cote * 0.19), (ambre, cote * 0.09)):
        stylo = QPen(couleur, epaisseur)
        stylo.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(stylo)
        painter.drawLine(debut, fin)

    painter.end()
    return result
