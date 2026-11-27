#!/usr/bin/env python3
"""Extractor de sesiones Amp (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state


def amp_proj_from_uri(uri):
    """Deriva el proyecto (org-repo) desde el uri file:// de Amp (coffe-n35).

    El proyecto canónico es el repo bajo para/areas/dev/gh/<org>/<repo>,
    nombrado como clean_proj_name ("charly-coffee", "sk-REPLy-jl") para que
    la taxonomía y project_daily lo crucen con las demás fuentes.
    Devuelve None si el uri no es derivable.
    """
    path = uri or ""
    if path.startswith("file://"):
        path = path[len("file://"):]
    marker = "/para/areas/dev/gh/"
    if marker not in path:
        return None
    rest = path.split(marker, 1)[1].strip("/").split("/")
    if len(rest) >= 2 and rest[1]:
        return clean_proj_name(f"{rest[0]}-{rest[1]}")
    return clean_proj_name(rest[0]) if rest and rest[0] else None


def _amp_row(ts, proj, session):
    return {
        "source": "amp", "tool": "amp",
        "model_raw": "amp", "model_family": "amp", "model_version": "v1",
        "project": proj,
        "timestamp": ts.isoformat(), "hour": hour_key(ts),
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_effective": 0,
        "session_file": session,  # add-session-spend-report (hilo amp = sesión)
    }


def _amp_file_hit(f):
    """(ts, proyecto derivado) por archivo tocado, o None si fuera de
    scope/ventana o ilegible."""
    try:
        entry = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
        _debug_skip(f"amp: {e}")
        return None
    uri = entry.get("uri", "")
    if not is_charly(uri):  # coffe-n35: unifica con --filter
        return None
    ts = parse_ts(entry.get("timestamp"))
    if ts and in_window(ts):  # coffe-snj: ventana
        return (ts, amp_proj_from_uri(uri) or "amp-unknown")
    return None


def extract_amp():
    """Extrae de Amp (@ampcode/cli, agente autónomo). Sin costo.

    coffe-n35: el filtro es is_charly(uri) (respeta --filter) y el label
    es el proyecto derivado del uri, no "charly/amp-auto" fijo.
    """
    rows = []
    amp_dir = AMP_DIR / "file-changes"
    if not amp_dir.exists():
        return rows
    for td in amp_dir.iterdir():
        if not td.is_dir():
            continue
        hits = [h for h in (_amp_file_hit(f) for f in td.iterdir()) if h]
        for ts, proj in hits:
            rows.append(_amp_row(ts, proj, td.name))
    return rows

usage_state._link(globals())
