"""Détection de doublons (identiques) et de similaires — métadonnées uniquement.

Définitions (cf. SPEC 4.7) :
- **Identiques** : même contenu (hash SHA-256) **OU** (même taille **ET** même
  datetime de prise de vue). → code couleur **rouge**.
- **Similaires** : datetime de prise de vue à ≤ 2 s d'écart **ET** localisation
  GPS proche (< 50 m). → code couleur **orange**.

⚠️ Hors v1 : aucune similarité visuelle (perceptual hash). On se base seulement
sur les métadonnées (temps + GPS + taille + hash binaire).

Optimisation : seuls les fichiers partageant une même taille sont hachés (un
contenu identique implique une taille identique), ce qui évite de lire l'octet
de milliers de fichiers inutilement.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field

from core import geo, metadata

# Seuils des « similaires » (constantes nommées, cf. SPEC 4.7).
SIMILAR_TIME_SECONDS = 2.0
SIMILAR_GPS_METERS = 50.0

# Codes couleur.
COLOR_IDENTICAL = "identical"  # rouge
COLOR_SIMILAR = "similar"      # orange


@dataclass
class DupIndex:
    """Résultat d'analyse des doublons/similaires."""

    # chemin -> clé du groupe à afficher (identique prioritaire sur similaire).
    group_of: dict[str, str] = field(default_factory=dict)
    # clé de groupe -> ensemble des chemins membres.
    groups: dict[str, set[str]] = field(default_factory=dict)
    # chemin -> code couleur (identique / similaire).
    color_of: dict[str, str] = field(default_factory=dict)

    def grouped_paths(self) -> set[str]:
        """Chemins faisant partie d'un groupe (à afficher en mode doublons)."""
        return set(self.group_of.keys())

    def members_of(self, path: str) -> set[str]:
        """Membres du groupe auquel appartient *path* (vide si aucun)."""
        key = self.group_of.get(path)
        return self.groups.get(key, set()) if key else set()


class _UnionFind:
    """Union-find minimaliste pour regrouper les fichiers liés."""

    def __init__(self, items) -> None:
        self._parent = {x: x for x in items}

    def find(self, x):
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Compression de chemin.
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def components(self) -> dict:
        comps: dict = defaultdict(set)
        for x in self._parent:
            comps[self.find(x)].add(x)
        return comps


def _sha256(path: str, chunk: int = 1 << 20) -> str | None:
    """Calcule le SHA-256 du contenu binaire (par blocs). None si illisible."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for block in iter(lambda: fh.read(chunk), b""):
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def analyze(paths: list[str]) -> DupIndex:
    """Analyse *paths* et renvoie l'index des doublons/similaires."""
    metas = {p: metadata.read(p) for p in paths}

    # --- Identiques ---
    ident = _UnionFind(paths)

    # 1) Par hash, mais uniquement entre fichiers de même taille (optimisation).
    by_size: dict[int, list[str]] = defaultdict(list)
    for p in paths:
        by_size[metas[p].size].append(p)
    by_hash: dict[str, list[str]] = defaultdict(list)
    for group in by_size.values():
        if len(group) < 2:
            continue
        for p in group:
            digest = _sha256(p)
            if digest:
                by_hash[digest].append(p)
    for group in by_hash.values():
        for p in group[1:]:
            ident.union(group[0], p)

    # 2) Par (taille + datetime de prise de vue) identiques.
    by_size_dt: dict[tuple, list[str]] = defaultdict(list)
    for p in paths:
        meta = metas[p]
        if meta.datetime_original is not None:
            by_size_dt[(meta.size, meta.datetime_original)].append(p)
    for group in by_size_dt.values():
        for p in group[1:]:
            ident.union(group[0], p)

    # --- Similaires (temps proche ET GPS proche) ---
    similar = _UnionFind(paths)
    timed_geo = [
        p for p in paths
        if metas[p].datetime_original is not None and metas[p].has_gps
    ]
    timed_geo.sort(key=lambda p: metas[p].datetime_original)
    for i, p in enumerate(timed_geo):
        mi = metas[p]
        for q in timed_geo[i + 1:]:
            mq = metas[q]
            dt = abs((mq.datetime_original - mi.datetime_original).total_seconds())
            if dt > SIMILAR_TIME_SECONDS:
                break  # liste triée : au-delà, plus aucun voisin temporel
            dist = geo.haversine_m(mi.latitude, mi.longitude, mq.latitude, mq.longitude)
            if dist <= SIMILAR_GPS_METERS:
                similar.union(p, q)

    # --- Construction de l'index (identique prioritaire) ---
    index = DupIndex()

    for root, members in ident.components().items():
        if len(members) < 2:
            continue
        key = f"ident:{root}"
        index.groups[key] = set(members)
        for p in members:
            index.group_of[p] = key
            index.color_of[p] = COLOR_IDENTICAL

    for root, members in similar.components().items():
        if len(members) < 2:
            continue
        key = f"sim:{root}"
        index.groups[key] = set(members)
        for p in members:
            # Ne pas écraser une appartenance « identique » déjà attribuée.
            if p not in index.group_of:
                index.group_of[p] = key
                index.color_of[p] = COLOR_SIMILAR

    return index
