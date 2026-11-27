#!/usr/bin/env python3
"""Tiempos, ventana --since/--until y scope de proyectos (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def parse_ts(ts):
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return None

def hour_key(dt):
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:00")


def parse_window(since=None, until=None):
    """coffe-snj: valida --since/--until (YYYY-MM-DD, extremos inclusive).

    Devuelve (date|None, date|None). ValueError si el formato es inválido
    o since > until (falla loud, nunca ventana silenciosamente invertida).
    """
    def _d(s, flag):
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            raise ValueError(
                f"--{flag} inválido: {s!r} (esperado YYYY-MM-DD)") from None
    sd, ud = _d(since, "since"), _d(until, "until")
    if sd and ud and sd > ud:
        raise ValueError(f"--since {sd} es posterior a --until {ud}")
    return sd, ud


def in_window(dt):
    """True si dt cae en la ventana SINCE/UNTIL (extremos inclusive).

    Acepta datetime o date; evalúa la fecha tal cual (los extractores
    producen timestamps UTC-aware, así que la ventana es por fecha UTC).
    Sin ventana activa → True. dt None → True (los extractores ya descartan
    filas sin fecha antes de llegar acá)."""
    if dt is None:
        return True
    d = dt.date() if isinstance(dt, datetime) else dt
    if SINCE and d < SINCE:
        return False
    if UNTIL and d > UNTIL:
        return False
    return True


def _session_overlaps(s, since, until):
    first = (s.get("first_ts") or "")[:10]
    last = (s.get("last_ts_full") or s.get("first_ts") or "")[:10]
    if since and last and last < since.isoformat():
        return False
    if until and first and first > until.isoformat():
        return False
    return True


def filter_sessions(sessions, since=None, until=None):
    """Sesiones que se solapan con [since, until] (fechas, inclusive).

    Una sesión que empieza antes de `since` pero termina dentro cuenta
    (su trabajo parcial está en la ventana); sin ventana → lista completa.
    Sesiones sin fechas no se descartan (los extractores ya filtran las
    filas por fecha; una sesión sin fecha no se pierde en silencio)."""
    if not since and not until:
        return list(sessions)
    return [s for s in sessions if _session_overlaps(s, since, until)]

_SCOPE_ORGS = ("charly", "sk", "ak")
_SCOPE_SEPS = ("-gh-", "/gh/")


def _label_in_scope(p):
    return any(p == org or p.startswith(org + "-") or p.startswith(org + "/")
               for org in _SCOPE_ORGS)


def _path_org_in_scope(p, sep, org):
    # el org debe ser un segmento completo: seguido de '-', '/' o fin
    # (cubre path mungeado '-gh-charly-coffee' y uri '…/gh/charly/coffe')
    i = p.find(sep + org)
    while i != -1:
        j = i + len(sep) + len(org)
        if j >= len(p) or p[j] in "-/":
            return True
        i = p.find(sep + org, i + 1)
    return False


def in_scope(proj):
    """Scope del reporte cuando --filter charly (default): proyectos de los
    tres orgs — charly (charly-vibes), sk (sashakile) y ak (akielbowicz,
    coffe-vp8). Con --filter all entra todo.

    `proj` puede venir en dos formas: label limpio ("ak-journal", post
    clean_proj_name) o path mungeado de Claude ("-var-home-sasha-para-
    areas-dev-gh-charly-coffee"), por eso el match es por prefijo de label
    O por segmento de org en el path ("-gh-<org>-", "/gh/<org>/").
    """
    if not CHARLY_FILTER:
        return True
    p = (proj or "").lower()
    if _label_in_scope(p):
        return True
    return any(_path_org_in_scope(p, sep, org)
               for sep in _SCOPE_SEPS for org in _SCOPE_ORGS)


# Alias histórico: el nombre viejo mentía (también filtra ak/sk desde v4.5).
is_charly = in_scope

def model_details(model_id):
    """coffe-dnx.1: modelo → (familia, versión) desde config/fpa.json
    (bloque model_mapping; orden por priority, primera coincidencia gana).

    Sin match → ("other", model_id[:20]): fallback conservado (no se borra,
    solo queda visible y contado en metadata.model_mapping_coverage).
    """
    m = model_id.lower()
    for rx, family, version in _MODEL_MAPPING:
        if rx.search(m):
            return (family, version)
    return ("other", model_id[:20])


def clean_proj_name(raw):
    """Normaliza nombres de proyecto a forma canónica entre fuentes.

    Claude usa paths con '/' reemplazado por '-' (p.ej. el repo en
    <home>/para/areas/dev/gh/charly/coffe aparece como
    '-var-home-sasha-para-areas-dev-gh-charly-coffe'). El prefijo se deriva
    de Path.home() en runtime para ser independiente de la máquina.
    Pi usa nombres de directorio distintos ('-charly-atril/',
    '-sk-REPLy.jl/' → 'sk-REPLy-jl').
    """
    home_prefix = str(Path.home()).replace("/", "-") + "-para-areas-dev-gh-"
    p = raw.replace(home_prefix, "")
    p = p.strip("-/")
    p = p.replace(".jl", "-jl")  # repos Julia: REPLy.jl → REPLy-jl
    return {"charly-mibilioteca": "charly-miblioteca",  # typo en sesiones Pi
            "sk-sxAct": "sk-XAct-jl",  # sxAct no existe; repo real XAct.jl
            # coffe-vp8: Claude mungea puntos del path a '-', Pi no — un repo,
            # un label.
            "ak-akielbowicz-github-io": "ak-akielbowicz.github.io",
            # coffe-vp8: uri de Amp apuntaba a un archivo en la raíz del org
            # (gh/ak/justfile), no a un repo — el label correcto es la org.
            "ak-justfile": "ak",
            # repo renombrado coffe→coffee (bd coffe-85z): los logs históricos
            # derivan 'charly-coffe' del path viejo; se canonicaliza al nombre
            # corregido para que todo el dataset use un solo label.
            "charly-coffe": "charly-coffee"}.get(p, p)

usage_state._link(globals())
