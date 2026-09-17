"""Journalisation de diagnostic — **désactivée par défaut**, toujours débrayable.

Seule entorse assumée à la règle « zéro persistance » (SPEC 2) : quand elle est
activée, l'application écrit un fichier de log, afin de pouvoir diagnostiquer
une lenteur ou une erreur sur la machine de l'utilisateur. Rien n'est écrit tant
que la journalisation n'a pas été demandée explicitement.

Activation, par ordre de priorité :

1. option de ligne de commande : ``main.py --log`` (ou ``--log-perf``) ;
2. variable d'environnement : ``PICTURIT_LOG=1`` (ou ``debug`` / ``perf``) ;
3. sinon : **rien**, aucun fichier créé, surcoût nul.

Emplacement du fichier : ``%LOCALAPPDATA%\\PicturIt\\picturit.log``, avec
rotation (3 fichiers de 2 Mo). On n'écrit jamais à côté de l'exécutable, qui
peut être installé dans Program Files sans droit d'écriture.

Le chemin peut être imposé par ``PICTURIT_LOG_FILE``.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

LOGGER_NAME = "picturit"

# Niveaux acceptés dans PICTURIT_LOG / --log=…
_LEVELS = {
    "1": logging.INFO,
    "true": logging.INFO,
    "info": logging.INFO,
    "debug": logging.DEBUG,
    "perf": logging.DEBUG,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}

_configured = False
_perf_enabled = False


def default_log_path() -> str:
    """Chemin du fichier de log (créé seulement si la journalisation est active)."""
    override = os.environ.get("PICTURIT_LOG_FILE")
    if override:
        return override
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, "PicturIt", "picturit.log")


def _requested_level() -> int | None:
    """Niveau demandé par l'environnement, ou None si la journalisation est off."""
    raw = os.environ.get("PICTURIT_LOG", "").strip().lower()
    if raw in ("", "0", "false", "off", "no"):
        return None
    return _LEVELS.get(raw, logging.INFO)


def configure(level: int | None = None, to_console: bool = False) -> bool:
    """Active la journalisation. Renvoie True si elle est effectivement active.

    *level* à None : le niveau est lu dans l'environnement ; si rien n'y est
    demandé, la journalisation reste **désactivée** et aucun fichier n'est créé.
    """
    global _configured, _perf_enabled

    if level is None:
        level = _requested_level()
    if level is None:
        return False

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False

    if _configured:  # déjà configuré : on ne réinstalle pas les handlers
        return True

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S"
    )

    path = default_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = RotatingFileHandler(
            path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    except OSError as exc:  # disque plein, droits manquants… : on continue sans fichier
        print(f"PicturIt : journalisation fichier impossible ({exc})", file=sys.stderr)

    if to_console:
        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(fmt)
        logger.addHandler(console)

    _perf_enabled = level <= logging.DEBUG
    _configured = True
    logger.info("Journalisation active (niveau %s) → %s",
                logging.getLevelName(level), path)
    return True


def get_logger(suffix: str = "") -> logging.Logger:
    """Logger nommé ``picturit`` ou ``picturit.<suffix>``."""
    return logging.getLogger(f"{LOGGER_NAME}.{suffix}" if suffix else LOGGER_NAME)


def perf_enabled() -> bool:
    """True si les mesures de performance doivent être collectées."""
    return _perf_enabled

