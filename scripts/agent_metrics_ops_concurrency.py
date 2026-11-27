"""Purpose: slices coffe-qi7 del bloque `ops` (epic coffe-a5v) para
data/agent_metrics.json — saturación (concurrency), stall y queue_wait.

Responsibilities: calcular por día el máximo de requests en vuelo (turnos
assistant que se solapan en el tiempo) y de agentes simultáneos (sesiones
distintas solapadas), con p95 a través de los días; separar los minutos idle
entre turnos assistant consecutivos de una sesión en idle puro vs. adyacente
a error/aborto (tope gap_cap del metadata); y reportar queue_wait como null
honesto con razón (los logs locales no registran TTFT).

Rationale: la saturación se define por solapamiento real de turnos, no por
agentes configurados; los días sin señal de duración se excluyen del daily y
quedan declarados con razón (`no_overlap_data`), nunca como 0. Para
error-adyacente se usa la definición por turnos (`stop` error/aborted o
toolResult con isError de la misma sesión entre los dos turnos assistant),
equivalente a los bits 1/2 de `f` en tasks: el flag de tarea es exactamente
esos stops, pero el turno se atribuye al día local sin depender del join
sid→sessions del dataset. queue_wait es constante porque nada en los logs
locales mide tiempo en cola.

Corrida: `python3 scripts/agent_metrics_ops_concurrency.py` después del
tracker (los loaders recorren ~/.pi y ~/.claude, ~30s).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_metrics_common import tz_from_offset  # noqa: E402
from agent_metrics_ops import run_slice  # noqa: E402
from agent_metrics_turns import load_claude_turns, load_pi_turns  # noqa: E402

NO_OVERLAP_REASON = "no_overlap_data"
QUEUE_WAIT_BLOCK = {"value": None, "reason": "no_ttft_in_local_logs",
                    "provenance": {"queue_wait": "assumed"}}
def queue_wait_block():
    """ops.queue_wait: TTFT no medible en logs locales → null con razón."""
    return dict(QUEUE_WAIT_BLOCK)


DEFAULT_GAP_CAP_MINUTES = 30.0
_ERROR_STOPS = frozenset({"error", "aborted"})


# ----------------------------------------------------------------------
# Utilidades puras
# ----------------------------------------------------------------------

def _assistant_turns(turns):
    return [t for t in turns if t.get("role") == "assistant" and t.get("ts") is not None]


def _local_day(ts, tz):
    """Día local (YYYY-MM-DD) de un datetime aware, con la TZ del metadata."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=None)
        return ts.date().isoformat()
    return ts.astimezone(tz).date().isoformat() if tz else ts.date().isoformat()


def _session_id(turn):
    ses = turn.get("ses") or {}
    return str(ses.get("session_id") or id(ses))


def _turn_source(turn):
    return (turn.get("ses") or {}).get("source") or "unknown"


def _interval_depth(intervals):
    """Máximo de solapamiento y máximas sesiones distintas en vuelo.

    intervals: [(start, end, session_id)] con end > start (dur 0 cuenta como
    un instante: [start, start] se solapa consigo mismo → profundidad 1).
    Devuelve (max_depth, max_distinct_agents) muestreando en cada start.
    """
    if not intervals:
        return 0, 0
    events = []
    for start, end, sid in intervals:
        events.append((start, 1, sid))
        events.append((end, -1, sid))
    events.sort(key=lambda e: (e[0], -e[1]))  # opens antes que closes en el mismo instante
    active = {}
    max_depth = max_agents = 0
    for _t, delta, sid in events:
        if delta > 0:
            active[sid] = active.get(sid, 0) + 1
        else:
            active[sid] -= 1
            if active[sid] <= 0:
                active.pop(sid)
        depth = sum(active.values())
        max_depth = max(max_depth, depth)
        max_agents = max(max_agents, len(active))
    return max_depth, max_agents


def concurrency_block(pi_turns, claude_turns, doc):
    """ops.concurrency: p95 diario de requests en vuelo y agentes simultáneos.

    Los días cuyos turnos no tienen duración en absoluto se excluyen del
    `daily` (el schema exige `agents_p95` numérico) y quedan declarados con
    `reason: no_overlap_data` a nivel del bloque, nunca como 0.
    """
    tz = tz_from_offset((doc.get("metadata") or {}).get("timezone"))
    turns = _assistant_turns(pi_turns) + _assistant_turns(claude_turns)

    by_day: dict[str, list] = {}
    days_without_dur: set[str] = set()
    coverage: dict[str, list[int]] = {}
    for t in turns:
        source = _turn_source(t)
        cov = coverage.setdefault(source, [0, 0])
        cov[0] += 1
        day = _local_day(t["ts"], tz)
        dur = t.get("dur_s")
        if isinstance(dur, (int, float)) and dur >= 0:
            cov[1] += 1
            end = t["ts"] + timedelta(seconds=max(float(dur), 0.0))
            by_day.setdefault(day, []).append((t["ts"], end, _session_id(t)))
        else:
            days_without_dur.add(day)

    daily = []
    day_maxes = []
    for day in sorted(by_day):
        depth, agents = _interval_depth(by_day[day])
        daily.append({"day": day, "agents_p95": float(agents),
                      "requests_p95": float(depth)})
        day_maxes.append((agents, depth))

    block = {
        "daily": daily,
        "agents_p95": None, "requests_p95": None,
        "provenance": {"concurrency": "derived"},
        "coverage": [{"source": s, "coverage_pct": round(100.0 * ok / total, 1) if total else 0.0,
                      "reason": NO_OVERLAP_REASON if total and not ok else None}
                     for s, (total, ok) in sorted(coverage.items())],
    }
    if day_maxes:
        from agent_metrics_ops import percentile  # noqa: PLC0415 — evitar ciclo en tests
        block["agents_p95"] = percentile(sorted(a for a, _ in day_maxes), 0.95)
        block["requests_p95"] = percentile(sorted(r for _, r in day_maxes), 0.95)
    if not by_day:
        block["reason"] = NO_OVERLAP_REASON
    if days_without_dur - set(by_day):
        block["reason"] = NO_OVERLAP_REASON
    return block


# ----------------------------------------------------------------------
# ops.stall
# ----------------------------------------------------------------------

def _error_adjacent_split(session_turns):
    """Por sesión: minutos idle vs. error-adyacentes entre turnos assistant
    consecutivos. Lineal: una pasada sobre los turnos ordenados marcando si
    hubo señal de error (assistant stop error/aborted, o tool isError —
    mismos bits 1/2 de task f) desde el turno assistant anterior.

    Devuelve [(prev_ts, next_ts, error_adjacent)] en orden."""
    marked = sorted(
        (t["ts"], ("tool_error" if t.get("role") == "tool"
                   else ("error" if t.get("stop") in _ERROR_STOPS else "assistant")))
        for t in session_turns if t.get("ts") is not None
        and t.get("role") in ("assistant", "tool"))
    out = []
    saw_error = False
    prev_assistant = None
    for ts, kind in marked:
        if kind == "tool_error":
            saw_error = True
            continue
        if prev_assistant is not None and ts > prev_assistant:
            out.append((prev_assistant, ts, saw_error))
        prev_assistant, saw_error = ts, kind == "error"  # el propio stop cuenta
    return out


def stall_block(pi_turns, claude_turns, doc):
    """ops.stall: minutos idle entre turnos assistant consecutivos de una
    sesión por día, con tope gap_cap (metadata, default 30 min); los gaps
    con señal de error de la misma sesión en el medio van a
    minutes_error_adjacent."""
    gap_cap_s = 60.0 * float(((doc.get("metadata") or {}).get("gap_cap_minutes"))
                             or DEFAULT_GAP_CAP_MINUTES)
    tz = tz_from_offset((doc.get("metadata") or {}).get("timezone"))
    all_turns = list(pi_turns) + list(claude_turns)
    turns = _assistant_turns(all_turns)

    by_session: dict[str, list] = {}
    for t in turns:
        by_session.setdefault(_session_id(t), []).append(t)
    for t in all_turns:  # tool errors entran a la pasada por sesión
        if t.get("role") == "tool" and t.get("is_error") and t.get("ts") is not None:
            by_session.setdefault(_session_id(t), []).append(t)

    idle_min: dict[str, float] = {}
    error_min: dict[str, float] = {}
    for session_turns in by_session.values():
        for prev, nxt, is_error in _error_adjacent_split(session_turns):
            gap_s = (nxt - prev).total_seconds()
            if gap_s <= 0:
                continue
            day = _local_day(nxt, tz)
            capped = min(gap_s, gap_cap_s) / 60.0
            idle_min[day] = idle_min.get(day, 0.0) + capped
            if is_error:
                error_min[day] = error_min.get(day, 0.0) + capped

    days = sorted(set(idle_min) | set(error_min))
    daily = [{"day": d, "minutes_error_adjacent": round(error_min.get(d, 0.0), 3),
              "minutes_idle": round(idle_min.get(d, 0.0) - error_min.get(d, 0.0), 3)}
             for d in days]

    coverage: dict[str, list[int]] = {}
    for t in turns:
        cov = coverage.setdefault(_turn_source(t), [0, 0])
        cov[0] += 1
        cov[1] += 1  # los turnos ya llegan con ts parseado (loader filtra)
    totals = {"minutes_error_adjacent": round(sum(error_min.values()), 3),
              "minutes_idle": round(sum(v - error_min.get(k, 0.0) for k, v in idle_min.items()), 3)}
    return {"daily": daily, "totals": totals,
            "provenance": {"stall": "derived"},
            "coverage": [{"source": s, "coverage_pct": round(100.0 * ok / total, 1) if total else 0.0,
                          "reason": None} for s, (total, ok) in sorted(coverage.items())]}


# ----------------------------------------------------------------------
# Computos (contrato run_slice)
# ----------------------------------------------------------------------

def compute_concurrency(inputs):
    return concurrency_block(inputs["pi_turns"], inputs["claude_turns"], inputs["doc"])


def compute_stall(inputs):
    return stall_block(inputs["pi_turns"], inputs["claude_turns"], inputs["doc"])


def compute_queue_wait(_inputs):
    return dict(QUEUE_WAIT_BLOCK)


# ----------------------------------------------------------------------
# Render server-side (viz-agent.py)
# ----------------------------------------------------------------------

def _fmt_num(v, unit=""):
    if v is None:
        return "—"
    s = f"{float(v):,.1f}".replace(",", "@").replace(".", ",").replace("@", ".")
    return s + unit


def render_section(ops):
    """Dos secciones: saturación y stall+cola. n/a con razón si falta la clave."""
    parts = []
    conc = (ops or {}).get("concurrency")
    if conc:
        rows = "\n".join(
            f"<tr><td>{d['day']}</td><td>{_fmt_num(d.get('agents_p95'))}</td>"
            f"<td>{_fmt_num(d.get('requests_p95'))}</td></tr>"
            for d in conc.get("daily") or [])
        reason = conc.get("reason")
        parts.append(
            '<section class="block"><h2>Saturación: requests en vuelo y agentes simultáneos (ops.concurrency)</h2>'
            f'<p>p95 a través de los días — agentes: <strong>{_fmt_num(conc.get("agents_p95"))}</strong>, '
            f'requests en vuelo: <strong>{_fmt_num(conc.get("requests_p95"))}</strong>.</p>'
            + (f'<p class="reason">{reason}</p>' if reason else "")
            + ('<table><thead><tr><th>Día</th><th>Agentes p95</th><th>Requests en vuelo p95</th></tr></thead><tbody>'
               f"\n{rows}\n    </tbody></table>" if rows else "")
            + '<p class="reason">Solapamiento real de turnos assistant (no agentes configurados); '
              'días sin señal de duración se excluyen con razón, no cuentan como 0.</p></section>')
    else:
        parts.append('<section class="block"><h2>Saturación (ops.concurrency)</h2>'
                     '<p class="reason">n/a: ops.concurrency ausente (epic coffe-a5v).</p></section>')

    stall = (ops or {}).get("stall")
    qwait = (ops or {}).get("queue_wait")
    if stall:
        totals = stall.get("totals") or {}
        rows = "\n".join(
            f"<tr><td>{d['day']}</td><td>{_fmt_num(d.get('minutes_error_adjacent'), ' min')}</td>"
            f"<td>{_fmt_num(d.get('minutes_idle'), ' min')}</td></tr>"
            for d in stall.get("daily") or [])
        parts.append(
            '<section class="block"><h2>Stall: minutos idle y adyacentes a error (ops.stall)</h2>'
            f'<p>Totales — idle: <strong>{_fmt_num(totals.get("minutes_idle"), " min")}</strong>, '
            f'adyacente a error/aborto: <strong>{_fmt_num(totals.get("minutes_error_adjacent"), " min")}</strong>.</p>'
            + ('<table><thead><tr><th>Día</th><th>Min. adyacentes a error</th><th>Min. idle</th></tr></thead><tbody>'
               f"\n{rows}\n    </tbody></table>" if rows else "")
            + '<p class="reason">Gaps entre turnos assistant de la misma sesión, con tope gap_cap; '
              'error-adyacente usa stop error/aborted o toolResult con isError de la sesión (coffe-qi7).</p></section>')
    else:
        parts.append('<section class="block"><h2>Stall (ops.stall)</h2>'
                     '<p class="reason">n/a: ops.stall ausente (epic coffe-a5v).</p></section>')

    if qwait:
        parts.append('<section class="block"><h2>Espera en cola (ops.queue_wait)</h2>'
                     f'<p class="reason">n/a con razón: {qwait.get("reason")} '
                     f'(provenance: {next(iter((qwait.get("provenance") or {}).values()), "?")}).</p></section>')
    return "\n".join(parts)


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def main(argv=None):
    pi_turns, pi_stats = load_pi_turns()
    claude_turns, claude_stats = load_claude_turns()
    extra = {"pi_turns": pi_turns, "claude_turns": claude_turns}
    _doc, conc = run_slice("concurrency", compute_concurrency, extra)
    _doc, stall = run_slice("stall", compute_stall, extra)
    _doc, _qwait = run_slice("queue_wait", compute_queue_wait)
    totals = stall.get("totals") or {}
    print(f"ops.concurrency: p95 agentes={conc.get('agents_p95')}, "
          f"requests en vuelo={conc.get('requests_p95')}, días={len(conc.get('daily') or [])} "
          f"(pi: {pi_stats.get('sessions', 0)} sesiones, claude: {claude_stats.get('sessions', 0)}) | "
          f"ops.stall: idle={totals.get('minutes_idle')} min, error-adyacente="
          f"{totals.get('minutes_error_adjacent')} min | ops.queue_wait: null (no_ttft_in_local_logs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
