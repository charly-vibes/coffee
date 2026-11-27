#!/usr/bin/env python3
"""reduce_context: reducer puro de contexto por sesión (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

def reduce_context(entries, config=None, month=None):
    """Reducer puro de contexto por sesión de Pi (ctx-tracker, coffe-7gg).

    entries: iterable de dicts ya parseados de un JSONL de Pi (1 archivo =
    1 sesión). config: umbrales (DEFAULT_CONTEXT_THRESHOLDS como fallback;
    CLAR-002: nada hardcodeado — la config manda).

    Semántica (CORR-001): el acumulador de chars se RESETea en cada entrada
    type=="compaction" — los archivos de Pi retienen los mensajes previos a
    la compactación, así que peak_ctx es el máximo DENTRO del segmento de
    mayor uso, no del archivo completo.

    Atribución por fuente (suma == transcript observado, sin duplicar ni
    omitir roles): user_text, asst_text, asst_thinking, asst_args (argumentos
    de toolCall serializados) y tool:<toolName> por cada toolResult.

    Loss visible: turnos assistant sin usage (abortados) cuentan en
    turns_no_usage — nunca se silencian como 0 fake.

    Atribución por modelo (CORR-002, coffe-sht): models_turns es fuente de
    verdad (turno a turno, desde message.model o el último model_change);
    model = dominante por conteo. month viene del filename del archivo de
    sesión (el reducer no lo deriva): solo se emite si se pasa.
    """
    th = dict(DEFAULT_CONTEXT_THRESHOLDS)
    if config:
        th.update(config)
    acc = _CtxAcc()
    acc.th = th
    for entry in entries:
        acc.feed(entry)
    return _ctx_emit(acc, th, month)


class _CtxAcc:
    """Acumuladores de reduce_context (ctx-tracker, coffe-7gg)."""

    def __init__(self):
        self.turns = 0
        self.turns_no_usage = 0
        self.chars_by_source = {}
        self.big_results_by_tool = {}
        self.compactions = 0
        self.segment_count = 1
        self.seg_chars = 0          # acumulador del segmento actual
        self.peak_chars = 0         # máximo del acumulador dentro del segmento mayor
        self.max_assistant_block = 0  # chars del mayor bloque assistant individual (gp8)
        self.models_turns = {}
        self.cur_model = self.cur_provider = None
        self.cost_sums = {"input": 0.0, "output": 0.0, "cacheRead": 0.0,
                          "cacheWrite": 0.0}
        self.turns_with_cost = 0
        self.tokens_reported = 0     # suma de usage de la sesión (telemetría reportada)
        self.final_turn_tokens = 0   # usage del ÚLTIMO turno con usage (validación 1.7)
        self.th = {}

    def add(self, source, n):
        self.chars_by_source[source] = self.chars_by_source.get(source, 0) + n
        return self

    def feed(self, entry):
        etype = entry.get("type")
        if etype == "compaction":
            self.compactions += 1
            self.segment_count += 1
            self.seg_chars = 0  # reset del acumulador: segmento nuevo
        elif etype == "model_change":
            self.cur_provider = entry.get("provider")
            if entry.get("modelId"):
                self.cur_model = entry["modelId"]
        elif etype == "message":
            self.message(entry)
            if self.seg_chars > self.peak_chars:
                self.peak_chars = self.seg_chars  # máximo dentro del segmento actual
        return self

    def message(self, entry):
        msg = entry.get("message", {}) or {}
        role = msg.get("role")
        if role == "user":
            self.user_parts(msg)
        elif role == "assistant":
            self.assistant(msg)
        elif role == "toolResult":
            self.tool_result(msg)
        return self

    def user_parts(self, msg):
        for part in (msg.get("content") or []):
            if isinstance(part, dict) and part.get("type") == "text":
                n = len(part.get("text") or "")
                self.add("user_text", n)
                self.seg_chars += n
        return self

    def turn_model(self, msg):
        # modelo del turno: el campo propio del mensaje manda (los
        # archivos reales lo traen); fallback al último model_change
        turn_model = msg.get("model") or self.cur_model
        if turn_model:
            self.models_turns[turn_model] = self.models_turns.get(turn_model, 0) + 1
        if msg.get("provider"):
            self.cur_provider = msg["provider"]
        return self

    def turn_tokens(self, u):
        if isinstance(u.get("totalTokens"), (int, float)):
            return int(u["totalTokens"])
        if isinstance(u.get("input"), (int, float)):
            return int(u["input"] + (u.get("output") or 0)
                       + (u.get("cacheRead") or 0) + (u.get("cacheWrite") or 0))
        return 0

    def turn_cost(self, u):
        cost = u.get("cost")
        if not isinstance(cost, dict):
            return
        self.turns_with_cost += 1
        for k in self.cost_sums:
            v = cost.get(k)
            if isinstance(v, (int, float)):
                self.cost_sums[k] += v
        return self

    def assistant(self, msg):
        self.turns += 1
        self.turn_model(msg)
        u = msg.get("usage") or {}
        turn_tokens = self.turn_tokens(u)
        if turn_tokens:
            self.tokens_reported += turn_tokens
            self.final_turn_tokens = turn_tokens  # el último turno manda
        self.turn_cost(u)
        self.max_assistant_block = max(self.max_assistant_block,
                                       self.asst_parts(msg))
        if not msg.get("usage"):
            self.turns_no_usage += 1
        return self

    def asst_parts(self, msg):
        block_chars = 0
        for part in (msg.get("content") or []):
            if not isinstance(part, dict):
                continue
            n = self.asst_part(part)
            if n is None:
                continue
            block_chars += n
            self.seg_chars += n
        return block_chars

    def asst_part(self, part):
        """chars del part assistant, o None si no es text/thinking/toolCall."""
        ptype = part.get("type")
        if ptype == "text":
            n = len(part.get("text") or "")
            self.add("asst_text", n)
        elif ptype == "thinking":
            n = len(part.get("thinking") or part.get("text") or "")
            self.add("asst_thinking", n)
        elif ptype == "toolCall":
            n = len(json.dumps(part.get("arguments", {}),
                               ensure_ascii=False, sort_keys=True))
            self.add("asst_args", n)
        else:
            return None
        return n

    def tool_result(self, msg):
        name = str(msg.get("toolName") or "unknown")
        n = 0
        for part in (msg.get("content") or []):
            if isinstance(part, dict) and part.get("type") == "text":
                n += len(part.get("text") or "")
        self.add(f"tool:{name}", n)
        self.seg_chars += n
        if n > self.th["big_result_chars"]:
            self.big_results_by_tool[name] = self.big_results_by_tool.get(name, 0) + 1
        return self


def _ctx_costs(acc):
    # coste por sesión (coffe-5uu): ausente → null con razón (patrón
    # "sin telemetría", no 0 fake); parcial → suma de los turnos con coste
    if acc.turns_with_cost:
        return {"cost_in": round(acc.cost_sums["input"], 6),
                "cost_out": round(acc.cost_sums["output"], 6),
                "cost_cr": round(acc.cost_sums["cacheRead"], 6),
                "cost_cw": round(acc.cost_sums["cacheWrite"], 6)}
    return {"cost_in": None, "cost_out": None, "cost_cr": None,
            "cost_cw": None,
            "cost_null_reason": ("sin usage.cost en ningún turno (turnos "
                                 "abortados o proveedor sin billing por token)")}


def _ctx_emit(acc, th, month):
    cpt = th["chars_per_token"]
    dominant = None
    if acc.models_turns:
        dominant = max(sorted(acc.models_turns), key=lambda k: acc.models_turns[k])
    out = {
        "turns": acc.turns,
        "turns_no_usage": acc.turns_no_usage,
        "chars_by_source": acc.chars_by_source,
        "big_results": sum(acc.big_results_by_tool.values()),
        "big_results_by_tool": acc.big_results_by_tool,
        "compactions": acc.compactions,
        "segment_count": acc.segment_count,
        "peak_ctx_chars": acc.peak_chars,
        "final_ctx_chars": acc.seg_chars,
        "peak_ctx_est": acc.peak_chars // cpt,
        "final_ctx_est": acc.seg_chars // cpt,
        "max_assistant_block_chars": acc.max_assistant_block,
        "chars_per_token": cpt,
        "model": dominant,
        "models_turns": acc.models_turns,
        "provider": acc.cur_provider,
        "tokens_reported": acc.tokens_reported,
        "final_turn_tokens": acc.final_turn_tokens,
    }
    # patologías (coffe-gp8): bloque assistant > umbral de config → flag en
    # la fila (el emisor la excluye de percentiles y cuenta en
    # meta.excluded_pathological); toolResults grandes son big_results, no glitch
    out["pathological"] = acc.max_assistant_block > th["pathological_chars"]
    if month is not None:
        out["month"] = month
    out.update(_ctx_costs(acc))
    # context_source (coffe-5uu): claude-cli tiene usage inválido y coste de
    # suscripción — se emite separado, nunca mezclado en percentiles de coste
    out["context_source"] = ("claude-cli"
                             if acc.cur_provider == "claude-cli" else "pi")
    return out

usage_state._link(globals())
