#!/usr/bin/env python3
"""Extractor de sesiones omp: turnos (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

class _OmpScan:
    """Estado del scan de 1 archivo de sesión omp (formato verificado
    2026-11-11, runtime 18.18.8).

    - record "session": {id, timestamp, cwd} — cwd REAL, no mungeado
    - turno principal: "message" role=assistant con message.usage
      {input, output, cacheRead, cacheWrite, cost{total}} (costo *reported*)
    - llamadas auxiliares del harness: record "model_usage" (purpose
      title/job-caption/find/…) — también facturan tokens → fila propia
      con kind "aux_model_call"
    - prompts: "message" role=user (los inyectados del harness viajan como
      records "custom", no como user messages)
    - tools: "custom" customType=tool_execution_start (data.toolName)

    Malformed lines best-effort (una línea rota no tira la sesión), como
    codex (coffe-2sy). Devuelve (None, []) si el archivo no trae session
    record ni actividad.
    """

    def __init__(self):
        self.cwd = None
        self.session_ts = None
        self.first_ts = None
        self.last_ts = None
        self.first_raw = None
        self.last_raw = None
        self.user_ts = []
        self.n_errors = 0
        self.tools = set()
        self.calls = []  # (ts, model, usage, kind)

    def feed(self, entry):
        typ = entry.get("type")
        ts = parse_ts(entry.get("timestamp"))
        self._dispatch(typ, entry, ts)
        if ts:
            if self.first_ts is None:
                self.first_ts, self.first_raw = ts, entry.get("timestamp")
            self.last_ts, self.last_raw = ts, entry.get("timestamp")
        return self

    def _dispatch(self, typ, entry, ts):
        if typ == "session":
            self.cwd = entry.get("cwd") or self.cwd
            self.session_ts = ts or self.session_ts
        elif typ == "message":
            self.message(entry, ts)
        elif typ == "model_usage":
            self.aux_call(entry, ts)
        elif typ == "custom" and entry.get("customType") == "tool_execution_start":
            name = (entry.get("data") or {}).get("toolName")
            if name:
                self.tools.add(str(name))
        return self

    def message(self, entry, ts):
        msg = entry.get("message", {}) or {}
        if msg.get("role") == "user":
            if ts:
                self.user_ts.append(ts)
        elif msg.get("role") == "assistant":
            usage = msg.get("usage") or {}
            if not usage:
                return  # sin telemetría: nada facturable
            if msg.get("errorId"):
                self.n_errors += 1
            if ts:
                self.calls.append((ts, msg.get("model", "unknown"),
                                   usage, "assistant_turn"))
        return self

    def aux_call(self, entry, ts):
        usage = entry.get("usage") or {}
        if ts and usage:
            self.calls.append((ts, entry.get("model", "unknown"),
                               usage, "aux_model_call"))
        return self

    def is_empty(self):
        return self.cwd is None and not self.calls and not self.user_ts

    def meta(self):
        return {
            "cwd": self.cwd, "session_ts": self.session_ts,
            "first_ts": self.first_ts, "last_ts": self.last_ts,
            "first_raw": self.first_raw, "last_raw": self.last_raw,
            "user_ts": self.user_ts, "n_errors": self.n_errors,
            "tools": self.tools,
        }


def _omp_scan(f):
    """1 archivo de sesión omp → (meta, calls)."""
    scan = _OmpScan()
    with open(f) as fh:
        for line in fh:
            entry = _json_line(line)
            if entry is not None:
                scan.feed(entry)
    if scan.is_empty():
        return None, []
    return scan.meta(), scan.calls


def _omp_skip(f, e, skipped):
    if skipped is not None:
        key = f"omp:{f.name}"
        skipped[key] = skipped.get(key, 0) + 1
    elif os.environ.get("TRACKER_DEBUG"):
        print(f"  [skipped omp] {f.name}: {e}", file=sys.stderr)
    return skipped


def _omp_excluded_row(cwd, sd_name, calls):
    # FPA-141: registrar lo excluido, no descartarlo en silencio
    return {
        "project": amp_proj_from_uri(cwd) or clean_proj_name(sd_name),
        "tool": "omp",
        "interactions": len(calls),
        "cost_effective": None,
        "timestamp": None,
    }


def _omp_user_kinds(user_ts, kinds):
    # FPA-140: user prompts se cuentan aparte (no generan fila)
    for ts_u in user_ts:
        if in_window(ts_u):
            kinds.setdefault(ts_u.strftime("%Y-%m"), Counter())["user_prompt"] += 1
    return kinds


def _omp_row_usage(usage):
    return (usage.get("input") or 0, usage.get("output") or 0,
            usage.get("cacheRead") or 0, usage.get("cacheWrite") or 0)


def _omp_row_cost(usage, ts, fam_ver, toks):
    cost_info = usage.get("cost")
    cost = cost_info.get("total") if isinstance(cost_info, dict) else None
    if cost or not any(toks[:3]):
        return cost or 0
    fam, ver = fam_ver
    return estimate_cost(fam, ver, toks[0], toks[1], toks[2], toks[3],
                         when=ts.date())


def _omp_row_tokens(model, usage, ts):
    fam_ver = model_details(model)
    toks = _omp_row_usage(usage)
    return (*fam_ver, *toks, _omp_row_cost(usage, ts, fam_ver, toks))


def _omp_call_row(ts, call, proj, fname):
    """call = (model, usage, kind)."""
    model, usage, kind = call
    fam, ver, inp, out, cache_r, cache_w, cost = _omp_row_tokens(model, usage, ts)
    return {
        "source": "omp",
        "tool": "omp",
        "model_raw": model,
        "model_family": fam,
        "model_version": ver,
        "project": proj,
        "timestamp": ts.isoformat(),
        "hour": hour_key(ts),
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cache_r,
        "cache_write_tokens": cache_w,
        "cost_effective": cost,
        "kind": kind,
        "session_file": fname,  # add-session-spend-report
    }


def _omp_calls_rows(f, calls, proj, rows):
    for ts, model, usage, kind in calls:
        if not in_window(ts):
            continue  # coffe-snj: ventana
        rows.append(_omp_call_row(ts, (model, usage, kind), proj, f.name))
    return rows


def _omp_session_rows(sd, ctx):
    """ctx = (skipped, kinds, excluded_rows, rows)."""
    skipped, kinds, excluded_rows, rows = ctx
    for f in sorted(sd.glob("*.jsonl")):
        try:
            meta, calls = _omp_scan(f)
        except OSError as e:
            _omp_skip(f, e, skipped)
            continue
        if meta is None:
            continue
        cwd = meta["cwd"] or sd.name
        if not is_charly(cwd):
            excluded_rows.append(_omp_excluded_row(cwd, sd.name, calls))
            continue
        if kinds is not None:
            _omp_user_kinds(meta["user_ts"], kinds)
        proj = amp_proj_from_uri(cwd) or "omp-unknown"
        _omp_calls_rows(f, calls, proj, rows)
    return rows


def extract_omp(skipped=None, kinds=None, excluded=None, sessions_dir=None):
    """Extrae sesiones omp (~/.omp/agent/sessions). kinds/excluded como
    extract_pi/extract_claude (FPA-140/141). sessions_dir permite tests
    con tmpdir.

    Una fila por llamada facturada: turnos principales (assistant con
    usage) + llamadas auxiliares del harness (model_usage). El costo de
    usage.cost.total es *reported* por el harness; sin costo → estimación
    pay-per-token como pi (coffe-8t8). omp paga por token vía OpenRouter:
    no está en SUBSCRIPTIONS → cost_real = cost_effective.
    """
    rows = []
    excluded_rows = []
    sessions_dir = sessions_dir or OMP_DIR
    if not sessions_dir.exists():
        return rows, excluded_rows
    for sd in sorted(sessions_dir.iterdir()):
        if not sd.is_dir():
            continue
        _omp_session_rows(sd, (skipped, kinds, excluded_rows, rows))
    return rows, excluded_rows

usage_state._link(globals())
