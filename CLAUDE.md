# CLAUDE.md — PicturIt

Trieur de photos/vidéos de bureau **Windows**. **Python 3.12 + PySide6 (Qt6)**.
Carte **Leaflet dans QtWebEngine**. Vignettes/métadonnées vidéo via **ffmpeg/
ffprobe** (dans `./bin/`). **Zéro persistance disque** (tout en RAM ; seules
écritures : opérations de tri/édition explicites + export manuel du log).

## Documentation (lire selon le besoin)
- `SPEC.md` — exigences v1 (périmètre figé ; ne pas en sortir sans accord).
- `ARCHITECTURE.md` — **carte du code, flux de données et pièges connus** (à lire
  avant tout bugfix non trivial).
- `README.md` — installation, lancement, build, installeur.
- `Backlog.txt` — état des fonctionnalités (À FAIRE / REPOUSSÉ / LIVRÉ).

## Structure
- `core/` = logique métier **sans UI** (scanner, metadata, thumbnails, duplicates,
  operations, editing, geo, fftools).
- `ui/` = widgets PySide6 ; `ui/main_window.py` **orchestre et câble les signaux**
  (les panneaux ne se connaissent pas entre eux).

## Lancer / valider
- Dev : `.\.venv\Scripts\python.exe main.py`
- Test headless : script avec `QT_QPA_PLATFORM=offscreen`, et **poser
  `QCoreApplication.setAttribute(AA_ShareOpenGLContexts)` AVANT `QApplication`**
  (sinon QtWebEngine casse).
- Build : `pyinstaller build.spec --noconfirm --clean` → `dist/PicturIt/`.
- Installeur : `ISCC.exe installer.iss` → `installer_output/PicturIt-Setup-1.1.0.exe`.
- Tests : `.\.venv\Scripts\python.exe -m pytest` (dossier `tests/`, ~190 tests sur
  `core/`, sans Qt). Couverture : `-m pytest --cov=core --cov-report=term-missing`.
  ⚠️ `ui/` et `core/thumbnails.py` ne sont **pas** couverts (validation manuelle).

## Conventions
- Commentaires et libellés UI **en français**.
- **Zéro persistance** : ne jamais écrire sur disque hors tri/édition explicites
  et export du log. Pas de QSettings ni cache disque.
- Tolérance aux erreurs **sans planter** (fichier corrompu, EXIF absent, ffmpeg/
  codec manquant) → repli propre.
- Suppression **toujours** via corbeille (`send2trash`), jamais `os.remove`.
- Garder `README.md`, `Backlog.txt` et `ARCHITECTURE.md` à jour avec le code.

## Pièges majeurs (détail dans ARCHITECTURE.md §6)
- Carte blanche = `invalidateSize` manquant (conteneur masqué à l'init).
- GPS : `geo.dms_to_decimal` gère tuples piexif **et** IFDRational Pillow.
- Vignettes : cache RAM par chemin → `rekey`/`duplicate`/`invalidate`/`clear`.
- Édition « Remplacer » = destructive, **non annulable** (≠ move/copy/rename).
- `build.spec` retire des modules Qt ; en réintroduire un si une nouvelle
  dépendance le nécessite.
