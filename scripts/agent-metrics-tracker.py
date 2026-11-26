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

def turns_from_pi(records):
    """Registros de una sesión Pi → turnos {ts, role, model, stop, is_error,
    dur_s}. Roles: user_prompt / assistant / tool. Los preámbulos inyectados
    del harness viajan como `custom`, no como user: no abren tarea."""
    model = None
    turns = []
    for r in records:
        typ = r.get("type")
        if typ == "model_change":
            model = r.get("modelId") or model
            continue
        if typ != "message":
            continue
        msg = r.get("message") or {}
        role = msg.get("role")
        ts = _dt(r.get("timestamp") or msg.get("timestamp"))
        if ts is None:
            continue
        if role == "user":
            turns.append({"ts": ts, "role": "user_prompt", "model": model})
        elif role == "assistant":
            dur_ms = msg.get("durationMs")
            turns.append({
                "ts": ts, "role": "assistant",
                "model": msg.get("model") or model,
                "stop": msg.get("stopReason"),
                "dur_s": (float(dur_ms) / 1000.0) if isinstance(dur_ms, (int, float)) else None,
            })
        elif role == "toolResult":
            turns.append({"ts": ts, "role": "tool",
                          "is_error": bool(msg.get("isError"))})
    return turns


PREAMBLE_MARKERS = (
    "You are resuming an existing conversation",
    "This session is being continued from a previous conversation",
)


def _is_preamble(content):
    """Mensaje `user` inyectado por el harness al reanudar una sesión: no es
    un prompt humano (mismo criterio que usage-tracker.py, FPA-140)."""
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = " ".join(b.get("text", "") for b in content
                        if isinstance(b, dict) and isinstance(b.get("text"), str))
    else:
        return False
    head = text.strip()[:160]
    return any(head.startswith(marker) for marker in PREAMBLE_MARKERS)


def turns_from_claude(records, stats=None):
    """Registros de una sesión Claude Code → turnos. Solo el hilo principal
    (`isSidechain` false): los subagentes duplicarían los conteos. Claude no
    escribe stopReason `aborted`, así que `stop` queda None (cobertura)."""
    turns = []
    for r in records:
        if r.get("isSidechain"):
            continue
        typ = r.get("type")
        msg = r.get("message") or {}
        ts = _dt(r.get("timestamp") or msg.get("timestamp"))
        if ts is None:
            continue
        if typ == "user":
            content = msg.get("content")
            is_tool_result = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            if is_tool_result:
                err = bool(r.get("toolUseResult", {}).get("is_error")) if isinstance(
                    r.get("toolUseResult"), dict) else False
                turns.append({"ts": ts, "role": "tool", "is_error": err})
            elif isinstance(content, str) or isinstance(content, list):
                if _is_preamble(content):
                    if stats is not None:
                        stats["preambles_skipped"] = stats.get("preambles_skipped", 0) + 1
                else:
                    turns.append({"ts": ts, "role": "user_prompt"})
        elif typ == "assistant":
            turns.append({"ts": ts, "role": "assistant",
                          "model": msg.get("model"),
                          "stop": msg.get("stop_reason")})
    return turns


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
        sid = None
        for r in records[:3]:
            if r.get("type") == "session":
                sid = r.get("id")
                break
        key = sid or str(path)
        prev = by_id.get(key)
        if prev is None or len(records) > prev[1]:
            by_id[key] = (path, len(records), records)
    turns = []
    for path, _n, records in by_id.values():
        turns.extend(turns_from_pi(records))
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
        turns.extend(turns_from_claude(records, stats))
    return turns, {"sessions": sessions, **stats}


# ----------------------------------------------------------------------
# Tareas (funciones puras: testeables sin disco)
# ----------------------------------------------------------------------

def build_tasks(turns, gap_cap_s=1800, retry_window_s=300, max_task_s=14400,
                stats=None):
    """Turnos → tareas. Un `user_prompt` abre tarea; el siguiente la cierra.

    Tiempo ocupado del agente: por turno, `dur_s` si el log lo trae, si no el
    gap desde el evento previo, ambos con tope `gap_cap_s` (un gap mayor es
    inactividad humana o máquina dormida, no trabajo).

    Flags: aborted (stopReason aborted), error (stopReason error), tool_error
    (algún toolResult con isError), retry (el prompt que abrió la tarea llegó
    ≤ retry_window_s después del ÚLTIMO turno de una tarea anterior que
    terminó en fallo y tuvo actividad). `stats` acumula `orphan_turns`
    (turnos previos al primer prompt de una sesión retomada).
    """
    ordered = sorted(turns, key=lambda t: t["ts"])
    tasks = []
    cur = None
    prev_ts = None
    prev_end_ts = None
    prev_ended_error = False

    def close(task, end_ts):
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

    for t in ordered:
        gap = (t["ts"] - prev_ts).total_seconds() if prev_ts else None
        if gap is not None and gap < 0:
            gap = 0
        if t["role"] == "user_prompt":
            if cur is not None:
                # la ventana se mide desde el último turno de la tarea
                # anterior: usar el prompt nuevo daría 0 s siempre (dead knob)
                prev_ended_error = bool(
                    (cur["aborted"] or cur["errors"]) and cur["busy_s"] > 0)
                prev_end_ts = cur.get("_last") or cur["_start"]
            else:
                prev_ended_error = False
                prev_end_ts = None
            close(cur, t["ts"])
            cur = {"_start": t["ts"], "busy_s": 0.0, "turns": 0,
                   "aborted": 0, "errors": 0, "tool_errors": 0, "retry": 0,
                   "model": None, "clipped": False}
            if (prev_ended_error and prev_end_ts is not None
                    and (t["ts"] - prev_end_ts).total_seconds() <= retry_window_s):
                cur["retry"] = 1
            prev_ts = t["ts"]
            continue
        if cur is None:
            if stats is not None:
                stats["orphan_turns"] = stats.get("orphan_turns", 0) + 1
            prev_ts = t["ts"]
            continue
        busy = None
        if t["role"] == "assistant":
            cur["turns"] += 1
            cur["model"] = cur["model"] or t.get("model")
            if t.get("stop") == "aborted":
                cur["aborted"] = 1
            elif t.get("stop") == "error":
                cur["errors"] = 1
            if t.get("dur_s") is not None:
                busy = min(float(t["dur_s"]), gap_cap_s)
        elif t["role"] == "tool":
            if t.get("is_error"):
                cur["tool_errors"] += 1
        if busy is None and gap is not None:
            busy = min(gap, gap_cap_s)
        if busy:
            cur["busy_s"] += busy
        cur["_last"] = t["ts"]
        prev_ts = t["ts"]
    if ordered:
        close(cur, ordered[-1]["ts"])
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


def series(tasks, days, sources, models, tz=None):
    """Series compactas para percentiles exactos por ventana en el viz.

    `day` = índice en `days` (día local), `s` = segundos ocupados (int),
    `sp` = segundos de reloj de la tarea (incluye esperas),
    `src`/`model` = índice, `f` = bitmask de flags.
    """
    day_ix = {d: i for i, d in enumerate(days)}
    src_ix = {s: i for i, s in enumerate(sources)}
    model_ix = {m: i for i, m in enumerate(models)}
    out = {"day": [], "s": [], "sp": [], "src": [], "model": [], "f": []}
    for t in tasks:
        day = task_day(t, tz)
        if day not in day_ix:
            continue
        out["day"].append(day_ix[day])
        out["s"].append(int(round(t["busy_s"])))
        out["sp"].append(int(round(t.get("span_s") or 0)))
        out["src"].append(src_ix.get(t.get("source"), 0))
        out["model"].append(model_ix.get(t.get("model") or "?", 0))
        out["f"].append(task_flags(t))
    return out


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
            continue
        root_depth = len(root.parts)
        for dirpath, dirnames, _files in os.walk(root):
            p = Path(dirpath)
            if len(p.parts) - root_depth >= max_depth:
                dirnames[:] = []
            if ".git" in dirnames:
                dirnames[:] = [d for d in dirnames if d != ".git"]
                found[str(p)] = p
    return [found[k] for k in sorted(found)], missing


def repo_metrics(paths, run=_git, prefixes=None):
    """Repos → {name, first_commit, days_by_month}.

    Días con commit por mes calendario: la ventana del dashboard suma meses
    (los días no se solapan entre meses, así el conteo de días del rango es
    exacto). `prefixes` = raíces configuradas; el nombre es relativo a la que
    matchea (sin prefijos hardcodeados).
    """
    out = []
    prefixes = [os.path.expanduser(x) for x in
                (prefixes or ["~/para/areas/dev/gh"])]
    prefixes.sort(key=len, reverse=True)
    for path in paths:
        log = run(["log", "--date=short", "--format=%ad"], path)
        if not log:
            continue
        dates = [line.strip() for line in log.splitlines() if line.strip()]
        days = sorted(set(dates))
        if not days:
            continue
        days_by_month = {}
        for d in days:
            days_by_month[d[:7]] = days_by_month.get(d[:7], 0) + 1
        name = str(path)
        for prefix in prefixes:
            if name.startswith(prefix + os.sep):
                name = name[len(prefix) + 1:]
                break
        out.append({"name": name, "first_commit": days[0],
                    "days_by_month": days_by_month})
    return out


# ----------------------------------------------------------------------
# Ensamblado
# ----------------------------------------------------------------------

def build_report(tasks, cfg, coverage, repos, report_meta, tz=None):
    """Tareas de todas las fuentes → dataset del dashboard."""
    days = sorted({task_day(t, tz) for t in tasks})
    sources = sorted({t["source"] for t in tasks}) or ["pi"]
    models = sorted({t["model"] for t in tasks if t.get("model")}) or ["?"]
    return {
        "metadata": {
            "date_range": {"start": report_meta["date_range"]["start"],
                           "end": report_meta["date_range"]["end"]},
            "timezone": report_meta.get("timezone"),
            "first_task": days[0] if days else None,
            "last_task": days[-1] if days else None,
            "task_definition": (
                "una tarea = un prompt de usuario y los turnos que dispara "
                "hasta el prompt siguiente; el tiempo del agente es la suma "
                "de los turnos (durationMs reportado, o gap desde el evento "
                "previo con tope gap_cap)."),
            "gap_cap_minutes": cfg["gap_cap_minutes"],
            "retry_window_minutes": cfg["retry_window_minutes"],
            "config_source": cfg.get("config_source"),
            "provenance": {"tasks": "derived", "aborts": "reported",
                           "tool_errors": "reported", "repos": "reported"},
            "coverage": coverage,
            "reasons": {
                "abort_signal": "solo pi registra stopReason aborted/error; "
                                "claude-cli no escribe esa señal",
                "fork_dedupe": "sesiones pi con el mismo id = fork: se toma "
                               "el archivo más largo",
            },
        },
        "days": days,
        "sources": sources,
        "models": models,
        "daily": daily_from_tasks(tasks, tz),
        "tasks": series(tasks, days, sources, models, tz),
        "repos": repos,
        "months": sorted({t["_start"].strftime("%Y-%m") for t in tasks}),
    }


def summarize_coverage(pi_stats, claude_stats):
    return [
        {"source": "pi", "turn_level": True, "abort_signal": True,
         "sessions": pi_stats.get("sessions", 0),
         "note": pi_stats.get("skipped_reason") or
                 "solo sesiones on-disk (pi rota las viejas; arranca 2026-04)"},
        {"source": "claude-cli", "turn_level": True, "abort_signal": False,
         "sessions": claude_stats.get("sessions", 0),
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


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    out_path = OUT
    if "--output" in argv:
        out_path = Path(argv[argv.index("--output") + 1])
    if "--validate-only" in argv:
        # valida un dataset ya escrito (los tests usan esto para el caso
        # negativo del spec, sin re-extraer logs)
        target = Path(argv[argv.index("--validate-only") + 1])
        return _report_errors(_schema_errors(json.loads(target.read_text(encoding="utf-8"))))

    cfg = load_config()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    meta = report["metadata"]
    tz = tz_from_offset(meta.get("timezone"))

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

    repo_paths, missing_roots = find_git_repos(cfg["repo_roots"],
                                               cfg["repo_max_depth"])
    repos = repo_metrics(repo_paths, prefixes=cfg["repo_roots"])
    merged = build_report(tasks, cfg, coverage, repos, meta, tz=tz)
    merged["metadata"]["repo_roots"] = cfg["repo_roots"]
    merged["metadata"]["repo_roots_missing"] = missing_roots
    merged["metadata"]["min_commit_days"] = cfg["min_commit_days"]
    merged["metadata"]["min_returning_months"] = cfg["min_returning_months"]
    merged["metadata"]["bulk_day_interactions"] = cfg["bulk_day_interactions"]
    merged["metadata"]["orphan_turns"] = task_stats.get("orphan_turns", 0)
    merged["metadata"]["preamble_prompts_skipped"] = cl_stats.get(
        "preambles_skipped", 0)
    if missing_roots:
        merged["metadata"]["reasons"]["repo_roots_missing"] = (
            "raíces de repos configuradas que no existen: "
            + ", ".join(missing_roots))
    if tz is None:
        merged["metadata"]["reasons"]["timezone"] = (
            "el reporte no declara timezone: días atribuidos en UTC")

    # validar ANTES de escribir: un dataset inválido no debe quedar en disco
    if "--validate" in argv:
        errors = _schema_errors(merged)
        if errors:
            return _report_errors(errors)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    m = merged["metadata"]
    print(f"tareas: {len(merged['tasks']['s'])} · días: {len(merged['days'])} "
          f"· fuentes: {', '.join(merged['sources'])} · modelos: {len(merged['models'])}")
    print(f"rango: {m['first_task']} → {m['last_task']} · repos: {len(merged['repos'])}")
    if missing_roots:
        print(f"aviso: raíces inexistentes: {', '.join(missing_roots)}", file=sys.stderr)
    print(f"escrito: {out_path.relative_to(ROOT) if out_path.is_absolute() else out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
