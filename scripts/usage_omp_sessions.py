#!/usr/bin/env python3
"""Extractor de sesiones omp: resumen por sesión (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def _omp_session_ts(meta, first):
    last = meta["last_ts"] or first
    return {
        "first_ts_full": meta["first_raw"] or first.isoformat(),
        "last_ts_full": meta["last_raw"] or (last or first).isoformat(),
    }


def _omp_session_dict(meta, calls, cwd, first):
    n_turns = sum(1 for c in calls if c[3] == "assistant_turn")
    out = {
        "tool": "omp",
        "source": "omp_session_files",
        "project": amp_proj_from_uri(cwd) or "omp-unknown",
        "first_ts": first.isoformat()[:10],
        "duration_msgs": len(meta["user_ts"]),
        "n_turns": n_turns,
        "n_tools": len(meta["tools"]),
        "has_agent": False,
        "n_skills": None,
        "n_errors": meta["n_errors"],
        "n_compactions": None,
    }
    out.update(_omp_session_ts(meta, first))
    return out


def _omp_session_row(meta, calls, sd_name):
    """Sesiones omp — 1 archivo JSONL = 1 sesión (misma semántica de reset
    que pi: cada sesión abre archivo nuevo, el archivo ES la señal).

    n_turns = llamadas principales (assistant con usage); las auxiliares
    (model_usage) facturan tokens pero no son turnos de conversación.
    n_errors es señal real del log (errorId en la llamada); skills y
    compactions sin señal en el formato omp (None — _session_stats las
    excluye de los totales, no las cuenta como 0). has_agent=False: sin
    señal de subagentes. La ventana --since/--until se aplica en
    extract_sessions() (filter_sessions). sessions_dir permite tests.
    """
    cwd = meta["cwd"] or sd_name
    if not is_charly(cwd):
        return None
    first = meta["first_ts"] or meta["session_ts"]
    n_turns = sum(1 for c in calls if c[3] == "assistant_turn")
    if not n_turns and not meta["user_ts"] and first is None:
        return None  # archivo sin actividad
    return _omp_session_dict(meta, calls, cwd, first)


def _omp_session_file(f, sessions):
    try:
        meta, calls = _omp_scan(f)
    except OSError:
        return
    if meta is None:
        return
    row = _omp_session_row(meta, calls, f.parent.name)
    if row is not None:
        sessions.append(row)
    return sessions


def extract_omp_sessions(sessions_dir=None):
    sessions = []
    sessions_dir = sessions_dir or OMP_DIR
    if not sessions_dir.exists():
        return sessions
    for sd in sorted(sessions_dir.iterdir()):
        if not sd.is_dir():
            continue
        for f in sorted(sd.glob("*.jsonl")):
            _omp_session_file(f, sessions)
    return sessions

usage_state._link(globals())
