"""Vue Carte — Leaflet (CDN) dans QtWebEngine, pont QWebChannel.

- Affiche un marqueur par média géolocalisé, avec clustering (compteur).
- Sélection bidirectionnelle carte ⇄ galerie (cf. SPEC 4.4) :
  - clic sur marqueur/cluster → émet ``markers_selected`` (chemins) ;
  - ``set_highlight`` met en évidence les marqueurs des photos sélectionnées.
- Bouton « Agrandir / Réduire la carte » (géré par la fenêtre principale).

Le fichier ``resources/map.html`` est chargé avec le script ``qwebchannel.js``
injecté en ligne (plus robuste que de dépendre de l'URL qrc).
"""

from __future__ import annotations

import json
import os
import sys

from PySide6.QtCore import QFile, QIODevice, QObject, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


def _resources_dir() -> str:
    """Dossier des ressources : bundle PyInstaller (_MEIPASS) ou racine projet."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.join(base, "resources")
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources"
    )


_MAP_HTML = os.path.join(_resources_dir(), "map.html")


def _read_qwebchannel_js() -> str:
    """Lit le script qwebchannel.js fourni par Qt (ressource interne)."""
    qfile = QFile(":/qtwebchannel/qwebchannel.js")
    if qfile.open(QIODevice.OpenModeFlag.ReadOnly):
        data = bytes(qfile.readAll().data()).decode("utf-8", "ignore")
        qfile.close()
        if data:
            return f"<script>{data}</script>"
    # Repli : laisse Qt résoudre l'URL qrc (fonctionne dans la plupart des cas).
    return '<script src="qrc:///qtwebchannel/qwebchannel.js"></script>'


class _MapBridge(QObject):
    """Pont QWebChannel entre Python (Qt) et la carte (JavaScript)."""

    # Vers Python : l'utilisateur a sélectionné des marqueurs (liste d'ids/chemins).
    markers_selected = Signal(list)

    # Vers JavaScript : la carte écoute ces signaux.
    pointsChanged = Signal(str)
    highlightChanged = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ready = False
        self._points = "[]"
        self._highlight = "[]"

    @Slot()
    def jsReady(self) -> None:
        """Appelé par le JS quand la carte est initialisée : envoie l'état courant."""
        self._ready = True
        self.pointsChanged.emit(self._points)
        self.highlightChanged.emit(self._highlight)

    @Slot(str)
    def selectMarkers(self, payload: str) -> None:
        """Reçoit du JS la liste des ids de photos cliquées sur la carte."""
        try:
            ids = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return
        if isinstance(ids, list):
            self.markers_selected.emit(ids)

    # API côté Python.
    def set_points(self, points: list[dict]) -> None:
        self._points = json.dumps(points)
        if self._ready:
            self.pointsChanged.emit(self._points)

    def set_highlight(self, ids: list[str]) -> None:
        self._highlight = json.dumps(ids)
        if self._ready:
            self.highlightChanged.emit(self._highlight)


class MapPanel(QWidget):
    """Panneau carte : barre d'outils + vue web Leaflet."""

    # Sélection issue de la carte (liste de chemins).
    markers_selected = Signal(list)
    # Demande d'agrandissement/réduction (géré par la fenêtre principale).
    enlarge_toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._enlarged = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Barre d'outils de la carte.
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(4, 2, 4, 2)
        toolbar.addWidget(QLabel("Carte"))
        toolbar.addStretch(1)
        self._enlarge_button = QPushButton("Agrandir la carte")
        self._enlarge_button.clicked.connect(self._on_enlarge_clicked)
        toolbar.addWidget(self._enlarge_button)
        layout.addLayout(toolbar)

        # Vue web + pont.
        self._view = QWebEngineView()
        self._bridge = _MapBridge(self)
        self._bridge.markers_selected.connect(self.markers_selected)
        self._channel = QWebChannel(self)
        self._channel.registerObject("bridge", self._bridge)
        self._view.page().setWebChannel(self._channel)
        layout.addWidget(self._view, stretch=1)

        self._load_map()

    def _load_map(self) -> None:
        """Charge le template carte avec qwebchannel.js injecté en ligne."""
        try:
            with open(_MAP_HTML, encoding="utf-8") as fh:
                html = fh.read()
        except OSError:
            html = "<html><body>Carte indisponible (map.html introuvable)</body></html>"
        html = html.replace("<!--QWEBCHANNEL_JS-->", _read_qwebchannel_js())
        # baseUrl en https pour autoriser le chargement des ressources CDN.
        self._view.setHtml(html, QUrl("https://localhost/"))

    # --- API ---
    def set_points(self, points: list[dict]) -> None:
        """Définit les marqueurs : liste de dicts {id, lat, lon}."""
        self._bridge.set_points(points)

    def set_highlight(self, ids: list[str]) -> None:
        """Met en évidence les marqueurs des photos sélectionnées."""
        self._bridge.set_highlight(ids)

    def refresh(self) -> None:
        """Force le recalcul de la taille de la carte (après affichage/déplacement)."""
        self._view.page().runJavaScript("if (window.map || true) { map.invalidateSize(); }")

    def _on_enlarge_clicked(self) -> None:
        self._enlarged = not self._enlarged
        self._enlarge_button.setText(
            "Réduire la carte" if self._enlarged else "Agrandir la carte"
        )
        self.enlarge_toggled.emit(self._enlarged)

    def is_enlarged(self) -> bool:
        return self._enlarged

    def collapse(self) -> None:
        """Réduit la carte si elle était agrandie (retour au panneau droit)."""
        if self._enlarged:
            self._on_enlarge_clicked()
