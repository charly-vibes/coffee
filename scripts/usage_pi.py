#!/usr/bin/env python3
"""Extractor de sesiones Pi (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state
from dataclasses import dataclass  # noqa: F401  (necesario antes de _link)

@dataclass
class _PiTurn:
    """Contexto de un turno assistant de Pi (fila en construcción)."""
    entry: dict
    msg: dict
    ts: object
    fname: str
    proj: str


class _PiTurnAcc:
    """coffe-dnx.2: señales por sesión acumuladas durante el parse
    (se adjuntan a la fila assistant siguiente — aditivo, no cambia el
    shape del row salvo claves nuevas opcionales)."""

    def __init__(self):
        self.calls = 0          # toolResults pendientes
        self.errors = 0
        self.durs = []          # durationMs de toolResults pendientes
        self.mc_count = 0       # model_change acumulados (el 1ro es el modelo inicial)
        self.model_chain = []   # modelos en orden de primera aparición
        self.last_row = None

    def tool_result(self, msg):
        # coffe-dnx.2: errores/latencia de tools (se atribuyen al turno
        # assistant que los invocó)
        self.calls += 1
        if msg.get("isError"):
            self.errors += 1
        td = msg.get("durationMs")
        if isinstance(td, (int, float)):
            self.durs.append(int(td))
        return self

    def model_change(self, entry):
        self.mc_count += 1
        mid = entry.get("modelId")
        if mid and mid not in self.model_chain:
            self.model_chain.append(mid)
        return self

    def track_model(self, model):
        if model not in self.model_chain:
            self.model_chain.append(model)

    def reset_pending(self):
        self.calls = self.errors = 0
        self.durs = []
        return self

    def flush_tail(self):
        # toolResults de cola (tras el último turno assistant, p.ej.
        # sesión abortada): se atribuyen al último turno emitido
        row = self.last_row
        if row is not None and (self.calls or self.errors or self.durs):
            row["tool_calls"] += self.calls
            row["tool_errors"] += self.errors
            row["tool_durations_ms"] += self.durs
        return self


def _pi_usage_tokens(usage):
    inp = usage.get("input_tokens") or usage.get("input", 0) or 0
    out = usage.get("output_tokens") or usage.get("output", 0) or 0
    cache_r = usage.get("cacheRead") or usage.get("cache_read") or 0
    cache_w = usage.get("cacheWrite") or usage.get("cache_write") or 0
    return inp, out, cache_r, cache_w


def _pi_cost(usage, fam_ver, ts, toks):
    """Costo del turno: reported (cost.total) o estimado (coffe-8t8: los
    logs de gemini-cli (Pi) no traen cost → estimación pay-per-token con
    los rates vigentes en la fecha (FPA-016). Sin tokens no hay base de
    estimación: queda 0.0 real). Devuelve (cost, cost_info)."""
    fam, ver = fam_ver
    inp, out, cache_r, cache_w = toks
    cost_info = usage.get("cost", {})
    if isinstance(cost_info, dict):
        cost = cost_info.get("total", 0) or 0
    else:
        cost = cost_info or 0
    if not cost and (inp or out or cache_r):
        cost = estimate_cost(fam, ver, inp, out, cache_r or 0, cache_w or 0,
                             when=ts.date())
    return cost, cost_info


def _pi_kinds(msg, ts, kinds):
    if kinds is not None:
        kind = "tool_call" if _pi_has_tool_call(msg) else "assistant_turn"
        kinds.setdefault(ts.strftime("%Y-%m"), Counter())[kind] += 1
    return kinds


def _pi_row_dict(turn, acc):
    entry, msg, ts = turn.entry, turn.msg, turn.ts
    usage = msg.get("usage", {}) or {}
    model = msg.get("model", "unknown")
    provider = entry.get("provider", msg.get("provider", ""))
    fam, ver = model_details(model)
    toks = _pi_usage_tokens(usage)
    cost, cost_info = _pi_cost(usage, (fam, ver), ts, toks)
    inp, out, cache_r, cache_w = toks
    acc.track_model(model)
    return {
        "source": "pi",
        "tool": _PI_TOOL_MAP.get(provider, provider),
        "model_raw": model,
        "model_family": fam,
        "model_version": ver,
        "project": turn.proj,
        "timestamp": ts.isoformat(),
        "hour": hour_key(ts),
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cache_r or 0,
        "cache_write_tokens": cache_w or 0,
        "cost_effective": cost,
        "kind": "tool_call" if _pi_has_tool_call(msg) else "assistant_turn",
        "session_file": turn.fname,  # add-session-spend-report
        # coffe-dnx.2: señales de calidad/enrutamiento
        "duration_ms": (int(msg["durationMs"])
                        if isinstance(msg.get("durationMs"), (int, float))
                        else None),
        "stop_reason": msg.get("stopReason"),
        "cost_reported": (isinstance(cost_info, dict)
                          and cost_info.get("totalFromProvider") is True),
        "tool_calls": acc.calls,
        "tool_errors": acc.errors,
        "tool_durations_ms": list(acc.durs),
        "model_change_idx": acc.mc_count,
        "model_chain": list(acc.model_chain),
    }


class _PiFileParser:
    """Parse de un archivo JSONL de Pi → filas (rol a rol)."""

    def __init__(self, out):
        self.fname, self.proj, self.rows, self.kinds = out
        self.acc = _PiTurnAcc()

    def run(self, f):
        with open(f) as fh:
            for line in fh:
                self.feed(json.loads(line))
        self.acc.flush_tail()

    def feed(self, entry):
        if entry.get("type") == "model_change":
            return self.acc.model_change(entry)
        if entry.get("type") != "message":
            return
        msg = entry.get("message", {}) or {}
        role = msg.get("role")
        if role == "toolResult":
            return self.acc.tool_result(msg)
        if role == "user":
            return self.count_user(entry)
        if role != "assistant":
            return
        self.assistant(entry, msg)

    def count_user(self, entry):
        # FPA-140: user prompts se cuentan, no generan fila de coste
        ts_u = parse_ts(entry.get("timestamp"))
        if self.kinds is not None and ts_u is not None and in_window(ts_u):
            self.kinds.setdefault(ts_u.strftime("%Y-%m"), Counter())["user_prompt"] += 1
        return self

    def assistant(self, entry, msg):
        ts = parse_ts(entry.get("timestamp"))
        if not ts or not in_window(ts):
            return  # coffe-snj: ventana
        _pi_kinds(msg, ts, self.kinds)
        turn = _PiTurn(entry, msg, ts, self.fname, self.proj)
        row = _pi_row_dict(turn, self.acc)
        self.rows.append(row)
        self.acc.last_row = row
        self.acc.reset_pending()
        return self


def extract_pi(skipped=None, kinds=None, excluded=None, sessions_dir=None):
    """Extrae sesiones Pi. kinds/excluded como en extract_claude
    (FPA-140/141). Devuelve (rows, excluded_rows). sessions_dir permite
    tests con tmpdir (coffe-8t8)."""
    rows = []
    excluded_rows = []
    sessions_dir = sessions_dir or (PI_DIR / "sessions")
    if not sessions_dir.exists():
        return rows, excluded_rows
    for sd in sessions_dir.iterdir():
        if not sd.is_dir():
            continue
        proj = sd.name
        if not is_charly(proj):
            excluded_rows.append({"project": proj, "tool": "pi",
                                  "interactions": len(list(sd.glob("*.jsonl"))),
                                  "cost_effective": None, "timestamp": None})
            continue
        for f in sd.glob("*.jsonl"):
            parser = _PiFileParser((f.name, proj, rows, kinds))
            try:
                parser.run(f)
            except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
                _record_skipped(skipped, "pi", f.name, e)
    return rows, excluded_rows


def _container_has_tool(container):
    """True/False si el contenedor (parts/content/toolCalls) trae un tool
    call; None si no es lista (el siguiente contenedor decide)."""
    if not isinstance(container, list):
        return None
    for part in container:
        if isinstance(part, dict):
            ptype = str(part.get("type", ""))
            if ptype.startswith("tool") or "toolCall" in ptype:
                return True
        elif isinstance(part, str) and part in ("tool-call", "tool_use"):
            return True
    return False


def _pi_has_tool_call(msg):
    """FPA-140: detección best-effort de tool calls en mensajes Pi
    (formato AI SDK: parts/content con type tool-*/toolUse)."""
    return any(_container_has_tool(msg.get(key)) is True
               for key in ("parts", "content", "toolCalls"))

usage_state._link(globals())
