# SPEC.md — Trieur de Photos (application de bureau Windows)

> ℹ️ **Note** : l'application a été renommée **PicturIt** (l'exécutable est
> `PicturIt.exe`). Ce document conserve son intitulé d'origine « Trieur de
> Photos » ; il décrit le périmètre v1 et reste la référence des exigences.
>
> Document de spécification destiné à un agent de développement autonome (Claude Code).
> Lis ce fichier en entier avant d'écrire du code. Respecte le périmètre v1 ; ne rajoute pas de fonctionnalités hors périmètre.

---

## 1. Objectif

Construire une application de bureau **Windows** permettant de **trier des photos et vidéos personnelles** (volume cible : quelques milliers de fichiers). L'application doit ressembler à un explorateur de fichiers simplifié, orienté tri rapide, avec une vue galerie, une vue carte géographique, et une détection de doublons.

**Livrable final** : un exécutable `.exe` autonome (PyInstaller), ne nécessitant aucune installation côté utilisateur final.

---

## 2. Contraintes techniques imposées

| Contrainte | Choix imposé | Raison |
|---|---|---|
| Langage | **Python 3.12** | Compatibilité PySide6 (requiert ≥3.10, <3.15) |
| Framework UI | **PySide6 (Qt 6)** | Vraie application de bureau native |
| Carte | **Leaflet** (JS) dans **QtWebEngine** | Carte interactive riche ; tuiles OpenStreetMap **en ligne** |
| Packaging | **PyInstaller** | `.exe` autonome |
| Persistance | **AUCUNE** | Pas de fichier de config, pas de cache disque, pas de base de données. L'app ne doit rien écrire en dehors des opérations de tri explicites et d'un éventuel log exporté à la demande. |

⚠️ **Zéro persistance** est une exigence ferme. Pas de `QSettings`, pas de fichier `.ini`, pas de cache de vignettes sur disque. Tout est recalculé en mémoire à chaque lancement. Seule exception : l'export manuel du log (section 8).

### Dépendances Python autorisées
- `PySide6` (UI + QtWebEngine + QtMultimedia)
- `Pillow` (lecture images, génération vignettes, EXIF de base)
- `piexif` (lecture/écriture EXIF fine, GPS)
- `send2trash` (suppression vers corbeille Windows)
- `ffmpeg`/`ffprobe` : **exécutable externe**, pas un paquet pip. Voir section 6.4 pour la stratégie de packaging.

---

## 3. Architecture de l'interface

Fenêtre principale divisée en **3 colonnes** + barre supérieure + barre inférieure.

```
┌──────────────────────────────────────────────────────────────────────┐
│ BARRE SUP : [chemin source] [Parcourir]      [switch Aperçu/Carte]    │
├──────────────────┬────────────────────────────┬──────────────────────┤
│ COLONNE GAUCHE   │   COLONNE CENTRE           │   COLONNE DROITE     │
│ (divisée en 2)   │   GALERIE                  │   (panneau bascule)  │
│                  │                            │                      │
│ ┌──────────────┐ │   ── sous-dossier A ────   │   MODE APERÇU :      │
│ │ ARBORESCENCE │ │   ▢  ▢  ▢  ▢              │   - grande image     │
│ │ (QTreeView   │ │   ── sous-dossier B ────   │     OU lecteur vidéo │
│ │  fichiers)   │ │   ▢  ▢  ▢                 │   - métadonnées      │
│ │ clic G=source│ │                            │                      │
│ │ clic D=épingl│ │   [switch: doublons]       │   MODE CARTE :       │
│ ├──────────────┤ │                            │   - carte Leaflet    │
│ │ ACCÈS RAPIDE │ │                            │   - clusters         │
│ │ 1 Vacances   │ │                            │   - sélection        │
│ │ 2 Famille    │ │                            │                      │
│ │ 3 À jeter    │ │                            │                      │
│ └──────────────┘ │                            │                      │
├──────────────────┴────────────────────────────┴──────────────────────┤
│ BARRE INF : raccourcis (1-9 vers dossier, Suppr=corbeille, Ctrl+Z)    │
└──────────────────────────────────────────────────────────────────────┘
```

⚠️ Le panneau droit (carte) est étroit. Prévoir un bouton **« Agrandir la carte »** qui affiche la carte en plein espace central (par-dessus la galerie), avec retour à la vue normale.

---

## 4. Spécifications fonctionnelles détaillées

### 4.1 Colonne gauche — Navigation

**Haut : arborescence de fichiers** (type explorateur Windows)
- Widget `QTreeView` + `QFileSystemModel`.
- **Clic gauche** sur un dossier → le charge comme **dossier source** : la galerie affiche **récursivement** toutes les photos/vidéos de ce dossier et de tous ses sous-dossiers.
- **Clic droit** sur un dossier → menu contextuel avec **« Épingler »** → l'ajoute à l'accès rapide.

**Bas : accès rapide** (dossiers cibles épinglés)
- Liste des dossiers épinglés, chacun affiché avec son **numéro de raccourci (1 à 9)**.
- Maximum **9 dossiers** (limite des raccourcis clavier). Si l'IHM est contrainte, en afficher moins proprement.
- **Clic droit** sur un élément épinglé → **« Désépingler »**.
- Ces dossiers sont les **cibles** des opérations de tri.

### 4.2 Colonne centre — Galerie

- Affichage en **grille de vignettes** (`QListView` en mode IconMode, ou grille custom).
- **Chargement récursif** : toutes les photos/vidéos de l'arborescence du dossier source.
- **Séparateurs par sous-dossier** : un en-tête de section affichant le chemin relatif du sous-dossier avant ses vignettes. (Si `QListView` ne le permet pas nativement, utiliser une vue custom ou des sections.)
- **Chargement paresseux** des vignettes (lazy loading) + **génération en arrière-plan** (QThreadPool / workers) pour ne pas figer l'UI. ⚠️ Important pour quelques milliers de fichiers.
- **Barre de progression** pendant le scan initial.
- **Vignette vidéo** = **première frame** extraite (voir 6.4), avec une **icône « play »** superposée pour distinguer des photos.
- **Indicateur GPS** : photo **sans coordonnées GPS** → petit badge/icône ou liseré de couleur sur la vignette (pour signaler qu'elle n'apparaîtra pas sur la carte).

**Sélection**
- Sélection **simple** (clic), **multiple** (Ctrl+clic), **plage** (Shift+clic).
- Sélection **bidirectionnelle** avec la carte (voir 4.4).

### 4.3 Colonne droite — Panneau bascule (Aperçu / Carte)

Un **switch** (bouton à deux états) en barre supérieure bascule ce panneau entre deux modes.

**Mode Aperçu**
- Affiche en grand la photo actuellement sélectionnée.
- Si c'est une **vidéo** : afficher un **lecteur vidéo** (QtMultimedia : `QMediaPlayer` + `QVideoWidget`) avec contrôles lecture/pause.
- Sous l'image/vidéo : **métadonnées** → nom, chemin, date de prise de vue (EXIF/conteneur), dimensions, taille fichier, coordonnées GPS si présentes.

**Mode Carte** — voir 4.4.

### 4.4 Vue Carte (Leaflet dans QtWebEngine)

- Fond de carte **OpenStreetMap en ligne** (tuiles standard OSM).
- Affiche **un marqueur par photo géolocalisée** (ayant des coordonnées GPS EXIF).
- **Clustering** : les marqueurs proches sont regroupés ; le cluster affiche le **nombre de photos** qu'il contient (utiliser **Leaflet.markercluster**).
- Navigation carte : **zoom/dézoom**, **déplacement** (pan) standard Leaflet.
- **Sélection par clic sur un cluster ou un marqueur** → sélectionne les photos correspondantes **dans la galerie** (colonne centre).
- **Bidirectionnel** : sélectionner des photos dans la galerie → **met en évidence** les marqueurs/clusters correspondants sur la carte.
- Les photos **sans GPS** n'apparaissent **pas** sur la carte (elles restent visibles en galerie avec leur badge).

⚠️ **Pont Python ⇄ JavaScript** : utiliser `QWebChannel` pour la communication bidirectionnelle entre le code Python (Qt) et la carte Leaflet (JS). Côté JS : Leaflet + Leaflet.markercluster chargés depuis CDN (connexion requise) ou embarqués en ressources. Côté Python : injecter la liste des points (lat, lon, id photo) au chargement, recevoir les événements de clic.

### 4.5 Tri (cœur de l'application)

- **Envoyer la sélection vers un dossier cible** :
  - via **clic** sur le dossier dans l'accès rapide,
  - ou via **raccourci clavier 1-9** correspondant au dossier épinglé.
- **Action par défaut = DÉPLACER** (move).
- **Shift maintenu pendant l'action = COPIER** (copy) au lieu de déplacer.
- **Visibilité après déplacement** :
  - Si le dossier cible est un **sous-dossier du dossier source** → la photo **reste affichée** en galerie (car galerie récursive), mais déplacée dans la bonne section.
  - Si le dossier cible est **hors** de l'arborescence source → la photo **disparaît** de la galerie.

### 4.6 Suppression

- Touche **Suppr** sur la sélection → envoie les fichiers vers la **corbeille Windows** (via `send2trash`, jamais de suppression définitive).
- ⚠️ La suppression est **toujours manuelle**. L'application ne supprime **jamais** automatiquement des doublons ou des similaires.

### 4.7 Détection de doublons et similaires

Un **switch dans la galerie** : « Afficher doublons / similaires ».
- **Activé** → masque les fichiers **uniques**, n'affiche que ceux faisant partie d'un groupe de doublons ou de similaires.
- **Code couleur** des vignettes :
  - **Rouge** = doublons **identiques**,
  - **Orange** = **similaires**.
- **Clic sur une vignette d'un groupe** → la galerie se **filtre** pour n'afficher que les membres de **ce** groupe (doublons/similaires de la photo cliquée).

**Définition « identiques »** : fichiers ayant le **même contenu** (hash identique, ex. SHA-256 du contenu binaire) **OU** (même taille de fichier **ET** même datetime de prise de vue).

**Définition « similaires »** : fichiers pris à **intervalle court** (≤ **2 secondes** d'écart sur le datetime de prise de vue) **ET** ayant une **localisation GPS proche**. (Définir un seuil de proximité GPS, ex. < 50 m ; rendre ce seuil constant et clairement nommé dans le code.)

⚠️ **Hors v1, ne pas implémenter** : la **similarité visuelle** (analyse du contenu de l'image type perceptual hash). On se base uniquement sur métadonnées (temps + GPS + taille + hash binaire).

---

## 5. Comportements transverses

### 5.1 Undo
- **Annuler la dernière opération de tri** (déplacement ou copie) : `Ctrl+Z` restaure le(s) fichier(s) à leur emplacement d'origine.
- Undo **à un seul niveau** (dernière action) suffit pour la v1 si un undo multi-niveaux est trop complexe. Privilégier la simplicité et la fiabilité.

### 5.2 Log
- Tenir en mémoire un **journal horodaté** de toutes les opérations (déplacements, copies, suppressions) : timestamp, action, fichier source, destination.
- À la **fermeture de l'application**, proposer (boîte de dialogue) d'**exporter** ce log en fichier texte. Si l'utilisateur refuse, ne rien écrire. ⚠️ C'est la seule écriture disque autorisée hors opérations de tri.

### 5.3 Performances
- Quelques milliers de fichiers : le scan et la génération de vignettes doivent se faire **en arrière-plan** sans figer l'UI.
- Pas de cache disque (contrainte zéro persistance) → vignettes recalculées à chaque ouverture. C'est assumé. Optimiser le calcul en mémoire (cache RAM pour la session en cours uniquement).

---

## 6. Détails d'implémentation et pièges connus

### 6.1 Formats de fichiers à gérer
- **Photos** : `.jpg`, `.jpeg`, `.png`, `.heic`, `.heif`, `.tiff`, `.bmp`, `.webp`, `.gif`.
  - ⚠️ **HEIC/HEIF** : Pillow ne lit pas le HEIC nativement. Ajouter `pillow-heif` et l'enregistrer comme opener Pillow.
- **Vidéos** : `.mp4`, `.mov`, `.avi`, `.mkv`, `.m4v`, `.wmv`.

### 6.2 Lecture EXIF / GPS (photos)
- Date de prise de vue : tag EXIF `DateTimeOriginal`.
- GPS : tags EXIF `GPSLatitude`/`GPSLongitude` (+ refs N/S/E/W). Convertir DMS → degrés décimaux.
- Utiliser `piexif` ou `Pillow.ExifTags`. Gérer proprement l'absence de tags (beaucoup de photos n'ont pas de GPS).

### 6.3 Métadonnées vidéo
- Pas d'EXIF standard. Date et GPS dans les métadonnées conteneur (QuickTime/MP4 : atomes `creation_time`, parfois `location`).
- Extraire via **ffprobe** (`ffprobe -v quiet -print_format json -show_format -show_streams fichier`).
- ⚠️ Beaucoup de vidéos n'ont pas de GPS → traiter comme les photos sans GPS.

### 6.4 Extraction de la première frame vidéo (vignette)
- Utiliser **ffmpeg** : `ffmpeg -i fichier -vframes 1 -f image2 sortie.jpg` (ou via pipe pour rester en mémoire).
- ⚠️ **Stratégie de packaging ffmpeg/ffprobe** : ce sont des binaires externes. Pour un `.exe` vraiment autonome, **embarquer `ffmpeg.exe` et `ffprobe.exe`** dans le bundle PyInstaller (via `--add-binary`) et les appeler par chemin relatif au bundle (`sys._MEIPASS`). Documenter clairement où placer ces binaires dans le repo (`./bin/ffmpeg.exe`, `./bin/ffprobe.exe`).
- Si la frame ne peut pas être extraite (codec absent), afficher une vignette générique « vidéo » plutôt que de planter.

### 6.5 Lecture vidéo dans l'aperçu
- `QtMultimedia` (`QMediaPlayer` + `QVideoWidget`).
- ⚠️ La lecture dépend des **codecs présents sur la machine**. Gérer le cas d'échec sans planter (message « lecture impossible, codec manquant »).

### 6.6 Carte — chargement Leaflet
- HTML/JS de la carte stocké comme **ressource** (string ou fichier embarqué via Qt resources). Chargé dans `QWebEngineView`.
- Leaflet + Leaflet.markercluster : depuis CDN (nécessite connexion, cohérent avec « carte en ligne ») ou embarqués. ⚠️ Si embarqués, attention au packaging des fichiers JS/CSS dans PyInstaller.
- Communication via `QWebChannel`.

### 6.7 Suppression corbeille
- `send2trash` (multiplateforme, gère la corbeille Windows). Ne **jamais** utiliser `os.remove` pour les suppressions utilisateur.

---

## 7. Architecture du code (suggestion)

Organiser en modules clairs, pas un seul fichier monolithique :

```
TrieurPhotos/
├── main.py                  # point d'entrée, lance l'app Qt
├── ui/
│   ├── main_window.py       # fenêtre principale, layout 3 colonnes
│   ├── gallery_view.py      # galerie de vignettes + sélection + sections
│   ├── nav_panel.py         # arborescence + accès rapide (épinglage)
│   ├── preview_panel.py     # panneau droit : aperçu image/vidéo + métadonnées
│   └── map_panel.py         # vue carte (QWebEngineView + QWebChannel)
├── core/
│   ├── scanner.py           # scan récursif, détection formats
│   ├── metadata.py          # EXIF photos + ffprobe vidéos (date, GPS, dimensions)
│   ├── thumbnails.py        # génération vignettes (images + 1re frame vidéo), workers
│   ├── duplicates.py        # détection identiques + similaires
│   ├── operations.py        # move/copy/trash + undo + log
│   └── geo.py               # conversion GPS DMS→décimal, clustering helpers
├── resources/
│   └── map.html             # template Leaflet + JS du pont QWebChannel
├── bin/
│   ├── ffmpeg.exe           # binaire embarqué (fourni par l'utilisateur)
│   └── ffprobe.exe          # binaire embarqué
├── requirements.txt
├── build.spec               # config PyInstaller
└── SPEC.md                  # ce fichier
```

---

## 8. Packaging final (PyInstaller)

- Cible : **un `.exe`**. Tester **deux modes** :
  - `--onefile` (un seul exe, lancement plus lent ~3-8 s à cause de la décompression),
  - `--onedir` (dossier, lancement quasi instantané).
- Inclure : QtWebEngine (lourd, ~Chromium), binaires ffmpeg/ffprobe, ressources (map.html, icônes).
- ⚠️ QtWebEngine + PyInstaller a des pièges connus : bien inclure les ressources Qt WebEngine (`QtWebEngineProcess.exe`, `resources/`, `translations/`). Utiliser les hooks PyInstaller à jour pour PySide6, ou ajouter les data files manuellement dans le `.spec`.
- Fournir un `build.spec` commenté et une commande de build documentée dans un `README.md`.

---

## 9. Périmètre — récapitulatif des décisions

| Sujet | Décision v1 |
|---|---|
| Action de tri par défaut | Déplacer ; **Shift = copier** |
| Médias | Photos **+ vidéos** |
| Similarité visuelle (analyse image) | ❌ **Hors v1** |
| Carte | OpenStreetMap **en ligne** |
| Tags | ❌ Abandonné |
| Persistance / config / cache disque | ❌ **Aucune** (sauf export log manuel) |
| Suppression | Corbeille Windows uniquement, manuelle |
| Undo | Dernière action (multi-niveaux optionnel) |
| Dossiers cibles | Max 9, épinglés, raccourcis 1-9, non mémorisés |
| Détection auto de doublons | Affichage seul, **jamais** de suppression auto |

---

## 10. Critères d'acceptation (definition of done)

1. L'app se lance sur Windows et affiche les 3 colonnes.
2. Clic gauche sur un dossier → galerie peuplée récursivement avec séparateurs par sous-dossier.
3. Vignettes photos ET vidéos (1re frame + icône play) s'affichent sans figer l'UI, avec barre de progression.
4. Épinglage/désépinglage de dossiers cibles fonctionne ; raccourcis 1-9 opérationnels.
5. Déplacer (défaut) et copier (Shift) vers un dossier cible fonctionnent ; visibilité post-tri conforme à 4.5.
6. Suppr → corbeille (vérifiable dans la corbeille Windows).
7. Switch Aperçu/Carte fonctionne ; aperçu image + lecteur vidéo + métadonnées OK.
8. Carte : marqueurs géolocalisés, clustering avec compteur, sélection bidirectionnelle carte⇄galerie.
9. Photos sans GPS : badge en galerie, absentes de la carte.
10. Switch doublons/similaires : code couleur rouge/orange, filtrage par groupe au clic.
11. Ctrl+Z annule la dernière opération de tri.
12. À la fermeture, proposition d'export du log ; aucune autre écriture disque hors opérations de tri.
13. Build PyInstaller produit un `.exe` lançable, carte fonctionnelle incluse.

---

## 11. Instructions de travail pour l'agent

- **Développe par incréments testables.** Ordre suggéré : (1) squelette UI 3 colonnes, (2) scan + galerie + vignettes photos, (3) navigation/épinglage, (4) tri move/copy/trash + undo + log, (5) métadonnées + vidéos, (6) carte, (7) doublons, (8) packaging.
- Après chaque incrément, **vérifie que l'app se lance toujours**.
- **Ne pas** introduire de persistance disque hors log.
- **Ne pas** ajouter de fonctionnalités hors périmètre (section 9).
- Commenter le code en français.
- Gérer les erreurs sans planter (fichiers corrompus, codecs manquants, EXIF absents).
- Fournir `requirements.txt`, `build.spec`, et un `README.md` avec : prérequis (Python 3.12), création venv, installation, où placer ffmpeg/ffprobe, commande de build.
