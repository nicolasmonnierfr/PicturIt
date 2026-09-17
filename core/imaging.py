"""Configuration commune de Pillow (décodeurs et tolérance aux fichiers réels).

Importé par ``thumbnails`` et ``metadata`` pour que **tous** les chemins de
lecture d'image partagent exactement les mêmes réglages.

Deux réglages, tous deux dictés par des fichiers réellement rencontrés :

1. **HEIC/HEIF** (photos iPhone récentes) via ``pillow_heif``, optionnel : son
   absence ne doit pas empêcher l'application de démarrer.

2. **Images tronquées** : ``LOAD_TRUNCATED_IMAGES``. Beaucoup de photos
   d'iPhone sont des **MPO** (plusieurs images concaténées : HDR, Live Photos)
   que Pillow refuse de décoder pour quelques octets manquants en fin de
   fichier — « image file is truncated (2 bytes not processed) ». Sans ce
   réglage, **17,5 % des photos** d'un dossier de test réel s'affichaient comme
   « illisibles » alors qu'elles sont parfaitement visibles.

   Contrepartie assumée : une image réellement corrompue sera décodée jusqu'où
   c'est possible, au lieu d'être rejetée. Afficher une photo partiellement
   décodée vaut mieux que de la déclarer illisible à tort.
"""

from __future__ import annotations

from PIL import ImageFile

# Décoder ce qui peut l'être plutôt que rejeter le fichier entier.
ImageFile.LOAD_TRUNCATED_IMAGES = True

# Support HEIC/HEIF (cf. SPEC 6.1) : optionnel, ne doit jamais faire planter.
HEIF_SUPPORTED = False
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
    HEIF_SUPPORTED = True
except Exception:  # noqa: BLE001 — support HEIC optionnel
    pass
