"""Módulo extraído de agent-metrics-tracker.py (decomposición, comportamiento idéntico).

Constantes compartidas y utilidades puras usadas por todos los módulos
agent_metrics_*.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "data" / "usage_report_v3.json"
CONFIG = ROOT / "config" / "fpa.json"
OUT = ROOT / "data" / "agent_metrics.json"
SCHEMA = ROOT / "specs" / "agent-metrics.schema.json"

PI_DIR = Path.home() / ".pi" / "agent" / "sessions"
CLAUDE_DIR = Path.home() / ".claude" / "projects"

DEFAULTS = {
    "gap_cap_minutes": 30,        # gap mayor = inactividad, no trabajo del agente
    "retry_window_minutes": 5,    # prompt de usuario que sigue a un error
    "max_task_minutes": 240,      # tope de cordura para una tarea
    "repo_roots": ["~/para/areas/dev/gh"],
    "repo_max_depth": 3,
    "min_commit_days": 5,         # "mantenido": commit en >5 días
    "min_returning_months": 2,    # ... y vuelve en al menos 2 meses
    "bulk_day_interactions": 5000,  # día "de máquina" (volumen de API, no de persona)
}

FLAG_ABORTED, FLAG_ERROR, FLAG_TOOL_ERROR, FLAG_RETRY = 1, 2, 4, 8


# ----------------------------------------------------------------------
# Config / utilidades
# ----------------------------------------------------------------------

def load_config(path=None):
    """Bloque `agent_behavior` del config único del sitio, con defaults."""
    cfg = dict(DEFAULTS)
    p = Path(path) if path else CONFIG
    if p.exists():
        raw = json.loads(p.read_text(encoding="utf-8")).get("agent_behavior") or {}
        cfg.update({k: v for k, v in raw.items() if v is not None})
        cfg["config_source"] = str(p.relative_to(ROOT)) if p.is_absolute() else str(p)
    else:
        cfg["config_source"] = None
    return cfg


def _dt(value):
    """ISO-8601 → datetime UTC. None si no parsea."""
    if not value:
        return None
    try:
        s = str(value).replace("Z", "+00:00")
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def tz_from_offset(value):
    """Offset del reporte ('-03', '-03:00', 'UTC') → tzinfo; None si no parsea.

    El sitio bucketiza por TZ local (metadata.timezone); el tracker convierte
    los timestamps UTC de los logs para atribuir el día igual que el reporte.
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if s.upper() in ("UTC", "Z", "GMT"):
        return timezone.utc
    m = re.match(r"^([+-])(\d{1,2})(?::?(\d{2}))?$", s)
    if not m:
        return None
    sign = 1 if m.group(1) == "+" else -1
    delta = timedelta(hours=int(m.group(2)), minutes=int(m.group(3) or 0))
    return timezone(sign * delta)


def task_day(task, tz=None):
    """Día (ISO) de la tarea en la TZ del reporte; UTC si no hay tz."""
    ts = task["_start"]
    return (ts.astimezone(tz) if tz else ts).date().isoformat()


def _pctl(sorted_vals, p):
    if not sorted_vals:
        return None
    return sorted_vals[int(p * (len(sorted_vals) - 1))]


def _iter_jsonl(path):
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _usage_tokens(u):
    """usage de un turno assistant → tokens {in, out, cache_read,
    cache_write}. Acepta el formato pi ({input, output, cacheRead,
    cacheWrite}) y el claude ({input_tokens, output_tokens, ...})."""
    if not isinstance(u, dict):
        return {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
    return {"in": u.get("input_tokens") or u.get("input") or 0,
            "out": u.get("output_tokens") or u.get("output") or 0,
            "cache_read": u.get("cache_read_input_tokens") or u.get("cacheRead") or 0,
            "cache_write": u.get("cache_creation_input_tokens") or u.get("cacheWrite") or 0}


def _cost_from(cost):
    """usage.cost puede venir como dict {total: n} (pi) o número; nada
    medible → 0.0."""
    if isinstance(cost, dict):
        cost = cost.get("total")
    return float(cost) if isinstance(cost, (int, float)) else 0.0


def _project_from_cwd(cwd):
    """Proyecto de la sesión resuelto desde su cwd: última componente del
    path (cwd ausente → None, la sesión queda sin proyecto)."""
    if not isinstance(cwd, str) or not cwd:
        return None
    return Path(cwd).name or cwd


def _session_from_records(records, source):
    """Encabezado de sesión → {source, project, session_id}. pi lo declara
    en el registro `type: session` (con cwd); claude-cli trae sessionId/cwd
    en cada línea. Sin encabezado reconocible → sesión anónima."""
    for r in records[:10]:
        if source == "pi":
            if r.get("type") != "session":
                continue
            return {"source": source, "project": _project_from_cwd(r.get("cwd")),
                    "session_id": r.get("id")}
        if r.get("sessionId"):
            return {"source": source, "project": _project_from_cwd(r.get("cwd")),
                    "session_id": r.get("sessionId")}
    return {"source": source, "project": None, "session_id": None}


PREAMBLE_MARKERS = (
    "You are resuming an existing conversation",
    "This session is being continued from a previous conversation",
)


def _prompt_text(content):
    """Contenido de un mensaje user → texto plano (string o bloques con
    `text`). coffe-dnx.5: materia prima de la atribución explícita."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and isinstance(b.get("text"), str))
    return ""


def _is_preamble(content):
    """Mensaje `user` inyectado por el harness al reanudar una sesión: no es
    un prompt humano (mismo criterio que usage-tracker.py, FPA-140)."""
    if not isinstance(content, (str, list)):
        return False
    head = _prompt_text(content).strip()[:160]
    return any(head.startswith(marker) for marker in PREAMBLE_MARKERS)


def _is_tool_result(content):
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def _claude_tool_error(r):
    tur = r.get("toolUseResult")
    return bool(tur.get("is_error")) if isinstance(tur, dict) else False


def _git(args, cwd):
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _bead_rel_name(path, prefixes):
    """Nombre `<org>/<repo>`: relativo a la raíz que matchea (misma regla que
    repo_metrics, prefijos más largos primero)."""
    name = str(path)
    expanded = sorted((os.path.expanduser(x) for x in prefixes),
                      key=len, reverse=True)
    for prefix in expanded:
        if name.startswith(prefix + os.sep):
            return name[len(prefix) + 1:]
    return name
