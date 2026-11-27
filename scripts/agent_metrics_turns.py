"""Módulo extraído de agent-metrics-tracker.py (decomposición, comportamiento idéntico).

Turnos por fuente (pi y claude-cli): registros JSONL → turnos con rol,
modelo, stopReason, duración, usage y coste; loaders con dedupe de forks.
coffe-t6e: el turno assistant de pi además viaja con `error_message`
(errorMessage crudo del registro) para las firmas de cuota de ops.quota_events.
"""
from __future__ import annotations

from pathlib import Path

from agent_metrics_common import (
    CLAUDE_DIR, PI_DIR, _claude_tool_error, _cost_from, _dt, _is_preamble,
    _is_tool_result, _iter_jsonl, _prompt_text, _session_from_records,
    _usage_tokens,
)

# ----------------------------------------------------------------------
# Turnos por fuente
# ----------------------------------------------------------------------

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
        "error_message": msg.get("errorMessage"),
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
