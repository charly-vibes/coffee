"""Módulo extraído de agent-metrics-tracker.py (decomposición, comportamiento idéntico).

Tareas (funciones puras): turnos → tareas, con flags, tiempo ocupado y
sesión de origen.
"""
from __future__ import annotations

from dataclasses import dataclass


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
        bits |= 1
    if task.get("errors"):
        bits |= 2
    if task.get("tool_errors"):
        bits |= 4
    if task.get("retry"):
        bits |= 8
    return bits
