# PicturIt

Application de bureau Windows pour trier rapidement photos et vidéos
personnelles (galerie, carte géographique, détection de doublons).

## Documentation du projet
- [SPEC.md](SPEC.md) — spécification v1 (périmètre).
- [ARCHITECTURE.md](ARCHITECTURE.md) — carte du code, flux de données, **pièges
  connus** (à lire avant un bugfix).
- [CLAUDE.md](CLAUDE.md) — orientation rapide + conventions.
- [Backlog.txt](Backlog.txt) — état des fonctionnalités (à faire / livré).

## Prérequis

- **Python 3.12** (PySide6 requiert >=3.10, <3.15).
- Windows 10/11.
- **ffmpeg.exe** et **ffprobe.exe** (binaires externes) à placer dans `./bin/`
  pour les vignettes vidéo et les métadonnées vidéo (incrément 5+).

## Installation (développement)

```powershell
# Créer l'environnement virtuel
python -m venv .venv

# L'activer
.\.venv\Scripts\Activate.ps1

# Installer les dépendances
pip install -r requirements.txt

# Optionnel : outillage de développement (tests)
pip install -r requirements-dev.txt
```

## Lancement

```powershell
.\.venv\Scripts\python.exe main.py
```

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Les tests couvrent `core/` (logique métier) à plus de 90 %, en quelques secondes.
Aucun test ne crée de fenêtre Qt : ni écran ni QtWebEngine ne sont nécessaires,
et ffmpeg/ffprobe sont simulés — la suite tourne donc sur une machine nue.

Couverture détaillée :

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=core --cov-report=term-missing
```

L'interface (`ui/`) et `core/thumbnails.py` restent validés manuellement ; voir
[ARCHITECTURE.md](ARCHITECTURE.md#tests-automatisés-tests) pour le détail du
harnais et les règles à suivre en ajoutant des tests.

Un garde-fou complémentaire construit la fenêtre complète hors écran, ce que le
harnais `core/` ne peut pas couvrir :

```powershell
.\.venv\Scripts\python.exe scripts/verifier_ui.py
```

## Diagnostic (journal et mesures de performance)

L'application **n'écrit rien** par défaut. Pour analyser une lenteur ou une
erreur, on active le journal explicitement :

```powershell
.\.venv\Scripts\python.exe main.py --log        # journal d'activité
.\.venv\Scripts\python.exe main.py --log-perf   # + mesures de performance détaillées
.\.venv\Scripts\python.exe main.py --log-perf --log-console   # aussi à l'écran
```

Le journal est écrit dans `%LOCALAPPDATA%\PicturIt\picturit.log` (rotatif,
3 fichiers de 2 Mo). Équivalent par variable d'environnement, utile pour l'exe
installé : `$env:PICTURIT_LOG="perf"`.

`--log-perf` produit, à la fin de chaque chargement de dossier, un bilan trié
par temps cumulé — c'est ce qui permet de savoir **où** part le temps :

```
operation                               appels   total ms   moyenne    max ms
image: decodage PIL                        600      19039     31.73     160.5
metadonnees: EXIF (photo)                  600       6070     10.12      46.8
affichage complet (600 media)                1        186    186.47     186.5
```

## Qualité du code

```powershell
.\.venv\Scripts\python.exe -m ruff check .          # vérifier
.\.venv\Scripts\python.exe -m ruff check . --fix    # corriger l'automatisable
```

La configuration est dans [ruff.toml](ruff.toml). Les règles qui contredisent
les conventions du projet y sont désactivées avec leur justification (replis
silencieux volontaires, typographie française, signaux camelCase imposés par
QWebChannel).

## Intégration continue

Chaque push et chaque pull request sur `main` déclenchent
[la CI GitHub Actions](.github/workflows/ci.yml), sur **windows-latest** — la
seule plateforme supportée :

1. **Lint** — `ruff check`
2. **Tests** — `pytest` avec couverture, puis construction de l'interface hors
   écran via `scripts/verifier_ui.py`

## Binaires ffmpeg / ffprobe

Télécharger une build Windows de ffmpeg (ex. gyan.dev / BtbN) et copier :

```
./bin/ffmpeg.exe
./bin/ffprobe.exe
```

Ils sont embarqués dans le `.exe` final via PyInstaller (voir `build.spec`).

## Construction de l'exécutable (.exe)

Prérequis : `ffmpeg.exe`/`ffprobe.exe` présents dans `./bin/`, puis :

```powershell
pip install pyinstaller
pyinstaller build.spec --noconfirm --clean
```

- **Mode dossier (par défaut, RECOMMANDÉ)** : produit `dist/PicturIt/` ;
  lancer `dist/PicturIt/PicturIt.exe`.
- **Mode fichier unique** : `PICTURIT_ONEFILE=1` avant la commande → un seul
  `dist/PicturIt.exe`.

Comparatif mesuré (Windows 11, bundle allégé) :

| Mode | Taille disque | Démarrage |
|---|---|---|
| Dossier (recommandé) | ~644 Mo | ~2 s |
| Fichier unique | ~246 Mo | 60 s+ (ré-extraction de tout le bundle à chaque lancement) |

→ Le **mode dossier** est conseillé : le mono-exe ré-extrait ~640 Mo (QtWebEngine)
dans un dossier temporaire à chaque lancement, d'où un démarrage très lent.

Allègement : le `build.spec` retire les ressources de debug/DevTools Chromium,
les traductions Qt et les locales WebEngine inutiles, et les modules Qt non
utilisés (~170 Mo économisés). Le bundle inclut QtWebEngine (carte Leaflet),
ffmpeg/ffprobe et `map.html`. La carte nécessite une connexion Internet
(tuiles OpenStreetMap + Leaflet CDN).

## Installeur Windows (setup.exe)

Pour distribuer l'application, on emballe le build mode dossier dans un
installeur avec **Inno Setup** (`installer.iss`) :

1. Produire `dist/PicturIt/` (commande de build ci-dessus).
2. Installer **Inno Setup 6** (https://jrsoftware.org/isdl.php).
3. Compiler :

```powershell
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer.iss
```

→ produit `installer_output/PicturIt-Setup-1.2.0.exe` : installation dans
Program Files (ou par utilisateur, au choix), raccourcis menu Démarrer / Bureau,
et désinstalleur. Aucune dépendance à installer côté utilisateur final.

## État d'avancement

La v1 est complète (incréments 1 à 8 ci-dessous) ; les évolutions ultérieures
sont listées dans [Backlog.txt](Backlog.txt) et l'« Historique des versions ».

- [x] **Incrément 1** — Squelette UI 3 colonnes.
- [x] **Incrément 2** — Scan récursif + galerie (sections par sous-dossier) +
  vignettes photos en arrière-plan + barre de progression + aperçu image.
- [x] **Incrément 3** — Épinglage de cibles (clic droit), accès rapide
  numéroté 1-9, désépinglage, raccourcis clavier câblés.
- [x] **Incrément 4** — Tri déplacer/copier/corbeille + annulation (Ctrl+Z) +
  journal + export à la fermeture. **Bonus : glisser-déposer** vers l'accès
  rapide et l'arborescence (hors SPEC, demandé).
- [x] **Incrément 5** — Métadonnées EXIF/GPS (photos) + ffprobe (vidéos),
  1ʳᵉ frame vidéo (ffmpeg) avec overlay « play », badge « sans GPS » en
  galerie, lecteur vidéo + métadonnées détaillées dans l'aperçu.
- [x] **Incrément 6** — Carte Leaflet (QtWebEngine + QWebChannel) : marqueurs
  OSM, clustering avec compteur, sélection bidirectionnelle carte ⇄ galerie,
  bouton « Agrandir la carte » (vue centrale).
- [x] **Incrément 7** — Détection de doublons (identiques, rouge) et similaires
  (orange) sur métadonnées : filtrage du switch, code couleur, isolement d'un
  groupe au clic. Seuils nommés (≤ 2 s, < 50 m).
- [x] **Incrément 7b — Backlog** (idées hors SPEC, cf. `Backlog.txt`) :
  pivoter/recadrer/convertir + switch Remplacer/Copier, renommage, zoom/pan,
  taille des vignettes, **plein écran + nav ←/→**, **tri/filtres/recherche**,
  **Ctrl+A/Échap + stats**, **menu contextuel** (ouvrir/explorateur/copier/
  pivoter), **nouveau dossier cible**, **EXIF étendu**, **doublons : tout sauf
  le 1er**. (Reste : diaporama.)
- [x] **Incrément 8** — Packaging PyInstaller (`build.spec`) : mode dossier ou
  mono-exe (`PICTURIT_ONEFILE=1`), bundle allégé (DevTools/locales/modules Qt
  inutiles retirés). Voir « Construction de l'exécutable » ci-dessus.

## Historique des versions

- **1.2.0** — **Réactivité** : le scan ne bloque plus
  l'interface (un clic sur un disque la figeait plusieurs minutes) ; le clic
  charge le dossier seul, le parcours récursif devient un geste explicite et
  reste interruptible. **Vignettes** : ce qui est à l'écran se charge en
  premier, les vidéos en dernier ; une seule lecture par photo, et la vignette
  EXIF embarquée est exploitée quand le dossier s'y prête. **Correctifs** :
  17,5 % des photos d'un dossier réel s'affichaient à tort comme illisibles
  (JPEG/MPO d'iPhone jugés tronqués). **Diagnostic** : journal optionnel
  (`--log` / `--log-perf`). **Interface** : contrôles vidéo complets, flèches
  de navigation en plein écran, avertissement sur l'édition destructive,
  cartes de galerie uniformes, interrupteurs redessinés, filtres type et GPS
  séparés et étiquetés, nom du dossier en tête de galerie.

- **1.1.0** — Nouvelles fonctionnalités galerie & carte. **Tri** : bouton d'ordre
  croissant/décroissant ; **« Grouper par »** dossier (défaut) / jour / semaine /
  mois (date de prise de vue). **Doublons** : coches séparées « Doublons »
  (identiques) et « Similaires » pour voir l'une, l'autre ou les deux. **Carte** :
  un clic sur un marqueur **filtre** la galerie sur ces photos (masque les autres)
  avec une bannière « Supprimer le filtre » ; le zoom reste au double-clic.
  **Plein écran** : bouton ✕ de fermeture. **Lisibilité** : sélection des
  vignettes plus visible (fond bleu + bordure) et boutons d'édition contrastés.
- **1.0.1** — Correctifs et perfs : chargement de la galerie nettement accéléré
  (décodage `Image.draft` + conversion PIL→QImage directe, ~6× plus rapide par
  vignette ; cache mémoire des métadonnées EXIF/ffprobe). Taille minimale des
  vignettes abaissée à 48 px. Clic sur un marqueur de la carte : sélectionne la
  galerie (le zoom passe au double-clic). Cohérence du switch Aperçu/Carte en
  plein écran.
- **1.0.0** — Première version complète (incréments 1 à 8).
