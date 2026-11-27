"""Módulo extraído de agent-metrics-tracker.py (decomposición, comportamiento idéntico).

Repos git locales: descubrimiento de repos y métricas de creación/mantenimiento.
"""
from __future__ import annotations

import os
from pathlib import Path

from agent_metrics_common import _bead_rel_name, _git


# ----------------------------------------------------------------------
# Repos (git local)
# ----------------------------------------------------------------------

def _walk_git_root(root, max_depth, found):
    """Walk acotado de una raíz existente: registra dirs con .git y no baja
    dentro de un repo (ni más allá de max_depth)."""
    root_depth = len(root.parts)
    for dirpath, dirnames, _files in os.walk(root):
        p = Path(dirpath)
        if len(p.parts) - root_depth >= max_depth:
            dirnames[:] = []
        if ".git" in dirnames:
            dirnames[:] = [d for d in dirnames if d != ".git"]
            found[str(p)] = p


def find_git_repos(roots, max_depth=3):
    """Repos git bajo cada raíz (profundidad acotada), sin duplicados.

    Devuelve (paths, raíces_inexistentes): una raíz configurada que no existe
    se reporta en metadata, nunca se ignora en silencio.
    """
    found = {}
    missing = []
    for raw in roots:
        root = Path(os.path.expanduser(raw))
        if not root.exists():
            missing.append(raw)
        else:
            _walk_git_root(root, max_depth, found)
    return [found[k] for k in sorted(found)], missing


def _commit_days(log):
    """Días (ISO, únicos y ordenados) con commit en el log."""
    dates = [line.strip() for line in log.splitlines() if line.strip()]
    return sorted(set(dates))


def _days_by_month(days):
    """Días con commit agregados por mes calendario (los días no se solapan
    entre meses, así el conteo de días del rango es exacto)."""
    days_by_month = {}
    for d in days:
        days_by_month[d[:7]] = days_by_month.get(d[:7], 0) + 1
    return days_by_month


def _repo_metrics_row(path, run, prefixes):
    log = run(["log", "--date=short", "--format=%ad"], path)
    if not log:
        return None
    days = _commit_days(log)
    if not days:
        return None
    return {"name": _bead_rel_name(path, prefixes), "first_commit": days[0],
            "days_by_month": _days_by_month(days)}


def repo_metrics(paths, run=_git, prefixes=None):
    """Repos → {name, first_commit, days_by_month}.

    Días con commit por mes calendario: la ventana del dashboard suma meses.
    `prefixes` = raíces configuradas; el nombre es relativo a la que matchea
    (sin prefijos hardcodeados).
    """
    expanded = sorted((os.path.expanduser(x) for x in
                       (prefixes or ["~/para/areas/dev/gh"])),
                      key=len, reverse=True)
    rows = [_repo_metrics_row(path, run, expanded) for path in paths]
    return [row for row in rows if row is not None]
