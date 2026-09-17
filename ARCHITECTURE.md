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
| `logs.py` | Journal de diagnostic | **débrayé par défaut** (`--log`/`--log-perf`/`PICTURIT_LOG`) → `%LOCALAPPDATA%\PicturIt\picturit.log` rotatif ; seule écriture disque hors tri/édition |
| `imaging.py` | Configuration Pillow commune | HEIC/HEIF + `LOAD_TRUNCATED_IMAGES` (17,5 % des photos d'un dossier réel étaient rejetées à tort) |
| `perf.py` | Mesures de performance | `step` (ponctuel, tracé) / `measure` (répété, **agrégé**) / `report` ; actif seulement en niveau DEBUG |

## 3. Modules `ui/`

| Fichier | Rôle | Signaux émis (vers main_window) |
|---|---|---|
| `main_window.py` | Orchestrateur, raccourcis, barres, contrôleur d'édition | — |
| `nav_panel.py` | Bouton « Inclure les sous-dossiers » + arborescence + accès rapide (épinglage) | `source_changed(path,recursive)`, `target_activated(path,copier)`, `files_dropped`, `recursive_toggled` |
| `gallery_view.py` | Galerie (sections, vignettes, tri/filtre/recherche, doublons) | `media_selected`, `selection_changed`, `geo_points_changed`, `loading_progress/finished`, `status`, `rename_requested`, `rotate_selection_requested`, `summary_changed`, `fullscreen_requested` |
| `preview_panel.py` | Aperçu image zoomable / lecteur vidéo (transport, progression, volume) / métadonnées + barre d'édition | `rotate_requested(bool)`, `crop_committed(box)`, `convert_requested(ext)` |
| `map_panel.py` | Carte Leaflet (QWebEngineView + QWebChannel) | `markers_selected(list)`, `enlarge_toggled(bool)` |
| `fullscreen.py` | Fenêtre plein écran qui **héberge le PreviewPanel reparenté** ; navigation par **←/→ ou flèches ‹ › flottantes** (bords latéraux), fermeture par **Échap ou ✕** | `nav(int)`, `closed` |
| `widgets.py` | `ToggleSlider` (mini-bascule), `make_crop_icon` | — |

---

## 4. Flux de données (à connaître pour déboguer)

### Chargement
`nav_panel` (clic/Entrée dossier) → `source_changed(dir, recursive)` →
`MainWindow._set_source` → `gallery.load_media(dir, recursive)` →
**`_ScanWorker` (QThreadPool)** → `scanner.scan` → `_on_scan_finished`
→ `_media_by_path` (**source de
vérité**) → `_compute_view()` (filtre carte + filtre + recherche + tri avec
ordre asc/desc) → `_display(list)` → `_section_of(media)` donne (libellé, clé)
de section selon le **regroupement** (`dir` = sous-dossier par défaut, ou
`day`/`week`/`month` = date de prise de vue) → une section (`_SectionListView`)
par groupe + `_append_item` → `ThumbnailManager.request`.

**Récursivité = geste explicite.** Le clic dans l'arbre charge le contenu
**direct** du dossier (`recursive=False`), ce qui est instantané même sur `C:\`.
Le parcours complet passe par le bouton « Inclure les sous-dossiers » ou le menu
contextuel de l'arborescence.

**Jeton de scan (`_scan_token`).** Il sert à deux choses à la fois : le worker
compare son jeton au jeton courant pour savoir s'il doit s'arrêter
(`should_cancel`), et `_on_scan_finished` ignore tout résultat dont le jeton est
périmé. Changer de dossier ou cliquer « Arrêter l'analyse » incrémente le jeton,
donc annule le scan en cours **sans** attendre qu'il se termine.

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
  `gallery.filter_to_paths` : **filtre** la galerie sur ces photos (masque les
  autres) + bannière « Supprimer le filtre » (`_path_filter`). Double-clic
  marqueur = centrer/zoomer (map.html). `select_paths` reste utilisé ailleurs
  (plein écran, menu contextuel).
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
- `QThreadPool.globalInstance()` : scan du dossier source (`_ScanWorker`),
  workers de vignettes (`_ThumbnailWorker`) et analyse des doublons
  (`_DuplicatesWorker`). Résultats remontés par **signaux** (donc exécutés sur
  le thread UI).
- ⚠️ **Rien de bloquant sur le thread UI** (SPEC 5.3). Le scan y était
  synchrone jusqu'à la v1.1.1 : un clic sur une racine de disque figeait
  l'application plusieurs minutes. Toute nouvelle opération qui parcourt le
  disque doit passer par un worker.
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
6bis. **Mode non récursif et `_is_within_source`** : en mode « dossier seul », un
    fichier copié/déplacé vers un **sous-dossier** ne doit pas réapparaître dans
    la galerie. `_is_within_source` teste donc le dossier parent exact, et non
    l'appartenance à l'arborescence. Si un fichier trié surgit là où il ne
    devrait pas, regarder cette méthode.
6. **`_media_by_path`** est la source de vérité ; il est maintenu dans
   `_add_path`/`_remove_item`. Le tri/filtre/recherche recalcule la vue via
   `_compute_view()` ; en mode doublons la base est l'ensemble groupé, **filtré
   par catégorie** : deux coches indépendantes (`duplicates_switch` = identiques,
   `similars_switch` = similaires) alimentent `_dup_categories` ; l'analyse
   (`_DuplicatesWorker`) n'est calculée **qu'une fois**, cocher/décocher une
   catégorie ne fait que re-filtrer (`_base_media` via `DupIndex.color_of`).
7. **Édition « Remplacer »** est destructive et **non annulable** (pas de
   sauvegarde de l'original). Le mode « Copier » accumule sur un seul `_copie`.
   Trois garde-fous : le côté « Remplacer » du switch s'affiche en **ambre**,
   `_confirm_replace_mode()` prévient **une fois par session** avant la
   première écriture, et `_confirm_rotate_in_place()` couvre le menu
   contextuel de la galerie — qui écrit toujours en place et **ignore le
   switch**. Cette confirmation-là ne se déclenche que s'il y a vraiment
   ré-encodage : la rotation JPEG est sans perte, avertir à chaque fois
   habituerait l'utilisateur à cliquer « Oui » sans lire.
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

### Chargement des vignettes (chemin le plus sensible aux perfs)

Trois mécanismes, tous motivés par des mesures sur un **partage réseau** (où
lire une photo coûte ~800 ms, contre ~30 ms en local) :

1. **File d'attente maison** (`ThumbnailManager._heap` / `_waiting`). On ne
   déverse pas tout dans `QThreadPool` : une tâche confiée à Qt a sa priorité
   figée et le défilement ne pourrait plus la faire remonter. Le pool ne reçoit
   que `_max_inflight` tâches, donc chaque créneau libéré va au média le plus
   utile *à cet instant*. Ordre : photo visible > vidéo visible > photo > vidéo.
2. **Repriorisation au défilement** : `gallery._visible_paths()` calcule par
   arithmétique (pas widget par widget) ce qui est à l'écran, et
   `ThumbnailManager.prioritize()` remonte ces entrées. Rien n'est abandonné —
   la carte et les statistiques ont besoin de **tous** les médias.
3. **Une seule lecture par photo**. Deux chemins possibles :
   - vignette EXIF embarquée (9 Ko au lieu de plusieurs Mo) quand elle existe ;
   - sinon décodage complet, avec `collect_metadata=True` qui récolte
     dimensions/date/GPS pendant l'ouverture déjà faite.

   ⚠️ `_HeaderStrategy` décide **par dossier** s'il faut tenter l'en-tête :
   le pari rapporte ~480 ms quand la vignette est là, coûte ~340 ms sinon, donc
   il n'est rentable qu'au-delà de ~40 % de réussite (mesuré : 100 % sur des
   photos d'iPhone, 11 % sur des photos re-compressées). Ne pas le rendre
   systématique : c'est une régression que les mesures ont déjà démasquée.

⚠️ `collect_metadata` lit `img.size` et `img.getexif()` **avant** `draft()` et
`exif_transpose()` : le premier change `size` (décodage à échelle réduite), le
second consomme l'EXIF d'orientation.

Nombre de threads : `_pool_threads()` = 2 × cœurs (plafonné à 32), car le
travail est surtout de l'attente réseau. Réglable par `PICTURIT_THREADS`.

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
- **Installeur** : `ISCC.exe installer.iss` → `installer_output/PicturIt-Setup-1.1.0.exe`.

### Tests automatisés (`tests/`)

```powershell
.\.venv\Scripts\python.exe -m pytest                              # quelques secondes
.\.venv\Scripts\python.exe -m pytest --cov=core --cov-report=term-missing
```

Périmètre : **`core/` uniquement** (plus de 80 % de couverture ; au-delà de
90 % hors parties Qt de `thumbnails`). Aucun test ne crée de `QApplication` : le harnais reste rapide
et n'a besoin ni d'écran ni de QtWebEngine. Seul `test_header_strategy.py`
importe PySide6, pour une classe de logique pure qui y réside.

| Fichier | Couvre | Points notables |
|---|---|---|
| `test_geo.py` | `geo` | les **deux** formats de rationnels (piexif + IFDRational), piège §6.3 |
| `test_scanner.py` | `scanner` | extensions, ordre des sections, scan récursif |
| `test_metadata.py` | `metadata` | EXIF, EXIF étendu, **ffprobe simulé**, cache et invalidation (§6.16) |
| `test_duplicates.py` | `duplicates` | identiques/similaires, seuils, priorité, piège §6.4 |
| `test_operations.py` | `operations` | move/copy/rename/trash, pile d'annulation LIFO, journal |
| `test_editing.py` | `editing` | rotation JPEG **vérifiée sans perte** (pixels stockés inchangés) |
| `test_fftools.py` | `fftools` | résolution des binaires, timeout, binaire absent |
| `test_logs.py` | `logs` | **rien n'est écrit** sans demande explicite (SPEC 2), niveaux, idempotence |
| `test_perf.py` | `perf` | inactif par défaut, agrégation, exceptions propagées intactes |
| `test_imaging`* | `imaging` | via `test_image_loading.py` : images tronquées acceptées |
| `test_image_loading.py` | `thumbnails` | en-tête, vignette EXIF + orientation, décodage, récolte des métadonnées |
| `test_thumbnail_queue.py` | `thumbnails` | ordre de service, repriorisation, rien d'abandonné, dimensionnement du pool |
| `test_header_strategy.py` | `thumbnails` | décision figée, coupure sous concurrence |

Deux règles à respecter en ajoutant des tests :
- **Jamais la vraie corbeille** : la fixture `fake_trash` (conftest) remplace
  `send2trash`. Un test qui appelle `operations.trash` sans elle polluerait la
  corbeille de l'utilisateur.
- **Cache de métadonnées** : vidé automatiquement autour de chaque test (fixture
  `_clear_metadata_cache`), car c'est un état global de module.

Le conftest fournit `write_photo()` (JPEG de test daté/géolocalisé via piexif)
et `pad_to()` (égalise la taille de deux fichiers de contenus différents, pour
reproduire le cas « identique par taille + date »).

**Non couvert** : les parties Qt de `core/thumbnails.py` (workers, overlays,
cache de QPixmap) et tout `ui/` — validés manuellement,
ou par scripts offscreen jetables (`QT_QPA_PLATFORM=offscreen` +
`AA_ShareOpenGLContexts` avant `QApplication`).

### Garde-fou interface (`scripts/verifier_ui.py`)

```powershell
.\.venv\Scripts\python.exe scripts/verifier_ui.py
```

Construit réellement `MainWindow` hors écran : rattrape les imports cassés et les
signaux connectés à un slot disparu, que le harnais `core/` ne peut pas voir.

### Lint (`ruff.toml`)

```powershell
.\.venv\Scripts\python.exe -m ruff check .
```

Règles désactivées **volontairement**, à ne pas réactiver sans raison :
`S110`/`S112`/`SIM105` (replis silencieux voulus, §7), `RUF001-003` (typographie
française : « × », guillemets), et `N802`/`N815` sur `ui/map_panel.py` (les noms
camelCase des signaux et slots font partie du contrat avec `map.html`).

### Intégration continue (`.github/workflows/ci.yml`)

Sur chaque push et PR vers `main`, **windows-latest** (seule plateforme
supportée) : job `lint` (ruff seul, sans Qt) puis job `tests` (installation
complète des dépendances → pytest + couverture → garde-fou interface).
