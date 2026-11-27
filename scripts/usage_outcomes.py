#!/usr/bin/env python3
"""Outcomes por tool + picos de sesiones simultáneas (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def collect_outcomes():
    """FPA-014: commits/releases por proyecto por mes vía token de GitHub.

    Sin GITHUB_TOKEN (o sin mapeo proyecto→repo) se emite la estructura vacía
    con la razón en metadata.outcomes — n/a explícito, nunca inventado.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return {}, "unavailable: no hay GITHUB_TOKEN — commits/releases no emisionados"
    # Con token haría falta el mapeo proyecto local → repo remoto (no existe aún);
    # F6 lo conecta. Estructura ya declarada en el schema (monthly.outcomes_by_project).
    return {}, "unavailable: mapeo proyecto→repo GitHub pendiente (ver README)"


def _session_interval(s):
    """(first, last) parseados de una sesión, o None si no computable:
    falta timestamp o solo hay fecha sin hora."""
    f = s.get("first_ts_full") or s.get("first_ts")
    l = s.get("last_ts_full") or s.get("last_ts")
    if not f or not l:
        return None
    fi, li = parse_ts(f), parse_ts(l)
    if not fi or not li or len(str(f)) < 16:  # solo fecha = sin hora, no computable
        return None
    return fi, li


def _peak_from_events(intervals):
    events = []
    for fi, li in intervals:
        events.append((fi, 1))
        events.append((li, -1))
    # cierres antes de aperturas en el mismo instante → no cuenta doble
    events.sort(key=lambda e: (e[0], e[1]))
    peak = cur = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def _peak_simultaneous_sessions(sessions):
    """FPA-120(b): máximo solapamiento de sesiones con sweep line.
    None si falta algún timestamp (no se puede computar con honestidad)."""
    intervals = []
    for s in sessions:
        iv = _session_interval(s)
        if iv is None:
            return None
        intervals.append(iv)
    return _peak_from_events(intervals)

usage_state._link(globals())
