#!/usr/bin/env python3
"""
agent-metrics-tracker.py — métricas de comportamiento del agente.

Purpose: medir el eje que el talk de Armin Ronacher ("Build the Workshop
Before the Software Factory") presenta y que el reporte de uso no cubre:
- prompts de usuario y turnos abiertos por el modelo,
- duración de tarea (cuánto corre el agente por pedido) con percentiles,
- tiempo activo del agente (no wall-clock humano),
- abortos/errores y reintentos tras error (proxy de "pushback"),
- repos creados y mantenidos (git local, sin GITHUB_TOKEN).

Responsibilities:
- parsear los logs con timestamps por turno (`~/.pi/agent/sessions/*/*.jsonl`,
  `~/.claude/projects/**/*.jsonl`) → turnos con rol, stopReason, isError;
- agrupar turnos en tareas (un prompt de usuario abre tarea, el siguiente la
  cierra) y sumar el tiempo que el agente estuvo ocupado — `durationMs` del
  turno cuando existe, si no el gap desde el evento previo, con tope
  `gap_cap_minutes` para no contar inactividad humana como trabajo del agente;
- emitir `data/agent_metrics.json` (serie diaria, series compactas de tareas
  para percentiles por ventana, y panel de repos por mes);
- escanear repos git locales para "creados" (primer commit) y "mantenidos"
  (días con commit + retorno en meses distintos) — la métrica del talk sin
  depender de la API de GitHub (`metadata.outcomes` del reporte sigue siendo
  `unavailable` por falta de token).

Rationale: el dashboard necesita percentiles exactos por ventana arbitraria
(el P90 de una tarea no se promedia desde P90 mensuales), por eso se emite la
serie de tareas completa y el viz recalcula. Se declara cobertura por fuente:
claude-cli no registra stopReason, así que sus abortos son `missing` con
razón, nunca 0. Cero dependencias (stdlib-only), igual que el resto del repo.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
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


# ----------------------------------------------------------------------
# Turnos por fuente
# ----------------------------------------------------------------------

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


def _pi_user_turn(msg, ts, session, model):
    """coffe-dnx.5: el texto del prompt viaja en el turno para la atribución
    explícita tarea↔bead (ids en el prompt)."""
    return {"ts": ts, "role": "user_prompt", "model": model,
            "text": _prompt_text(msg.get("content"))[:2000], "ses": session}


def _pi_assistant_turn(msg, ts, session, model):
    dur_ms = msg.get("durationMs")
    usage = msg.get("usage")
    return {
        "ts": ts, "role": "assistant",
        "model": msg.get("model") or model,
        "stop": msg.get("stopReason"),
        "dur_s": (float(dur_ms) / 1000.0) if isinstance(dur_ms, (int, float)) else None,
        "usage": _usage_tokens(usage),
        "cost": _cost_from((usage or {}).get("cost")),
        "ses": session,
    }


def _pi_tool_turn(msg, ts, session, model):
    return {"ts": ts, "role": "tool", "is_error": bool(msg.get("isError")),
            "ses": session}


_PI_TURN_BUILDERS = {"user": _pi_user_turn, "assistant": _pi_assistant_turn,
                     "toolResult": _pi_tool_turn}


def _pi_turn_from_record(r, state):
    """Un registro de sesión Pi → turno o None. `state` es [model] mutable:
    el último `model_change` visto."""
    typ = r.get("type")
    if typ == "model_change":
        state[0] = r.get("modelId") or state[0]
        return None
    if typ != "message":
        return None
    msg = r.get("message") or {}
    ts = _dt(r.get("timestamp") or msg.get("timestamp"))
    build = _PI_TURN_BUILDERS.get(msg.get("role"))
    if ts is None or build is None:
        return None
    return build(msg, ts, state[1], state[0])


def turns_from_pi(records, session=None):
    """Registros de una sesión Pi → turnos {ts, role, model, stop, is_error,
    dur_s, usage, cost, ses}. Roles: user_prompt / assistant / tool. Los
    preámbulos inyectados del harness viajan como `custom`, no como user: no
    abren tarea. `session` = {source, project, session_id} de la sesión
    (coffe-dnx.3): viaja en cada turno para que la tarea pueda llevar sid."""
    state = [None, session]
    turns = []
    for r in records:
        turn = _pi_turn_from_record(r, state)
        if turn is not None:
            turns.append(turn)
    return turns


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


def _claude_user_turn(r, ts, session, stats):
    content = (r.get("message") or {}).get("content")
    if _is_tool_result(content):
        return {"ts": ts, "role": "tool", "is_error": _claude_tool_error(r),
                "ses": session}
    if not isinstance(content, (str, list)):
        return None
    if _is_preamble(content):
        if stats is not None:
            stats["preambles_skipped"] = stats.get("preambles_skipped", 0) + 1
        return None
    return {"ts": ts, "role": "user_prompt",
            "text": _prompt_text(content)[:2000], "ses": session}


def _claude_assistant_turn(r, msg, ts, session):
    usage = msg.get("usage")
    cost = _cost_from((usage or {}).get("cost"))
    if not cost and isinstance(r.get("costUSD"), (int, float)):
        cost = float(r["costUSD"])
    return {"ts": ts, "role": "assistant", "model": msg.get("model"),
            "stop": msg.get("stop_reason"), "usage": _usage_tokens(usage),
            "cost": cost, "ses": session}


def turns_from_claude(records, stats=None, session=None):
    """Registros de una sesión Claude Code → turnos. Solo el hilo principal
    (`isSidechain` false): los subagentes duplicarían los conteos. Claude no
    escribe stopReason `aborted`, así que `stop` queda None (cobertura).
    `session` = {source, project, session_id} de la sesión (coffe-dnx.3).
    El coste por turno: claude-cli no escribe usage.cost; se toma costUSD
    cuando el log lo trae (sino 0.0 — cobertura declarada)."""
    turns = []
    for r in records:
        if r.get("isSidechain"):
            continue
        msg = r.get("message") or {}
        ts = _dt(r.get("timestamp") or msg.get("timestamp"))
        if ts is None:
            continue
        typ = r.get("type")
        if typ == "user":
            turn = _claude_user_turn(r, ts, session, stats)
        elif typ == "assistant":
            turn = _claude_assistant_turn(r, msg, ts, session)
        else:
            turn = None
        if turn is not None:
            turns.append(turn)
    return turns


def _pi_session_id(records):
    """Id de sesión declarado en los primeros registros de un archivo pi."""
    for r in records[:3]:
        if r.get("type") == "session":
            return r.get("id")
    return None


def load_pi_turns(sessions_dir=None):
    """Walk de sesiones Pi. Dedupe de forks por id de sesión: dos archivos con
    el mismo id (fork copia el historial) aportan solo el más largo."""
    base = Path(sessions_dir or PI_DIR)
    if not base.exists():
        return [], {"sessions": 0, "skipped_reason": "sin directorio de sesiones pi"}
    by_id = {}
    for path in sorted(base.glob("*/*.jsonl")):
        records = list(_iter_jsonl(path))
        if not records:
            continue
        key = _pi_session_id(records) or str(path)
        prev = by_id.get(key)
        if prev is None or len(records) > prev[1]:
            by_id[key] = (path, len(records), records)
    turns = []
    for _path, _n, records in by_id.values():
        turns.extend(turns_from_pi(records, session=_session_from_records(records, "pi")))
    return turns, {"sessions": len(by_id), "files": len(list(base.glob("*/*.jsonl")))}


def load_claude_turns(claude_dir=None):
    base = Path(claude_dir or CLAUDE_DIR)
    if not base.exists():
        return [], {"sessions": 0, "skipped_reason": "sin directorio de sesiones claude"}
    turns = []
    sessions = 0
    stats = {"preambles_skipped": 0}
    for path in sorted(base.glob("**/*.jsonl")):
        if "subagents" in path.parts:
            continue
        records = list(_iter_jsonl(path))
        if not records:
            continue
        sessions += 1
        turns.extend(turns_from_claude(records, stats,
                                       session=_session_from_records(records, "claude-cli")))
    return turns, {"sessions": sessions, **stats}


# ----------------------------------------------------------------------
# Tareas (funciones puras: testeables sin disco)
# ----------------------------------------------------------------------

@dataclass
class TaskWindows:
    """Topes de tiempo de build_tasks (segundos)."""
    gap_cap_s: float = 1800
    retry_window_s: float = 300
    max_task_s: float = 14400


def _new_task(t):
    return {"_start": t["ts"], "busy_s": 0.0, "turns": 0,
            "aborted": 0, "errors": 0, "tool_errors": 0, "retry": 0,
            "model": None, "clipped": False, "cost": 0.0,
            "tokens": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0},
            "prompt": t.get("text"), "_ses": t.get("ses")}


def _close_task(task, end_ts, tasks, max_task_s):
    """Cierra una tarea: span (con tope), busy ≤ span, y solo con actividad."""
    if task is None:
        return
    end = task.pop("_last", end_ts) or end_ts
    task["span_s"] = max(0.0, (end - task["_start"]).total_seconds())
    if task["span_s"] > max_task_s:
        task["span_s"] = max_task_s
        task["clipped"] = True
    # el durationMs reportado puede exceder el span (mensaje con timestamp
    # de inicio de respuesta): el tiempo ocupado nunca supera al de reloj
    task["busy_s"] = min(task["busy_s"], task["span_s"])
    if task["busy_s"] > 0:
        tasks.append(task)


def _turn_gap(t, prev_ts):
    """Gap desde el evento previo; un gap negativo (reordenado) cuenta 0."""
    if prev_ts is None:
        return None
    return max((t["ts"] - prev_ts).total_seconds(), 0)


def _crosses_session(cur, t):
    """coffe-dnx.3: la tarea no cruza sesiones — el primer turno de otra
    sesión cierra la tarea abierta."""
    return (cur is not None and t.get("ses") is not None
            and cur["_ses"] is not None and t["ses"] is not cur["_ses"])


def _retry_context(cur):
    """(terminó_en_fallo, fin) de la tarea anterior: la ventana de reintento
    se mide desde el último turno de la tarea anterior — usar el prompt nuevo
    daría 0 s siempre (dead knob)."""
    if cur is None:
        return False, None
    ended_error = bool((cur["aborted"] or cur["errors"]) and cur["busy_s"] > 0)
    return ended_error, cur.get("_last") or cur["_start"]


def _is_retry(t, prev, retry_window_s):
    """El prompt que abre tarea llega ≤ retry_window_s después del último
    turno de una tarea anterior que terminó en fallo y tuvo actividad."""
    prev_ended_error, prev_end_ts = prev
    return (prev_ended_error and prev_end_ts is not None
            and (t["ts"] - prev_end_ts).total_seconds() <= retry_window_s)


def _apply_assistant_turn(cur, t, gap_cap_s):
    """Turno assistant: turnos, modelo, stop, coste y tokens de la tarea.
    Devuelve el tiempo ocupado (dur_s con tope, o None)."""
    cur["turns"] += 1
    cur["model"] = cur["model"] or t.get("model")
    if t.get("stop") == "aborted":
        cur["aborted"] = 1
    elif t.get("stop") == "error":
        cur["errors"] = 1
    busy = None
    if t.get("dur_s") is not None:
        busy = min(float(t["dur_s"]), gap_cap_s)
    # coffe-dnx.3: coste y tokens de los turnos assistant de la tarea
    cur["cost"] += float(t.get("cost") or 0.0)
    usage = t.get("usage") or {}
    for k in ("in", "out", "cache_read", "cache_write"):
        cur["tokens"][k] += usage.get(k) or 0
    return busy


def _apply_turn(cur, t, gap, gap_cap_s):
    """Actualiza `cur` con un turno no-prompt; devuelve el tiempo ocupado
    (dur_s si lo trae, si no el gap, con tope gap_cap_s)."""
    if t["role"] == "assistant":
        busy = _apply_assistant_turn(cur, t, gap_cap_s)
        if busy is not None:
            return busy
    elif t["role"] == "tool" and t.get("is_error"):
        cur["tool_errors"] += 1
    return min(gap, gap_cap_s) if gap is not None else None


def _open_prompt_task(cur, t, tasks, windows):
    """Rama `user_prompt`: cierra la tarea abierta, abre otra y marca retry
    si el prompt llegó dentro de la ventana tras un fallo. Devuelve la
    tarea nueva."""
    prev = _retry_context(cur)
    _close_task(cur, t["ts"], tasks, windows.max_task_s)
    new = _new_task(t)
    if _is_retry(t, prev, windows.retry_window_s):
        new["retry"] = 1
    return new


def build_tasks(turns, stats=None, **windows_kw):
    """Turnos → tareas. Un `user_prompt` abre tarea; el siguiente la cierra.

    Tiempo ocupado del agente: por turno, `dur_s` si el log lo trae, si no el
    gap desde el evento previo, ambos con tope `gap_cap_s` (un gap mayor es
    inactividad humana o máquina dormida, no trabajo).

    Flags: aborted (stopReason aborted), error (stopReason error), tool_error
    (algún toolResult con isError), retry (el prompt que abrió la tarea llegó
    ≤ retry_window_s después del ÚLTIMO turno de una tarea anterior que
    terminó en fallo y tuvo actividad). `stats` acumula `orphan_turns`
    (turnos previos al primer prompt de una sesión retomada).

    coffe-dnx.3: una tarea no cruza sesiones — si el siguiente turno (prompt
    o respuesta) viene de otra sesión (`t["ses"]`), la tarea abierta se
    cierra y el prompt de la nueva sesión abre otra. Cada tarea suma `cost`
    y `tokens` de sus turnos assistant (`t["cost"]`, `t["usage"]`) y lleva
    `_ses` = sesión de origen, que series/build_report convierten en sid.

    Topes (segundos) por keyword: `gap_cap_s`, `retry_window_s`,
    `max_task_s` (ver TaskWindows).
    """
    windows = TaskWindows(**windows_kw)
    ordered = sorted(turns, key=lambda t: t["ts"])
    tasks = []
    cur = None
    prev_ts = None
    for t in ordered:
        gap = _turn_gap(t, prev_ts)
        if _crosses_session(cur, t):
            # coffe-dnx.3: el gap se considera inactividad, no se suma a la
            # tarea cerrada
            _close_task(cur, t["ts"], tasks, windows.max_task_s)
            cur, prev_ts = None, t["ts"]
            continue
        if t["role"] == "user_prompt":
            cur = _open_prompt_task(cur, t, tasks, windows)
            prev_ts = t["ts"]
            continue
        if cur is None:
            if stats is not None:
                stats["orphan_turns"] = stats.get("orphan_turns", 0) + 1
            prev_ts = t["ts"]
            continue
        busy = _apply_turn(cur, t, gap, windows.gap_cap_s)
        if busy:
            cur["busy_s"] += busy
        cur["_last"] = t["ts"]
        prev_ts = t["ts"]
    if ordered:
        _close_task(cur, ordered[-1]["ts"], tasks, windows.max_task_s)
    return tasks


def task_flags(task):
    bits = 0
    if task.get("aborted"):
        bits |= FLAG_ABORTED
    if task.get("errors"):
        bits |= FLAG_ERROR
    if task.get("tool_errors"):
        bits |= FLAG_TOOL_ERROR
    if task.get("retry"):
        bits |= FLAG_RETRY
    return bits


# ----------------------------------------------------------------------
# Agregaciones puras
# ----------------------------------------------------------------------

def daily_from_tasks(tasks, tz=None):
    """Tareas → métricas por día (día de la TZ del reporte)."""
    daily = {}
    for t in tasks:
        day = task_day(t, tz)
        d = daily.setdefault(day, {"prompts": 0, "tasks": 0, "turns": 0,
                                   "agent_s": 0.0, "aborted": 0, "errors": 0,
                                   "tool_errors": 0, "retry": 0})
        d["prompts"] += 1
        d["tasks"] += 1
        d["turns"] += t["turns"]
        d["agent_s"] += t["busy_s"]
        d["aborted"] += t["aborted"]
        d["errors"] += t["errors"]
        d["tool_errors"] += t["tool_errors"]
        d["retry"] += t["retry"]
    for d in daily.values():
        d["agent_s"] = round(d["agent_s"], 1)
    return daily


def _series_axes(days, sources, models):
    """Índices de día/fuente/modelo para las series compactas."""
    return {"day": {d: i for i, d in enumerate(days)},
            "src": {s: i for i, s in enumerate(sources)},
            "model": {m: i for i, m in enumerate(models)}}


def _series_row(out, t, ix, tz):
    day = task_day(t, tz)
    if day not in ix["day"]:
        return
    out["day"].append(ix["day"][day])
    out["s"].append(int(round(t["busy_s"])))
    out["sp"].append(int(round(t.get("span_s") or 0)))
    out["src"].append(ix["src"].get(t.get("source"), 0))
    out["model"].append(ix["model"].get(t.get("model") or "?", 0))
    out["f"].append(task_flags(t))
    out["sid"].append(t.get("_sid"))
    out["cost"].append(round(float(t.get("cost") or 0.0), 8))
    tok = t.get("tokens") or {}
    out["tokens"].append({k: int(tok.get(k) or 0)
                          for k in ("in", "out", "cache_read",
                                    "cache_write")})


def series(tasks, *axes, tz=None):
    """Series compactas para percentiles exactos por ventana en el viz.

    `day` = índice en `days` (día local), `s` = segundos ocupados (int),
    `sp` = segundos de reloj de la tarea (incluye esperas),
    `src`/`model` = índice, `f` = bitmask de flags.
    coffe-dnx.3: `sid` = índice en `sessions[]` (null si la tarea no conoce
    su sesión), `cost` = coste de los turnos assistant de la tarea, `tokens`
    = {in, out, cache_read, cache_write} acumulados.

    `axes` = (days, sources, models), en ese orden.
    """
    days, sources, models = axes
    ix = _series_axes(days, sources, models)
    out = {"day": [], "s": [], "sp": [], "src": [], "model": [], "f": [],
           "sid": [], "cost": [], "tokens": []}
    for t in tasks:
        _series_row(out, t, ix, tz)
    return out


def _sessions_from_tasks(tasks):
    """coffe-dnx.3: sessions[] = sesiones con al menos una tarea, en orden de
    primera aparición; cada tarea lleva `_sid` = índice en esa lista. Una
    sesión sin tareas (p. ej. un prompt abortado antes de la respuesta) no
    aparece. `None` como session_id se desambigua por identidad del dict."""
    seen = {}
    sessions = []
    for t in tasks:
        ses = t.get("_ses")
        if not ses:
            continue
        key = ("__id__", id(ses)) if ses.get("session_id") is None else (
            ses.get("source"), ses.get("session_id"))
        if key not in seen:
            seen[key] = len(sessions)
            sessions.append({"source": ses.get("source"),
                             "project": ses.get("project"),
                             "session_id": ses.get("session_id")})
        t["_sid"] = seen[key]
    return sessions


# ----------------------------------------------------------------------
# Repos (git local)
# ----------------------------------------------------------------------

def _git(args, cwd):
    try:
        r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                           text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


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


# ----------------------------------------------------------------------
# Índice de beads (coffe-dnx.4) — fuera de repo_metrics(): el panel de repos
# queda git-only; esto escanea .beads/issues.jsonl de cada repo bajo
# agent_behavior.repo_roots (bd corre no-db: el JSONL es la fuente de verdad).
# Agregado por repo × mes, sin texto de issues: ids y conteos solamente.
# ----------------------------------------------------------------------

BEAD_OUT = ROOT / "data" / "bead_index.json"
BEAD_SCHEMA = ROOT / "specs" / "bead-index.schema.json"
TASK_BEADS_OUT = ROOT / "data" / "task_beads.json"
TASK_BEADS_SCHEMA = ROOT / "specs" / "task-beads.schema.json"
BEAD_TEXT_KEYS = {"title", "description", "notes", "design"}
# FPA-141: los mismos tres orgs del filtro charly del reporte de uso
# (usage_tracker.in_scope). Copia local para no acoplar el import: los nombres
# de repo acá son `<org>/<repo>` relativos a la raíz, así que basta el primer
# segmento.
BEAD_ORGS = ("charly", "sk", "ak")


def _bead_in_scope(rel_name):
    """Filtro FPA-141 sobre `<org>/<repo>`: primer segmento = org."""
    org = rel_name.lower().split("/", 1)[0]
    return org in BEAD_ORGS


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


def _month_row(months, key):
    return months.setdefault(key, {"created": 0, "started": 0, "closed": 0,
                                   "lead_time_p50_min": None,
                                   "lead_time_n": 0, "completeness": None,
                                   "reopened": None, "_lead": []})


def _count_bead(row, it):
    """Cuenta un issue en su cohorte: created, started, closed y lead time
    (closed − started, en minutos, solo con ambos)."""
    row["created"] += 1
    started = _dt(it.get("started_at"))
    closed = _dt(it.get("closed_at"))
    if started:
        row["started"] += 1
    if closed:
        row["closed"] += 1
        if started:
            row["lead_time_n"] += 1
            row["_lead"].append((closed - started).total_seconds() / 60.0)


def _fold_lead(row):
    """Cierra la fila del mes: p50 de lead time y completeness."""
    lead = sorted(row.pop("_lead"))
    if lead:
        row["lead_time_p50_min"] = round(_pctl(lead, 0.5), 1)
    row["completeness"] = (round(row["lead_time_n"] / row["closed"], 4)
                           if row["closed"] else None)


def _bead_month_rows(issues):
    """Issues de un repo → filas por mes (mes de `created_at`: la cohorte de
    beads creados ese mes; started/closed/lead_time se cuentan dentro de esa
    cohorte, así created ≤ started, closed es consistente y completeness =
    lead_time_n / closed compara sobre la misma población)."""
    months = {}
    for it in issues:
        if not isinstance(it, dict):
            continue
        created = _dt(it.get("created_at"))
        if created is None:
            continue
        _count_bead(_month_row(months, created.strftime("%Y-%m")), it)
    for row in months.values():
        _fold_lead(row)
    return {k: months[k] for k in sorted(months)}


def bead_privacy_errors(doc):
    """Guard de privacidad (CLRT-003): title/description/notes/design de
    issues son una clase de datos nueva no cubierta por el resto del sitio
    (EDGE-002). Los ids quedan permitidos (opacos). Recorre todo el doc."""
    errors = []

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if k in BEAD_TEXT_KEYS:
                    errors.append(f"clave de texto de issue en {path}.{k}")
                else:
                    walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(doc, "$")
    return errors


def build_bead_index(repo_paths, prefixes=None, now=None):
    """Repos bajo las raíces → doc de data/bead_index.json.

    Per repo: filas por mes {created, started, closed, lead_time_p50_min,
    lead_time_n, completeness, reopened}. reopened queda `null` con razón en
    metadata: el JSONL es una foto de status sin historial de transiciones,
    y un 0 afirmaría un hecho que no se midió (CLRT-001). Repos fuera del
    filtro charly van a `excluded[]` con conteo de issues, sin texto.
    """
    prefixes = prefixes or ["~/para/areas/dev/gh"]
    repos, excluded = [], []
    for path in repo_paths:
        name = _bead_rel_name(path, prefixes)
        jsonl = Path(path) / ".beads" / "issues.jsonl"
        if not jsonl.exists():
            continue
        issues = list(_iter_jsonl(jsonl))
        if not _bead_in_scope(name):
            excluded.append({"name": name, "issues": len(issues)})
            continue
        repos.append({"name": name, "months": _bead_month_rows(issues)})
    repos.sort(key=lambda r: r["name"])
    excluded.sort(key=lambda e: e["name"])
    return {
        "metadata": {
            "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
            "repo_roots": prefixes,
            "definition": (
                "por repo × mes (mes de created_at): created = beads "
                "creados; started/closed = con started_at/closed_at dentro "
                "de la cohorte; lead_time = closed_at − started_at en "
                "minutos para beads con ambos, p50 exacto; completeness = "
                "lead_time_n / closed. Fuente: .beads/issues.jsonl de cada "
                "repo (bd no-db)."),
            "reasons": {
                "reopened": "no event log in no-db mode",
                "excluded": "repos fuera del filtro charly (FPA-141): "
                            "conteo de issues, sin texto",
            },
        },
        "repos": repos,
        "excluded": excluded,
    }


def _bead_schema_errors(doc):
    sys.path.insert(0, str(ROOT / "scripts"))
    from schema_validate import validate_against_schema  # noqa: PLC0415
    return validate_against_schema(
        doc, json.loads(BEAD_SCHEMA.read_text(encoding="utf-8")))


# ----------------------------------------------------------------------
# Atribución tarea↔bead (coffe-dnx.5) — el ledger es beads, no un task
# ledger propio. Resolución: explicit (id en el prompt de la tarea) > git
# (rama o mensaje de commit del repo menciona el id) > temporal (la sesión
# de la tarea arranca dentro de [started_at, closed_at] del bead, mismo
# repo). Confianza: explicit 1.0, git 0.8, temporal 0.5 (techo; la ventana
# queda documentada en cada fila). Las filas temporal NO suman coste.
# ----------------------------------------------------------------------

TASK_BEAD_CONFIDENCE = {"explicit": 1.0, "git": 0.8, "temporal": 0.5}
# tokens con guión (coffe-abc, coffe-dnx.5); case-insensitive. Sin NLP: el
# matcheo es literal y solo atribuye si el token existe en el índice de
# beads (by_id), así que el patrón amplio no inventa atribuciones.
BEAD_ID_RE = re.compile(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+)+(?:\.\d+)?)\b",
                        re.IGNORECASE)


def load_bead_records(repo_paths, prefixes=None):
    """Repos → registros de bead {id, repo, path, started_at, closed_at}
    desde .beads/issues.jsonl (misma fuente y mismo filtro de scope que
    build_bead_index). started_at/closed_at parseados a datetime UTC."""
    beads = []
    for path in repo_paths:
        name = _bead_rel_name(path, prefixes)
        jsonl = Path(path) / ".beads" / "issues.jsonl"
        if not jsonl.exists() or not _bead_in_scope(name):
            continue
        for it in _iter_jsonl(jsonl):
            if not isinstance(it, dict) or not it.get("id"):
                continue
            beads.append({"id": str(it["id"]), "repo": name, "path": Path(path),
                          "started_at": _dt(it.get("started_at")),
                          "closed_at": _dt(it.get("closed_at"))})
    return beads


def _git_mentioned_beads(repo_path, run=_git):
    """Ids de beads mencionados en el git del repo: subjects de commits
    (--all, último año) y nombres de rama. Un solo par de llamadas por repo."""
    ids = set()
    for args in (["log", "--all", "--since=1.year", "--format=%s"],
                 ["branch", "--all", "--format=%(refname:short)"]):
        out = run(args, repo_path)
        for line in (out or "").splitlines():
            for m in BEAD_ID_RE.finditer(line):
                ids.add(m.group(1).lower())
    return ids


def _session_repo(session, beads):
    """Repo de la sesión: proyecto (última componente del cwd) matcheado
    contra `<org>/<repo>` de los beads. None si no hay match único."""
    project = (session or {}).get("project")
    if not project:
        return None
    repos = {b["repo"] for b in beads
             if b["repo"] == project or b["repo"].endswith("/" + project)}
    return next(iter(repos)) if len(repos) == 1 else None


def _explicit_beads(t, by_id):
    """explicit: ids del prompt presentes en el índice de beads."""
    found = []
    for m in BEAD_ID_RE.finditer(t.get("prompt") or ""):
        b = by_id.get(m.group(1).lower())
        if b:
            found.append((b, "explicit"))
    return found


def _git_bead_ids(beads, repo, git_cache, run):
    """Ids de beads que el git del repo menciona, con caché por repo."""
    if repo not in git_cache:
        git_cache[repo] = _git_mentioned_beads(
            next(b["path"] for b in beads if b["repo"] == repo), run=run)
    return git_cache[repo]


def _in_bead_window(b, start, end):
    """La ventana acota qué tareas del repo toca el bead: sin ella, cada
    tarea heredaría todos los ids del git (ruido)."""
    if not b["started_at"] or b["started_at"] > end:
        return False
    return not b["closed_at"] or start <= b["closed_at"]


def _git_beads(t, beads, repo, git_ids):
    """git: el git del repo de la sesión menciona el id (rama o commit) y la
    tarea cae dentro de la ventana del bead."""
    found = []
    start = t["_start"]
    end = start + timedelta(seconds=t.get("span_s") or 0.0)
    for b in beads:
        if b["repo"] != repo or b["id"].lower() not in git_ids:
            continue
        if _in_bead_window(b, start, end):
            found.append((b, "git"))
    return found


def _temporal_beads(t, beads, repo, ses):
    """temporal: sin id, mismo repo, y el inicio de la tarea cae dentro de
    [started_at, closed_at] del bead (closed_at null = abierto)."""
    found = []
    if not repo or not ses:
        return found
    start = t["_start"]
    for b in beads:
        if b["repo"] != repo or not b["started_at"] or b["started_at"] > start:
            continue
        if b["closed_at"] and start > b["closed_at"]:
            continue
        found.append((b, "temporal"))
    return found


def _dedupe_beads(found, i, t):
    """Una fila por (id, método); la temporal declara su ventana."""
    seen = {}
    for b, method in found:
        seen.setdefault((b["id"], method), (b, method))
    rows = []
    for (_bid, method), (b, _) in seen.items():
        row = {"task": i, "session": t.get("_sid"), "bead": b["id"],
               "method": method, "confidence": TASK_BEAD_CONFIDENCE[method]}
        if method == "temporal":
            row["window"] = {"started_at": b["started_at"].isoformat(),
                             "closed_at": (b["closed_at"].isoformat()
                                           if b["closed_at"] else None)}
        rows.append(row)
    return rows


def attribute_task_beads(tasks, beads, run=_git):
    """Tareas × beads → filas de atribución [{task, session, bead, method,
    confidence, ...}] en orden de resolución explicit > git > temporal.

    - explicit: el prompt de la tarea contiene el id del bead (conf 1.0).
    - git: el git del repo de la sesión menciona el id (rama o commit) y la
      tarea cae dentro de la ventana del bead (conf 0.8).
    - temporal: sin id, mismo repo, y el inicio de la tarea cae dentro de
      [started_at, closed_at] del bead (closed_at null = abierto); conf 0.5
      y ventana declarada en la fila.
    """
    by_id = {b["id"].lower(): b for b in beads}
    git_cache = {}
    rows = []
    sessions = _sessions_from_tasks(tasks)
    for i, t in enumerate(tasks):
        ses = sessions[t["_sid"]] if t.get("_sid") is not None else None
        repo = _session_repo(ses, beads)
        found = _explicit_beads(t, by_id)
        if not found and repo:
            found = _git_beads(t, beads, repo,
                               _git_bead_ids(beads, repo, git_cache, run))
        if not found:
            found = _temporal_beads(t, beads, repo, ses)
        rows.extend(_dedupe_beads(found, i, t))
    return rows, sessions


SESSION_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _spend_index(session_spend):
    """{session_id: fila} y {uuid embutido: fila} del reporte."""
    spend, spend_uuid = {}, {}
    for r in session_spend or []:
        if not (isinstance(r, dict) and r.get("session_id")):
            continue
        spend.setdefault(r["session_id"], r)
        # pi: el tracker usa el uuid desnudo; el reporte lo trae embutido
        # en el nombre de archivo de la sesión (`..._uuid`)
        for m in SESSION_UUID_RE.finditer(str(r["session_id"])):
            spend_uuid.setdefault(m.group(0), r)
    return spend, spend_uuid


def _session_cost(sid, sessions, spend_maps):
    """c(s) = session_spend[s].cost_effective (0.0 si no hay fila o es null)."""
    s = sessions[sid] if isinstance(sid, int) and 0 <= sid < len(sessions) else {}
    sid = s.get("session_id")
    row = next((m[sid] for m in spend_maps if sid in m), None)
    c = (row or {}).get("cost_effective")
    return float(c) if isinstance(c, (int, float)) else 0.0


def _split_bead_costs(rows, sessions, spend_maps):
    """s_beads = sid → beads con coste (explicit/git); s_temporal = sid →
    beads temporales (solo cuentan); bead_cost = coste repartido por bead."""
    s_beads = defaultdict(set)
    s_temporal = defaultdict(set)
    for row in rows:
        if row["method"] in ("explicit", "git"):
            s_beads[row["session"]].add(row["bead"])
        else:
            s_temporal[row["session"]].add(row["bead"])
    cost = {sid: _session_cost(sid, sessions, spend_maps)
            for sid in set(s_beads) | set(s_temporal)}
    bead_cost = defaultdict(float)
    for sid, blist in s_beads.items():
        share = cost[sid] / len(blist) if blist else 0.0
        for b in blist:
            bead_cost[b] += share
    return s_beads, s_temporal, bead_cost


def _mark_splits(rows, s_beads):
    """Sesión en k > 1 beads con coste: split: true en las filas afectadas."""
    for row in rows:
        k = len(s_beads.get(row["session"]) or ())
        if row["method"] in ("explicit", "git") and k > 1:
            row["split"] = True


def _per_bead(rows, s_beads, s_temporal, bead_cost):
    per_bead = {}
    for b in {r["bead"] for r in rows}:
        per_bead[b] = {
            "cost_per_bead": round(bead_cost.get(b, 0.0), 8),
            "n_sessions": sum(1 for s in s_beads if b in s_beads[s]),
            "n_temporal": sum(1 for s in s_temporal if b in s_temporal[s]),
        }
    return per_bead


def compute_bead_costs(rows, sessions, session_spend):
    """Fórmula CLRT-005. S(b) = sesiones atribuidas a b con method ∈
    {explicit, git} (temporal solo cuenta); c(s) = session_spend[s]
    .cost_effective (0.0 si no hay fila o es null). Sesión en k beads:
    c(s)/k con split: true en las filas afectadas. Devuelve
    {bead: {cost_per_bead, n_sessions, n_temporal}} y el mapa sesión→beads
    con coste."""
    spend_maps = _spend_index(session_spend)
    s_beads, s_temporal, bead_cost = _split_bead_costs(rows, sessions, spend_maps)
    _mark_splits(rows, s_beads)
    return _per_bead(rows, s_beads, s_temporal, bead_cost), s_beads


def build_task_beads_doc(tasks, repo_paths, **opts):
    """Dataset data/task_beads.json: filas de atribución + coste por bead.

    Opciones por keyword: `prefixes`, `session_spend`, `run`, `now`."""
    prefixes = opts.get("prefixes")
    session_spend = opts.get("session_spend")
    run = opts.get("run", _git)
    now = opts.get("now")
    beads = load_bead_records(repo_paths, prefixes)
    rows, sessions = attribute_task_beads(tasks, beads, run=run)
    per_bead, s_beads = compute_bead_costs(rows, sessions, session_spend)
    with_cost = sorted(v["cost_per_bead"] for v in per_bead.values()
                       if v["n_sessions"] >= 1)
    return {
        "metadata": {
            "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
            "repo_roots": prefixes or ["~/para/areas/dev/gh"],
            "confidence": dict(TASK_BEAD_CONFIDENCE),
            "n_beads": len(beads),
            "resolution": (
                "explicit (id en el prompt de la tarea) > git (rama o commit "
                "del repo menciona el id, tarea dentro de la ventana del "
                "bead) > temporal (inicio de la tarea dentro de "
                "[started_at, closed_at] del bead, closed_at null = abierto, "
                "mismo repo)."),
            "temporal_window": (
                "[started_at, closed_at] del bead; techo de confianza 0.5 y "
                "ventana declarada en cada fila temporal"),
            "formula": (
                "S(b) = sesiones atribuidas a b con method ∈ {explicit, "
                "git}; cost_per_bead(b) = Σ c(s) con c(s) = "
                "session_spend[s].cost_effective; las filas temporal NO "
                "suman coste (solo n_temporal). Sesión en k beads: c(s)/k "
                "con split: true. cost_per_bead_p50 = p50 sobre beads con "
                "|S(b)| >= 1; n_beads_sin_coste = beads con S(b) = ∅."),
            "task_index": (
                "índice en la lista de tareas del tracker (mismo orden que "
                "agent_metrics tasks); session = índice en sessions[]"),
        },
        "task_beads": rows,
        "beads": [{"bead": k, **v} for k, v in sorted(per_bead.items())],
        "cost_per_bead_p50": round(_pctl(with_cost, 0.5), 8) if with_cost else None,
        "n_beads_sin_coste": len(beads) - len(with_cost),
    }


def _task_beads_schema_errors(doc):
    sys.path.insert(0, str(ROOT / "scripts"))
    from schema_validate import validate_against_schema  # noqa: PLC0415
    return validate_against_schema(
        doc, json.loads(TASK_BEADS_SCHEMA.read_text(encoding="utf-8")))


# ----------------------------------------------------------------------
# Ensamblado
# ----------------------------------------------------------------------

def _report_metadata(cfg, coverage, report_meta, days):
    """Bloque metadata del dataset del dashboard."""
    return {
        "date_range": {"start": report_meta["date_range"]["start"],
                       "end": report_meta["date_range"]["end"]},
        "timezone": report_meta.get("timezone"),
        "first_task": days[0] if days else None,
        "last_task": days[-1] if days else None,
        "task_definition": (
            "una tarea = un prompt de usuario y los turnos que dispara "
            "hasta el prompt siguiente; el tiempo del agente es la suma "
            "de los turnos (durationMs reportado, o gap desde el evento "
            "previo con tope gap_cap). coffe-dnx.3: una tarea no cruza "
            "sesiones — si el prompt siguiente abre otra sesión, es otra "
            "tarea; sid indexa sessions[] = {source, project, "
            "session_id}, y cost/tokens suman los turnos assistant de "
            "la tarea."),
        "gap_cap_minutes": cfg["gap_cap_minutes"],
        "retry_window_minutes": cfg["retry_window_minutes"],
        "config_source": cfg.get("config_source"),
        "provenance": {"tasks": "derived", "aborts": "reported",
                       "tool_errors": "reported", "repos": "reported",
                       "cost": "reported", "sessions": "derived"},
        "coverage": coverage,
        "reasons": {
            "abort_signal": "solo pi registra stopReason aborted/error; "
                            "claude-cli no escribe esa señal",
            "fork_dedupe": "sesiones pi con el mismo id = fork: se toma "
                           "el archivo más largo",
        },
    }


def build_report(tasks, cfg, **opts):
    """Tareas de todas las fuentes → dataset del dashboard.

    Opciones por keyword: `coverage`, `repos`, `report_meta`, `tz`."""
    coverage = opts["coverage"]
    repos = opts["repos"]
    report_meta = opts["report_meta"]
    tz = opts.get("tz")
    days = sorted({task_day(t, tz) for t in tasks})
    sources = sorted({t["source"] for t in tasks}) or ["pi"]
    models = sorted({t["model"] for t in tasks if t.get("model")}) or ["?"]
    sessions = _sessions_from_tasks(tasks)
    return {
        "metadata": _report_metadata(cfg, coverage, report_meta, days),
        "days": days,
        "sources": sources,
        "models": models,
        "daily": daily_from_tasks(tasks, tz),
        "tasks": series(tasks, days, sources, models, tz=tz),
        "sessions": sessions,
        "repos": repos,
        "months": sorted({t["_start"].strftime("%Y-%m") for t in tasks}),
    }


def summarize_coverage(pi_stats, claude_stats):
    # coffe-dnx.3: el gate de paridad de coste está acotado — session_spend
    # incluye fuentes (amp, omp, codex, ...) que agent_metrics no procesa;
    # el alcance declarado queda en cada fila con `cost_parity`.
    return [
        {"source": "pi", "turn_level": True, "abort_signal": True,
         "sessions": pi_stats.get("sessions", 0),
         "cost_parity": "sum(tasks.cost) por modelo == suma de las filas "
                        "session_spend de pi (sesiones no-fork)",
         "note": pi_stats.get("skipped_reason") or
                 "solo sesiones on-disk (pi rota las viejas; arranca 2026-04)"},
        {"source": "claude-cli", "turn_level": True, "abort_signal": False,
         "sessions": claude_stats.get("sessions", 0),
         "cost_parity": "sum(tasks.cost) por modelo == suma de las filas "
                        "session_spend de claude-cli (sesiones no-fork)",
         "note": claude_stats.get("skipped_reason") or (
             f"preámbulos de reanudación excluidos: "
             f"{claude_stats.get('preambles_skipped', 0)}")},
        {"source": "amp", "turn_level": False, "abort_signal": False,
         "sessions": None,
         "note": "el log de amp no expone turnos con timestamp ni stopReason"},
        {"source": "omp", "turn_level": False, "abort_signal": False,
         "sessions": None,
         "note": "el log de omp no expone turnos con timestamp por mensaje"},
        {"source": "codex", "turn_level": False, "abort_signal": False,
         "sessions": None,
         "note": "el rollout no se camina acá (una tarea por rollout, sin "
                 "frontera de prompt de usuario)"},
    ]


def _schema_errors(doc):
    sys.path.insert(0, str(ROOT / "scripts"))
    from schema_validate import validate_against_schema  # noqa: PLC0415
    return validate_against_schema(doc, json.loads(SCHEMA.read_text(encoding="utf-8")))


def _report_errors(errors):
    for e in errors[:20]:
        print(f"schema: {e}", file=sys.stderr)
    return 1


def _argv_value(argv, flag):
    """Valor de una opción `flag valor` en argv (IndexError si no lo trae)."""
    return argv[argv.index(flag) + 1]


def _validate_only_mode(argv):
    """Modos --validate-only / --validate-bead / --validate-task-beads:
    validan un dataset ya escrito sin re-extraer logs. None = seguir."""
    if "--validate-only" in argv:
        # valida un dataset ya escrito (los tests usan esto para el caso
        # negativo del spec, sin re-extraer logs)
        target = Path(_argv_value(argv, "--validate-only"))
        return _report_errors(_schema_errors(json.loads(target.read_text(encoding="utf-8"))))
    if "--validate-bead" in argv:
        # coffe-dnx.4: valida data/bead_index.json contra su schema + guard
        # de privacidad (sin re-escanear repos)
        doc = json.loads(Path(_argv_value(argv, "--validate-bead")).read_text(encoding="utf-8"))
        errors = _bead_schema_errors(doc) + bead_privacy_errors(doc)
        return _report_errors(errors) if errors else 0
    if "--validate-task-beads" in argv:
        # coffe-dnx.5: valida data/task_beads.json contra su schema
        target = Path(_argv_value(argv, "--validate-task-beads"))
        errors = _task_beads_schema_errors(json.loads(target.read_text(encoding="utf-8")))
        return _report_errors(errors) if errors else 0
    return None


def _build_source_tasks(cfg):
    """Turnos de todas las fuentes → tareas etiquetadas, coverage y stats."""
    pi_turns, pi_stats = load_pi_turns()
    cl_turns, cl_stats = load_claude_turns()
    coverage = summarize_coverage(pi_stats, cl_stats)
    task_stats = {}
    tasks = []
    for turns, source in ((pi_turns, "pi"), (cl_turns, "claude-cli")):
        built = build_tasks(turns, gap_cap_s=cfg["gap_cap_minutes"] * 60,
                            retry_window_s=cfg["retry_window_minutes"] * 60,
                            max_task_s=cfg["max_task_minutes"] * 60,
                            stats=task_stats)
        for t in built:
            t["source"] = source
        tasks.extend(built)
    return tasks, coverage, task_stats, cl_stats


def _write_bead_index(bead):
    """coffe-dnx.4: bead_index desde .beads/issues.jsonl de los repos.
    Schema drift no aborta (mismo criterio del nightly); un error de
    privacidad sí impide escribir el artefacto."""
    for e in _bead_schema_errors(bead)[:10]:
        print(f"bead_index schema: {e}", file=sys.stderr)
    bead_priv = bead_privacy_errors(bead)
    if bead_priv:
        for e in bead_priv[:10]:
            print(f"bead_index privacy: {e}", file=sys.stderr)
        return
    BEAD_OUT.parent.mkdir(parents=True, exist_ok=True)
    BEAD_OUT.write_text(json.dumps(bead, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")


def _session_spend_rows():
    """session_spend del reporte; [] si no se puede leer."""
    try:
        return json.loads(REPORT.read_text(encoding="utf-8")).get("session_spend")
    except (OSError, json.JSONDecodeError):
        return []


def _write_task_beads(tasks, repo_paths, cfg):
    """coffe-dnx.5: atribución tarea↔bead + coste por bead. Schema drift no
    aborta (mismo criterio que bead_index)."""
    tb = build_task_beads_doc(tasks, repo_paths, prefixes=cfg["repo_roots"],
                              session_spend=_session_spend_rows())
    errors = _task_beads_schema_errors(tb)
    for e in errors[:10]:
        print(f"task_beads schema: {e}", file=sys.stderr)
    if errors:
        return
    TASK_BEADS_OUT.parent.mkdir(parents=True, exist_ok=True)
    TASK_BEADS_OUT.write_text(json.dumps(tb, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")


def _finalize_metadata(merged, cfg, stats, tz):
    """Metadata derivada del run: config de repos, stats y razones.
    `stats` = {"task": task_stats, "claude": cl_stats,
               "missing_roots": raíces inexistentes}."""
    m = merged["metadata"]
    missing_roots = stats["missing_roots"]
    m["repo_roots"] = cfg["repo_roots"]
    m["repo_roots_missing"] = missing_roots
    m["min_commit_days"] = cfg["min_commit_days"]
    m["min_returning_months"] = cfg["min_returning_months"]
    m["bulk_day_interactions"] = cfg["bulk_day_interactions"]
    m["orphan_turns"] = stats["task"].get("orphan_turns", 0)
    m["preamble_prompts_skipped"] = stats["claude"].get("preambles_skipped", 0)
    if missing_roots:
        m["reasons"]["repo_roots_missing"] = (
            "raíces de repos configuradas que no existen: "
            + ", ".join(missing_roots))
    if tz is None:
        m["reasons"]["timezone"] = (
            "el reporte no declara timezone: días atribuidos en UTC")


def _print_summary(merged, out_path, missing_roots):
    m = merged["metadata"]
    print(f"tareas: {len(merged['tasks']['s'])} · días: {len(merged['days'])} "
          f"· fuentes: {', '.join(merged['sources'])} · modelos: {len(merged['models'])}")
    print(f"rango: {m['first_task']} → {m['last_task']} · repos: {len(merged['repos'])}")
    if missing_roots:
        print(f"aviso: raíces inexistentes: {', '.join(missing_roots)}", file=sys.stderr)
    print(f"escrito: {out_path.relative_to(ROOT) if out_path.is_absolute() else out_path}")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out_path = Path(_argv_value(argv, "--output")) if "--output" in argv else OUT
    validated = _validate_only_mode(argv)
    if validated is not None:
        return validated

    cfg = load_config()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    meta = report["metadata"]
    tz = tz_from_offset(meta.get("timezone"))

    tasks, coverage, task_stats, cl_stats = _build_source_tasks(cfg)
    repo_paths, missing_roots = find_git_repos(cfg["repo_roots"],
                                               cfg["repo_max_depth"])
    repos = repo_metrics(repo_paths, prefixes=cfg["repo_roots"])
    _write_bead_index(build_bead_index(repo_paths, prefixes=cfg["repo_roots"]))
    merged = build_report(tasks, cfg, coverage=coverage, repos=repos,
                          report_meta=meta, tz=tz)
    _write_task_beads(tasks, repo_paths, cfg)
    _finalize_metadata(merged, cfg, {"task": task_stats, "claude": cl_stats,
                                     "missing_roots": missing_roots}, tz)

    # validar ANTES de escribir: un dataset inválido no debe quedar en disco
    if "--validate" in argv:
        errors = _schema_errors(merged)
        if errors:
            return _report_errors(errors)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    _print_summary(merged, out_path, missing_roots)
    return 0


if __name__ == "__main__":
    sys.exit(main())
