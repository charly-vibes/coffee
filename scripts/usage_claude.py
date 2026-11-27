#!/usr/bin/env python3
"""Extractor de sesiones Claude (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state


def extract_claude(skipped=None):
    """
    Read Claude JSONL files + dashboard cache.
    Uses cache cost where available (more accurate), falls back to token-based estimate.
    Returns deduplicated rows (no double counting between JSONL and cache).

    `skipped`: dict opcional; se incrementa con las líneas/archivos descartados
    por error de parseo (visible en metadata.skipped_lines del reporte).
    """
def _kind_of_claude(entry, msg):
    """FPA-140: kind del evento Claude. user_prompt se cuenta aparte (no
genera fila de coste); tool_call si el bloque assistant trae tool_use."""
    if entry.get("type") == "user":
        return "user_prompt"
    content = msg.get("content")
    if isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_use" for b in content):
        return "tool_call"
    return "assistant_turn"


def _record_skipped(skipped, tag, fname, e):
    """Registra una línea/archivo descartado (metadata.skipped_lines) o,
    sin dict de skipped, avisa a stderr en modo TRACKER_DEBUG."""
    if skipped is not None:
        key = f"{tag}:{fname}"
        skipped[key] = skipped.get(key, 0) + 1
    elif os.environ.get("TRACKER_DEBUG"):
        print(f"  [skipped] {fname}: {e}", file=sys.stderr)
    return skipped


def _debug_skip(message):
    """Aviso stderr best-effort para fuentes sin dict de skipped."""
    if os.environ.get("TRACKER_DEBUG"):
        print(f"  [skipped] {message}", file=sys.stderr)


def _json_line(line):
    """json.loads tolerante: None ante línea rota (best-effort)."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _claude_excluded_row(proj, projects_dir):
    # FPA-141: registrar lo excluido, no descartarlo en silencio
    return {"project": proj, "tool": "claude-cli",
            "interactions": len(list((projects_dir / proj).glob("*.jsonl"))),
            "cost_effective": None, "timestamp": None}


def _claude_user_kinds(ts, kinds):
    if kinds is not None:
        kinds.setdefault(ts.strftime("%Y-%m"), Counter())["user_prompt"] += 1
    return kinds


def _claude_assistant_kinds(entry, ts, msg, kinds):
    if kinds is not None:
        kinds.setdefault(ts.strftime("%Y-%m"), Counter())[
            _kind_of_claude(entry, msg)] += 1
    return kinds


def _claude_row_from_entry(entry, ts, proj, fname):
    msg = entry.get("message", {})
    if isinstance(msg, str):
        msg = json.loads(msg)
    usage = msg.get("usage", {}) or {}
    model = msg.get("model", "unknown")
    fam, ver = model_details(model)
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    cache_r = usage.get("cache_read_input_tokens", 0) or 0
    cache_c = usage.get("cache_creation_input_tokens", 0) or 0
    cost = estimate_cost(fam, ver, inp, out, cache_r, cache_c, when=ts.date())
    return {
        "source": "claude_jsonl",
        "tool": "claude-cli",
        "model_raw": model,
        "model_family": fam,
        "model_version": ver,
        "project": proj,
        "timestamp": ts.isoformat(),
        "hour": hour_key(ts),
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cache_r,
        "cache_write_tokens": cache_c,
        "cost_effective": cost,
        "kind": _kind_of_claude(entry, msg),
        "session_file": fname,  # add-session-spend-report
    }


def _claude_file_rows(f, proj, out):
    """Líneas JSONL → filas (append a out.rows); out = (rows, kinds,
    skipped)."""
    rows, kinds, skipped = out
    try:
        fh = open(f)
    except OSError as e:
        _record_skipped(skipped, "claude", f.name, e)
        return
    with fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as e:
                _record_skipped(skipped, "claude", f.name, e)
                continue
            _claude_line(entry, (f.name, proj, rows, kinds))


def _claude_line(entry, out):
    """1 evento → kinds + fila (append); errores → _record_skipped.
    out = (fname, proj, rows, kinds)."""
    fname, proj, rows, kinds = out
    try:
        ts = parse_ts(entry.get("timestamp"))
        if not ts or not in_window(ts):
            return  # coffe-snj: ventana --since/--until
        if entry.get("type") == "user":
            _claude_user_kinds(ts, kinds)
        elif entry.get("type") == "assistant":
            _claude_assistant_line(entry, ts, (proj, fname, rows, kinds))
    except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
        _record_skipped(None, "claude", fname, e)


def _claude_assistant_line(entry, ts, ctx):
    """ctx = (proj, fname, rows, kinds)."""
    proj, fname, rows, kinds = ctx
    msg = entry.get("message", {})
    if isinstance(msg, str):
        msg = json.loads(msg)
    _claude_assistant_kinds(entry, ts, msg, kinds)
    rows.append(_claude_row_from_entry(entry, ts, proj, fname))
    return rows


def _claude_cache_turn(lookup, proj, turn):
    ts = parse_ts(turn.get("ts"))
    if not ts or not in_window(ts):
        return lookup  # coffe-snj: ventana
    # Build lookup key: (timestamp_iso, model, project)
    lk = (ts.isoformat(), turn.get("model", "unknown"), proj)
    # Keep the first (earliest) entry if multiple matches
    if lk not in lookup:
        lookup[lk] = turn.get("cost", 0) or 0
    return lookup


def _claude_cache_summary(lookup, summary):
    if summary.get("source") != "claude":
        return
    proj = summary.get("project", "")
    if not is_charly(proj):
        return
    for turn in summary.get("turns", []):
        _claude_cache_turn(lookup, proj, turn)


def _claude_cache_lookup():
    """Step 2: Read dashboard cache for cost overrides."""
    lookup = {}
    cache_file = CLAUDE_DIR / "dashboard-cache.json"
    if not cache_file.exists():
        return lookup
    cache = json.loads(cache_file.read_text())
    for key, summary in cache.get("entries", {}).items():
        _claude_cache_summary(lookup, summary)
    return lookup


def _claude_jsonl_rows(skipped, kinds, excluded_rows):
    """Step 1: Read JSONL files."""
    rows = []
    projects_dir = CLAUDE_DIR / "projects"
    for pd in projects_dir.iterdir():
        if not pd.is_dir():
            continue
        proj = pd.name
        if not is_charly(proj):
            excluded_rows.append(_claude_excluded_row(proj, projects_dir))
            continue
        for f in pd.glob("*.jsonl"):
            _claude_file_rows(f, proj, (rows, kinds, skipped))
    return rows


def _claude_merge_cache(jsonl_rows, lookup):
    """Step 3: Merge cache costs into JSONL rows."""
    merged = []
    for row in jsonl_rows:
        lk = (row["timestamp"], row["model_raw"], row["project"])
        if lk in lookup:
            row["cost_effective"] = lookup[lk]
            row["source"] = "claude_cache_merged"
        merged.append(row)
    return merged


def _claude_cache_only_row(ts, model, proj, cost):
    fam, ver = model_details(model)
    return {
        "source": "claude_cache_only",
        "tool": "claude-cli",
        "model_raw": model,
        "model_family": fam,
        "model_version": ver,
        "project": proj,
        "timestamp": ts,
        "hour": hour_key(parse_ts(ts)),
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost_effective": cost,
        "kind": "assistant_turn",
        # add-session-spend-report: sin archivo de sesión → fila con
        # session_id de reserva (hash), nunca atribuida a una sesión ajena
    }


def _claude_cache_only_rows(merged, lookup):
    """Step 4: Add any cache entries that weren't in JSONL (shouldn't
    happen, but safety)."""
    cache_keys = {(r["timestamp"], r["model_raw"], r["project"]) for r in merged}
    return [_claude_cache_only_row(ts, model, proj, cost)
            for (ts, model, proj), cost in lookup.items()
            if (ts, model, proj) not in cache_keys]


def extract_claude(skipped=None, kinds=None, excluded=None):
    """
    Read Claude JSONL files + dashboard cache.
    Uses cache cost where available (more accurate), falls back to token-based estimate.
    Returns deduplicated rows (no double counting between JSONL and cache).

    `skipped`: dict opcional; se incrementa con las líneas/archivos descartados
    por error de parseo (visible en metadata.skipped_lines del reporte).
    `kinds`: dict opcional {mes: Counter} de eventos por kind (FPA-140),
    incluidos user prompts (no generan fila de coste).
    Devuelve (rows, excluded_rows): las excluidas son de proyectos fuera del
    filtro charly (FPA-141) y NO entran al reporte; timestamps crudos.
    """
    excluded_rows = []
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.exists():
        return [], []  # máquina sin logs de Claude (el guard de main() se encarga del resto)
    jsonl_rows = _claude_jsonl_rows(skipped, kinds, excluded_rows)
    lookup = _claude_cache_lookup()
    merged = _claude_merge_cache(jsonl_rows, lookup)
    merged.extend(_claude_cache_only_rows(merged, lookup))
    return merged, excluded_rows


_PI_TOOL_MAP = {"openai-codex": "codex", "claude-cli": "claude-cli",
                "google-gemini-cli": "gemini-cli", "gemini-cli": "gemini-cli",
                "openrouter": "openrouter", "github-copilot": "copilot",
                "anthropic": "claude-cli"}

usage_state._link(globals())
