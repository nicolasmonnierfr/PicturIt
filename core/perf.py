"""Mesures de performance — **désactivées par défaut**, écrites dans le log.

Sert à localiser les goulots d'étranglement (chargement d'un dossier, vignettes,
métadonnées, carte) sans avoir à deviner.

Les mesures ne sont collectées que si la journalisation est active en niveau
DEBUG (cf. ``core.logs``) :

    .\\.venv\\Scripts\\python.exe main.py --log-perf

Sinon le surcoût se limite à un test de booléen par appel, et **aucun fichier
n'est créé**.

Deux outils complémentaires :

- ``step("nom")`` — opération **ponctuelle** (un scan, un affichage) : chaque
  passage est journalisé immédiatement ;
- ``measure("nom")`` — opération **répétée** des milliers de fois (une vignette,
  une lecture EXIF) : les durées sont agrégées, sinon le log noierait
  l'information utile sous des milliers de lignes.

``report()`` écrit le bilan, trié par temps total cumulé.
"""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager

from core import logs

# Agrégats : nom -> [nombre d'appels, temps total ms, temps max ms].
_totals: dict[str, list] = {}
_lock = threading.Lock()


def enabled() -> bool:
    """True si les mesures sont collectées (journalisation en DEBUG)."""
    return logs.perf_enabled()


@contextmanager
def step(name: str):
    """Mesure une opération ponctuelle et la journalise immédiatement."""
    if not enabled():
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        duration = (time.perf_counter() - start) * 1000
        logs.get_logger("perf").debug("%s : %.1f ms", name, duration)
        _add(name, duration)


@contextmanager
def measure(name: str):
    """Mesure une opération répétée : agrégée, pas journalisée ligne à ligne."""
    if not enabled():
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        _add(name, (time.perf_counter() - start) * 1000)


def _add(name: str, duration_ms: float) -> None:
    with _lock:
        entry = _totals.setdefault(name, [0, 0.0, 0.0])
        entry[0] += 1
        entry[1] += duration_ms
        entry[2] = max(entry[2], duration_ms)


def note(message: str, *args) -> None:
    """Journalise un événement ponctuel sans durée (taille de lot, compteur…)."""
    if enabled():
        logs.get_logger("perf").debug(message, *args)


def reset() -> None:
    """Remet les agrégats à zéro (début d'une nouvelle mesure)."""
    with _lock:
        _totals.clear()


def snapshot() -> list[tuple[str, int, float, float]]:
    """Copie des agrégats : (nom, appels, total ms, max ms), plus lourd d'abord."""
    with _lock:
        lignes = [(nom, v[0], v[1], v[2]) for nom, v in _totals.items()]
    return sorted(lignes, key=lambda row: row[2], reverse=True)


def report(title: str = "Bilan") -> None:
    """Écrit le bilan agrégé dans le log, trié par temps total décroissant."""
    if not enabled():
        return
    lignes = snapshot()
    if not lignes:
        return
    logger = logs.get_logger("perf")
    logger.debug("--- %s ---", title)
    logger.debug(
        "%-38s %7s %10s %9s %9s",
        "operation", "appels", "total ms", "moyenne", "max ms",
    )
    for nom, appels, total, maximum in lignes:
        logger.debug(
            "%-38s %7d %10.0f %9.2f %9.1f",
            nom, appels, total, total / appels, maximum,
        )
