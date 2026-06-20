# ARCHITECTURE.md — PicturIt (carte du code pour le débogage)

Doc destinée à une session de **bugfix**. Complète `README.md` (build/run),
`SPEC.md` (exigences v1) et `Backlog.txt` (état des fonctionnalités).

PicturIt = trieur de photos/vidéos de bureau Windows. **Python 3.12 + PySide6
(Qt6)**. Carte = **Leaflet dans QtWebEngine**. **Zéro persistance disque** : tout
en RAM, sauf les opérations de tri/édition explicites et l'export manuel du log.

---

## 1. Lancement & couches

`main.py` → pose `AA_ShareOpenGLContexts` (requis QtWebEngine) **avant**
`QApplication`, fixe le nom/icône, ouvre `MainWindow` en **plein écran
(showMaximized)**.

- `core/` = logique métier **sans Qt UI** (scanner, métadonnées, vignettes,
  doublons, opérations fichiers, édition, géo, résolution ffmpeg).
- `ui/` = widgets PySide6. `main_window.py` est l'**orchestrateur** : il crée
  tous les panneaux et **câble leurs signaux** (aucun panneau n'en connaît un
  autre directement).

---

## 2. Modules `core/`

| Fichier | Rôle | Points clés |
|---|---|---|
| `scanner.py` | Scan récursif, formats | `MediaFile(path, section, is_video, size)` ; `scan()`→`[(section, [MediaFile])]` ; `is_video()`, `section_key()` |
| `metadata.py` | EXIF photos + ffprobe vidéos | `Metadata` (date, lat/lon, dims, durée, EXIF étendu) ; lit GPS via Pillow `getexif().get_ifd()` ; **cache mémoire** (clé chemin+mtime+taille) → `read()` mémoïsé, `invalidate`/`clear_cache` |
| `geo.py` | GPS | `dms_to_decimal` (gère tuples piexif **et** IFDRational Pillow) ; `parse_iso6709` ; `haversine_m` |
| `fftools.py` | Localise ffmpeg/ffprobe | ordre : `_MEIPASS/bin` → `./bin` → PATH ; `run()` sans fenêtre console |
| `thumbnails.py` | Vignettes + cache RAM | `ThumbnailManager` (QThreadPool) ; overlays (play, no-GPS, bordure doublon) ; `thumb_data_url` (base64 carte) |
| `duplicates.py` | Doublons/similaires | `analyze()` → `DupIndex` ; union-find ; seuils `SIMILAR_TIME_SECONDS=2`, `SIMILAR_GPS_METERS=50` |
| `operations.py` | move/copy/trash + undo + log | `OperationManager` ; renvoie `[FileChange(kind, src, dst)]` ; `rename`, `log_edit`, `export_log` |
| `editing.py` | Rotation/recadrage/conversion | rotation JPEG **lossless** (tag EXIF Orientation) ; crop/convert via Pillow ; **écrit sur disque** |

## 3. Modules `ui/`

| Fichier | Rôle | Signaux émis (vers main_window) |
|---|---|---|
| `main_window.py` | Orchestrateur, raccourcis, barres, contrôleur d'édition | — |
| `nav_panel.py` | Arborescence + accès rapide (épinglage) | `source_changed`, `target_activated(path,copier)`, `files_dropped`, |
| `gallery_view.py` | Galerie (sections, vignettes, tri/filtre/recherche, doublons) | `media_selected`, `selection_changed`, `geo_points_changed`, `loading_progress/finished`, `status`, `rename_requested`, `rotate_selection_requested`, `summary_changed`, `fullscreen_requested` |
| `preview_panel.py` | Aperçu image zoomable / lecteur vidéo / métadonnées + barre d'édition | `rotate_requested(bool)`, `crop_committed(box)`, `convert_requested(ext)` |
| `map_panel.py` | Carte Leaflet (QWebEngineView + QWebChannel) | `markers_selected(list)`, `enlarge_toggled(bool)` |
| `fullscreen.py` | Fenêtre plein écran qui **héberge le PreviewPanel reparenté** | `nav(int)`, `closed` |
| `widgets.py` | `ToggleSlider` (mini-bascule), `make_crop_icon` | — |

---

## 4. Flux de données (à connaître pour déboguer)

### Chargement
`nav_panel` (clic/Entrée dossier) → `source_changed` → `MainWindow._set_source`
→ `gallery.load_media(dir)` → `scanner.scan` → `_media_by_path` (**source de
vérité**) → `_compute_view()` (filtre+recherche+tri) → `_display(list)` → crée
une section (`_SectionListView`) par sous-dossier + `_append_item` →
`ThumbnailManager.request`.

### Vignettes (asynchrone)
`request` → worker QThreadPool → `thumbnail_ready(path, pixmap, has_gps)` /
`thumbnail_failed` / `geo_point(path, lat, lon)`. La galerie compose l'icône
(`_decorate` : play vidéo + badge no-GPS + bordure doublon) et fait avancer la
barre de progression. **Cache RAM uniquement** ; `rekey`/`duplicate` (move/copy),
`invalidate` (édition), `clear` (changement de dossier ou de taille).

### Carte ⇄ galerie (bidirectionnel)
- Points : `_geo[path]=(lat,lon)` → `_flush_geo` (débattu 200 ms) →
  `geo_points_changed([{id,lat,lon,name,thumb}])` → `map_panel.set_points` →
  `_MapBridge.pointsChanged` (QWebChannel) → JS `rebuild` (markercluster).
- Clic marqueur/cluster (JS) → `bridge.selectMarkers` → `markers_selected` →
  `gallery.select_paths`.
- Sélection galerie → `selection_changed` → `map_panel.set_highlight`.
- **Agrandir** : le même `MapPanel` est **reparenté** entre
  `preview_panel.map_container` (colonne droite) et `center_map_host` (centre).

### Tri (opérations)
`target_activated` / `files_dropped` → `MainWindow._sort_to` →
`operations.move|copy` → `[FileChange]` → `gallery.apply_changes` (ajout/retrait
**incrémental**, conforme SPEC 4.5). Suppr → `operations.trash` ; Ctrl+Z →
`operations.undo`. Log exporté à la fermeture (`closeEvent`).

### Édition (écrit sur disque)
`preview` (rotate/crop/convert) → `MainWindow._edit_target(src)` choisit la cible
selon le mini-slider **Remplacer/Copier** (en copie : fichier `_copie` cumulatif)
→ `editing.*` → `operations.log_edit` + `gallery.invalidate_thumbnail` +
`preview.show_media`. **Non annulable par Ctrl+Z** (seuls move/copy/rename le sont).

### Plein écran
F / double-clic → `MainWindow` **reparente `preview_panel`** dans
`FullScreenViewer` ; `nav(±1)` → `gallery.select_relative` (met à jour l'aperçu
via `media_selected`). Échap → reparente l'aperçu dans le splitter (colonne 3).

---

## 5. Threads
- `QThreadPool.globalInstance()` : workers de vignettes (`_ThumbnailWorker`) et
  analyse des doublons (`_DuplicatesWorker`). Résultats remontés par **signaux**
  (donc exécutés sur le thread UI).
- `closeEvent` fait `QThreadPool.clear()` + `waitForDone(2000)` pour éviter les
  erreurs « Signal source has been deleted » à l'arrêt.

---

## 6. Pièges connus / points sensibles (débogage)

1. **Carte blanche** si son conteneur était masqué à l'init (Leaflet calcule une
   taille 0). Corrigé par un `ResizeObserver` (map.html) + `map_panel.refresh()`
   (`invalidateSize`) appelé à l'affichage/agrandissement. Si la carte ne
   s'affiche pas, regarder là.
2. **QWebChannel** : `qwebchannel.js` est **injecté en ligne** dans `map.html`
   (lu via `QFile(":/qtwebchannel/qwebchannel.js")`), `setHtml` avec baseUrl
   `https://localhost/` (pour autoriser le CDN Leaflet). La carte exige une
   **connexion Internet** (tuiles OSM + Leaflet CDN).
3. **GPS** : `geo.dms_to_decimal` doit gérer **deux formats** (tuples piexif et
   `IFDRational` Pillow) — bug fréquent si on n'en gère qu'un.
4. **Doublons « identiques »** = hash **OU** (même taille ET même datetime). Des
   fichiers distincts de même taille + même datetime sont donc marqués identiques
   (conforme SPEC, mais surprenant sur des jeux de test artificiels).
5. **Vignette = clé par chemin** : après move/copy → `rekey`/`duplicate` ; après
   édition → `invalidate` ; changement de taille → `clear` + ré-affichage.
6. **`_media_by_path`** est la source de vérité ; il est maintenu dans
   `_add_path`/`_remove_item`. Le tri/filtre/recherche recalcule la vue via
   `_compute_view()` ; en mode doublons la base est l'ensemble groupé.
7. **Édition « Remplacer »** est destructive et **non annulable** (pas de
   sauvegarde de l'original). Le mode « Copier » accumule sur un seul `_copie`.
8. **ffmpeg/ffprobe** : si absents, vignette vidéo générique + métadonnées vidéo
   vides, **sans planter** (`fftools` renvoie None). Présents dans `./bin/`.
9. **Packaging** : `build.spec` **retire** DevTools Chromium, traductions Qt,
   locales WebEngine (sauf en-US/fr) et des modules Qt (PDF/3D/Charts/Quick3D…).
   Si un nouveau code dépend d'un module retiré, l'enlever de `_EXCLUDE_BIN_PREFIX`.
10. **Mono-exe (onefile) très lent au démarrage** (~60 s, ré-extraction de
    ~640 Mo). Utiliser le **mode dossier** / l'**installeur** (≈ 2 s).
11. **Perf carte** : les vignettes des popups sont injectées en **base64** dans
    le payload des points ; lourd si des **milliers** de photos sont géolocalisées
    (optimisable : charger la vignette à l'ouverture du popup).
12. **Sélection multi-sections** : chaque `_SectionListView` a son propre
    `selectionModel` ; la galerie coordonne (vide les autres au clic simple).
13. **AZERTY** : les raccourcis chiffres 1-9 sont ambigus ; le **Shift+clic
    souris** sur la cible reste la voie fiable pour copier.
14. Test : `MainWindowHandle` (.NET) n'est **pas fiable** sur `python.exe` — ne
    pas s'y fier pour mesurer le temps de démarrage en dev.
15. **Perf vignettes** : `thumbnails.load_qimage` appelle `Image.draft` (décodage
    JPEG à échelle réduite) et convertit PIL→QImage en direct (octets RGBA, pas
    de PNG). Ne pas réintroduire de round-trip PNG. Le `draft` est sans effet sur
    PNG/HEIC (try/except silencieux).
16. **Cache métadonnées** (`metadata._cache`, sous `threading.Lock`) : le calcul
    lourd (PIL/ffprobe) est fait **hors verrou** pour préserver le parallélisme
    des workers. Invalidé automatiquement si mtime/taille changent ; explicitement
    via `metadata.invalidate(path)` (appelé par `ThumbnailManager.invalidate`
    après édition) et `clear_cache()` (par `ThumbnailManager.clear`, nouveau
    dossier/taille). Si une édition disque n'est pas reflétée, regarder là.

---

## 7. Conventions
- Commentaires/identifiants UI **en français**.
- **Zéro persistance** : ne jamais écrire sur disque hors opérations de tri/
  édition explicites et export du log. Pas de QSettings/cache disque.
- Erreurs tolérées **sans planter** (fichier corrompu, EXIF absent, codec/ffmpeg
  manquant) : try/except qui retombe sur un repli.
- Suppression **toujours** via corbeille (`send2trash`), jamais `os.remove`.

---

## 8. Lancer / valider (voir README pour le détail)
- **Dev (rapide)** : `.\.venv\Scripts\python.exe main.py`
- **Tests headless** : exécuter un script avec `QT_QPA_PLATFORM=offscreen` ; pour
  la carte/WebEngine, poser `AA_ShareOpenGLContexts` avant `QApplication`.
- **Build** : `pyinstaller build.spec --noconfirm --clean` → `dist/PicturIt/`.
- **Installeur** : `ISCC.exe installer.iss` → `installer_output/PicturIt-Setup-1.0.1.exe`.

> ⚠️ Aucun harnais de tests automatisés n'est versionné : la validation s'est
> faite par scripts offscreen jetables + captures. Un dossier `tests/` serait un
> bon ajout pour la session de bugfix.
