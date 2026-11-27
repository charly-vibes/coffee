#!/usr/bin/env python3
"""
usage-tracker.py v4.2 — Extractor completo de uso de IA.
Filtra solo proyectos charly, incluye Amp, sesiones y patrones.
Corrige cálculo de costos: estima desde tokens × pricing para Claude JSONL,
fusiona cache de dashboard para costos precisos, suma cuotas de suscripción.
v4.1: cuenta tokens de cache (cacheRead/cacheWrite) de Pi y Claude
(validado contra toolpath/path-cli).
v4.2 (coffe-mbz): SUBSCRIPTIONS/MODEL_PRICING se cargan de config/fpa.json
(fuente de verdad F0, versionada por fecha efectiva); fallback a constantes
hardcodeadas si no hay config. Filtro charly → flag --filter {charly,all}.
v4.3 (coffe-snj): flags --since/--until (YYYY-MM-DD, extremos inclusive) para
corridas reproducibles: los extractores descartan eventos fuera de la ventana
(rows, kinds de user prompts y sesiones por solape); metadata.window registra
el pedido; el guard de MIN_INTERACTIONS se relaja a ≥1 cuando hay ventana
explícita (una ventana angosta legítimamente extrae poco). La ventana evalúa
fechas UTC (los timestamps de rows son ISO UTC).
v4.4 (coffe-n35): extract_amp unifica el filtro con in_scope (--filter all
ahora sí amplía Amp) y deriva el label de proyecto del uri (org-repo, p.ej.
"charly-coffe", vía amp_proj_from_uri); el "charly/amp-auto" fijo era un
label que mentía sobre el proyecto.
v4.5 (coffe-vp8): el scope incluye repos ak (akielbowicz) además de charly/sk
(is_charly → in_scope, alias por compat); metadata.filter pasa de
"charly-only" a "in-scope" (schema actualizado).
v4.6 (coffe-7mj.1): energía estimada por modelo — bloque energy_coefficients
en config (tiers J/token entregado versionados por effective, requerido y
fail-loud); emisión aditiva monthly.energy_kwh_by_model ({"kwh","tier"} o
null+"sin telemetría de tokens") + monthly.energy_kwh + metadata.energy_*.
La energía es SIEMPRE provenance "assumed": coeficientes de laboratorio
(Luccioni et al. / AI Energy Score), orden de magnitud, nunca medición;
jamás se mezcla con los costes USD (FPA-002).
v4.7 (epic add-context-dashboard, coffe-7gg/sht/5uu/gp8/64z/2xj/kup):
context_metrics por sesión de Pi — reducer puro reduce_context() (pico por
SEGMENTO de compactación: los archivos de Pi retienen los mensajes previos,
un pico continuo sobreestima el contexto post-compactación; atribución de
chars por fuente user_text/asst_text/asst_thinking/asst_args/tool:<name>;
models_turns para sesiones con model_change) + aggregate_context()
(monthly_model[] por mes×modelo dominante, set limpio: turns>0 sin
patológicas). Estimación de contexto: chars/chars_per_token (assumed, 4 —
SUBESTIMA ~25%, ver validation_ratio_median contra el usage del último
turno). Propósito: higiene de sesiones → presupuesto de contexto. Coste
ausente → null con razón (nunca 0 fake). Patologías (bloque assistant >
context_thresholds.pathological_chars de config) contadas, nunca
silenciosas. Pi-only: Claude/Amp/Codex fuera del alcance.
v4.8 (coffe-dnx.1): el mapeo modelo → (familia, versión) vive en
config/fpa.json (bloque model_mapping: lista de {priority, pattern,
family, version}; regex case-insensitive, gana el priority menor) y ya no
en una cadena de `if "x" in m` hardcodeada que pisaba precios y colapsaba
versiones (deepseek-v4-pro/v4.1-pro facturados a tarifa v4-flash, kimi k2.5
a k2, claude-opus-4.8 al fallback other, z-ai/glm-5.3-flash → other). El
fallback ("other", model_id[:20]) se conserva pero queda VISIBLE y CONTADO:
estimate_cost() lleva un contador default_rates_fallbacks y emite warning
a stderr (una vez por familia/versión) cuando una versión no tiene tarifa
en model_pricing (usa DEFAULT_RATES — provenance *assumed*); el reporte
emite metadata.model_mapping_coverage (tokens y filas por familia,
tokens_other_pct, default_rates_fallbacks contados por fila sin tarifa).
v4.9 (coffe-dnx.2): session_spend extiende cada fila con señales de
calidad y enrutamiento (mismo extractor, aditivo). Grupo A: duration_ms
{n,p50,p90} | null + duration_reason (nunca 0 fake; durationMs solo
existe en logs de Pi — cobertura medida en metadata.latency_coverage:
zai/minimax/qwen-token-plan/kimi 100%, openrouter 7,5%,
openai-codex/claude-cli/google-gemini-cli 0%), stop_reasons por categoría
(toolUse/stop/error/aborted/length del stopReason del turno) y
tool_errors {calls,errors,p50_ms} desde toolResult.durationMs+isError
(atribuidos al turno assistant que los invocó). Grupo B: model_chain
(modelos en orden de primera aparición) y model_switches (≥2
model_change ⇒ 1+; el PRIMER model_change fija el modelo inicial y NO
cuenta — el contador ingenuo reportaba 100% de switching porque 100% de
las sesiones traen el model_change inicial). Ambos: cache_share
(cache_read / tokens totales) y cost_provenance reported|estimated|mixed
("reported" SOLO si todos los turnos traen cost.totalFromProvider;
corpus real ~1,6%). Schema bump en specs/usage-report-v3.schema.json.
v4.10 (coffe-dnx.8): model_scorecard[] — scorecard de la cohorte
benchmark_cohort (config/fpa.json, consistente con config/model_eval.json,
coffe-dnx.9) por modelo × ventana (todo/YTD/mes).
cost_weighted_per_1m = cost / (input_fresh + output + cache_read ×
cache_factor) con cache_factor = cache_read_rate/input_rate del pricing
config (fallback default_rates con provenance assumed — NUNCA 1.0
silencioso: con 90% de cache_read el $/Mtok sobre tokens totales invierte
el ranking, CORR-003). Cruce coffe-dnx.3 (cost_per_task /
cost_per_accepted_task — aceptada = f sin bits aborted|error, proxy) y
coffe-dnx.2 (latencia, stop_reasons, aborts/tool_errors per 100 turns,
model_switches, subscription_vs_api). Toda cifra con provenance
reported|estimated|assumed; ausente → null + nulls[] con razón (FPA-008).
metadata.scorecard.rule_verdict = mantener|cambiar|datos_insuficientes
según model_decision_rule (umbrales provisional: true, EXCL-003);
datos_insuficientes si falta cost_per_accepted_task en la cohorte.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
PI_DIR = Path.home() / ".pi" / "agent"  # Gemini llega vía logs de Pi (google-gemini-cli), no hay extractor propio
AMP_DIR = Path.home() / ".amp"
CODEX_DIR = Path.home() / ".codex"  # coffe-2sy: rollouts del CLI standalone (sesiones/*/*/*/rollout-*.jsonl)
OMP_DIR = Path.home() / ".omp" / "agent" / "sessions"  # omp: <cwd-mungeado>/<ts>_<uuid>.jsonl
OUTPUT_DIR = Path("data")
LOCAL_TZ = datetime.now().astimezone().tzinfo  # OJO: los buckets hourly/daily usan la TZ local de la máquina que extrae

CHARLY_FILTER = True  # default del flag --filter (charly); main() lo setea desde args
SINCE = None  # date límite inferior (inclusive) de --since; None = sin límite
UNTIL = None  # date límite superior (inclusive) de --until; None = sin límite

# Umbral anti-clobber: si los extractores encuentran menos interacciones que esto
# (p.ej. máquina sin ~/.claude / ~/.pi/agent / ~/.amp), NO se escribe el reporte
# sin --force, para no destruir el dataset versionado en data/.
MIN_INTERACTIONS = 1000

# coffe-0c8 (antecedente coffe-jg3): dashboard-cache.json es fuente única de
# claude-cli para sesiones cuyo jsonl ya no existe, y Claude Code lo reescribe
# truncando turns viejos sin aviso. Guard fail-loud: si las interacciones
# claude-cli de un mes caen más de este % respecto al reporte vigente, abortar
# la regen en vez de pisar el dataset. Ajustable con TRACKER_MONTHLY_DROP_PCT.
DEFAULT_MONTHLY_DROP_PCT = 20.0

# =====================================================================
# Config (coffe-mbz): SUBSCRIPTIONS y MODEL_PRICING viven en
# config/fpa.json (fuente de verdad F0, pricing versionado por fecha
# efectiva). Las constantes de abajo son solo el FALLBACK para máquinas
# sin config — no editarlas para cambiar precios/suscripciones: editar
# config/fpa.json.
# =====================================================================

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parent

_FALLBACK_SUBSCRIPTIONS = {
    "claude-cli": [
        {"start": "2026-03-19", "end": "2026-04-19", "label": "Max $100/mes", "monthly_fee": 100},
        {"start": "2026-04-19", "end": "2026-05-19", "label": "Max $100/mes", "monthly_fee": 100},
        {"start": "2026-05-19", "end": "2026-06-19", "label": "Pro $20/mes", "monthly_fee": 20},
    ],
    "codex": [
        {"start": "2026-04-02", "end": "2026-05-02", "label": "ChatGPT Plus $20/mes", "monthly_fee": 20},
        {"start": "2026-05-02", "end": "2026-06-02", "label": "ChatGPT Plus $20/mes", "monthly_fee": 20},
    ],
    "gemini-cli": [
        {"start": "2025-12-03", "end": "2026-06-03", "label": "Google AI Pro $19.99/mes", "monthly_fee": 19.99},
    ],
}

# Model pricing per million tokens (pay-per-token rates) — FALLBACK
_FALLBACK_MODEL_PRICING = {
    "claude": {
        "opus-4.7":   {"input": 0.000015, "output": 0.000075, "cache_read": 0.0000015},
        "opus-4.6":   {"input": 0.000015, "output": 0.000075, "cache_read": 0.0000015},
        "opus-4.5":   {"input": 0.000015, "output": 0.000075, "cache_read": 0.0000015},
        "sonnet-4.6": {"input": 0.000003, "output": 0.000015, "cache_read": 0.0000003},
        "haiku":      {"input": 0.00000025, "output": 0.00000125, "cache_read": 0.000000025},
        "synthetic":  {"input": 0.000003, "output": 0.000015, "cache_read": 0.0000003},
    },
    "codex": {
        "gpt-5.5": {"input": 0.000010, "output": 0.000040, "cache_read": 0.0000010},
        "gpt-5.4": {"input": 0.000010, "output": 0.000040, "cache_read": 0.0000010},
        "gpt-5.3": {"input": 0.000010, "output": 0.000040, "cache_read": 0.0000010},
    },
    "gemini": {
        "gemini-3.1":    {"input": 0.00000125, "output": 0.000005, "cache_read": 0.0000000625},
        "gemini-3-pro":  {"input": 0.00000125, "output": 0.000005, "cache_read": 0.0000000625},
        "gemini-2.5":    {"input": 0.000000625, "output": 0.0000025, "cache_read": 0.00000003125},
    },
    "deepseek": {
        "v4-flash": {"input": 0.0000003, "output": 0.0000012, "cache_read": 0.00000003},
    },
    "kimi": {
        "k2": {"input": 0.000002, "output": 0.000008, "cache_read": 0.0000002},
    },
}

_FALLBACK_DEFAULT_RATES = {"input": 0.000003, "output": 0.000015, "cache_read": 0.0000003}

# coffe-dnx.1: mapeo modelo → (familia, versión) — FALLBACK (máquinas sin
# config). Misma cadena de patrones que model_details() tenía hardcodeada
# antes de v4.8; en config/fpa.json (bloque model_mapping) vive la fuente
# de verdad. Orden = prioridad (primera coincidencia gana); sin match →
# ("other", model_id[:20]) — conservado y contado.
_FALLBACK_MODEL_MAPPING = [
    {"priority": 10, "pattern": ":free$", "family": "openrouter", "version": "free"},
    {"priority": 20, "pattern": "claude-opus-4[.-]8", "family": "claude", "version": "opus-4.8"},
    {"priority": 21, "pattern": "claude-opus-4[.-]7", "family": "claude", "version": "opus-4.7"},
    {"priority": 22, "pattern": "claude-opus-4[.-]6", "family": "claude", "version": "opus-4.6"},
    {"priority": 23, "pattern": "claude-opus-4[.-]5", "family": "claude", "version": "opus-4.5"},
    {"priority": 24, "pattern": "claude-sonnet-4[.-]6", "family": "claude", "version": "sonnet-4.6"},
    {"priority": 25, "pattern": "claude-haiku", "family": "claude", "version": "haiku"},
    {"priority": 26, "pattern": "synthetic", "family": "claude", "version": "synthetic"},
    {"priority": 30, "pattern": "gpt-6\\.1", "family": "codex", "version": "gpt-6.1"},
    {"priority": 31, "pattern": "gpt-6", "family": "codex", "version": "gpt-6"},
    {"priority": 32, "pattern": "gpt-5\\.5", "family": "codex", "version": "gpt-5.5"},
    {"priority": 33, "pattern": "gpt-5\\.4", "family": "codex", "version": "gpt-5.4"},
    {"priority": 34, "pattern": "gpt-5\\.3", "family": "codex", "version": "gpt-5.3"},
    {"priority": 40, "pattern": "gemini-3\\.1", "family": "gemini", "version": "gemini-3.1"},
    {"priority": 41, "pattern": "gemini-3[-.]", "family": "gemini", "version": "gemini-3-pro"},
    {"priority": 42, "pattern": "gemini-2\\.5", "family": "gemini", "version": "gemini-2.5"},
    {"priority": 50, "pattern": "deepseek-v4\\.1-pro", "family": "deepseek", "version": "v4.1-pro"},
    {"priority": 51, "pattern": "deepseek-v4-pro", "family": "deepseek", "version": "v4-pro"},
    {"priority": 52, "pattern": "deepseek-v4-flash", "family": "deepseek", "version": "v4-flash"},
    {"priority": 53, "pattern": "deepseek", "family": "deepseek", "version": "v4-flash"},
    {"priority": 60, "pattern": "kimi.*k2\\.5", "family": "kimi", "version": "k2.5"},
    {"priority": 61, "pattern": "k2\\.5", "family": "kimi", "version": "k2.5"},
    {"priority": 62, "pattern": "kimi", "family": "kimi", "version": "k2"},
    {"priority": 70, "pattern": "z-ai/glm-5\\.3-flash", "family": "glm", "version": "glm-5.3-flash"},
    {"priority": 71, "pattern": "glm-5\\.3-flash", "family": "glm", "version": "glm-5.3-flash"},
    {"priority": 80, "pattern": "minimax-m3\\.1", "family": "minimax", "version": "m3.1-flash"},
    {"priority": 81, "pattern": "minimax-m2\\.7", "family": "minimax", "version": "m2.7"},
    {"priority": 90, "pattern": "qwen3\\.8-max", "family": "qwen", "version": "qwen3.8-max"},
    {"priority": 91, "pattern": "qwen3\\.8-flash", "family": "qwen", "version": "qwen3.8-flash"},
    {"priority": 100, "pattern": "devstral", "family": "mistral", "version": "devstral"},
    {"priority": 101, "pattern": "mistral", "family": "mistral", "version": "mistral"},
]

# coffe-dnx.1: contador de usos de DEFAULT_RATES (versión sin tarifa en
# model_pricing). Clave familia → {versión: n llamados a estimate_cost()}
# (defaultdict(Counter), mismo shape anidado que devuelve
# default_rates_fallbacks()). El reporte cuenta fallbacks POR FILA
# (determinista, ver _model_mapping_coverage); este contador alimenta el
# warning a stderr y los tests. Reset explícito con
# reset_default_rates_fallbacks().
_DEFAULT_RATES_FALLBACKS = defaultdict(Counter)


def reset_default_rates_fallbacks():
    """coffe-dnx.1: reinicia el contador de fallbacks a default_rates."""
    _DEFAULT_RATES_FALLBACKS.clear()
    return _DEFAULT_RATES_FALLBACKS


def default_rates_fallbacks():
    """coffe-dnx.1: snapshot {familia: {versión: n}} del contador."""
    return {fam: dict(vers) for fam, vers in _DEFAULT_RATES_FALLBACKS.items()}

# Ledger de cargos reales (coffe-a31.2, CRG-F1): facturas transcriptas,
# provenance *reported* (FPA-003). cash cost = estos cargos; los fees
# implícitos del calendario NO son cash (solo reconciliación FPA-082 y
# plan economy). Efectivo (tracker) y cash (ledger) jamás se suman (FPA-002).
CHARGES_PATH = _REPO_ROOT / "data" / "charges.json"
CHARGES_SOURCE = "data/charges.json"
CHARGES_KINDS_IA = {"subscription", "credits", "refund", "api_cycle"}
# Kinds del ledger que son cargas pay-per-token (FPA-013): créditos y
# reembolsos, NO las cuotas de suscripción (esas son del calendario).
CHARGES_KINDS_P2P = {"credits", "refund"}


def _ledger_date_ok(d):
    """Fecha ISO del ledger (YYYY-MM-DD) — chequeo estructural rápido."""
    return (isinstance(d, str) and len(d) == 10
            and d[4] == "-" and d[7] == "-"
            and d[:4].isdigit() and d[5:7].isdigit() and d[8:].isdigit())


def _charge_entry_error(provider, i, e):
    """Valida una entrada del ledger; devuelve el mensaje de error o None."""
    kind = e.get("kind")
    if kind not in CHARGES_KINDS_IA:
        return f"ledger: {provider}[{i}] kind fuera del vocabulario IA: {kind!r}"
    amount = e.get("amount")
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        return f"ledger: {provider}[{i}] amount no numérico"
    if not _ledger_date_ok(e.get("date")):
        return f"ledger: {provider}[{i}] date inválida: {e.get('date')!r}"
    return None


def _apply_charge(by_month, p2p, provider, e):
    """Agrega un cargo al mes correspondiente; devuelve el amount (cash)."""
    mes = e["date"][:7]
    amount = e["amount"]
    by_month.setdefault(provider, {}).setdefault(mes, 0.0)
    # redondeo a centavos: transcripción manual, dinero real
    by_month[provider][mes] = round(by_month[provider][mes] + amount, 2)
    if e.get("kind") in CHARGES_KINDS_P2P:
        p2p.setdefault(provider, {}).setdefault(mes, 0.0)
        p2p[provider][mes] = round(p2p[provider][mes] + amount, 2)
    return amount


def cargar_charges(path=None):
    """coffe-a31.2: cargar data/charges.json y agregar por mes (YYYY-MM).

    Devuelve (charges_real_by_month, total, p2p_by_month): {provider:
    {YYYY-MM: amount}} con TODOS los kinds del vocabulario IA (api_cycle
    aporta $0 y así queda en la serie), la suma total IA y la serie p2p
    (credits/refund — FPA-013). Falla loud ante ledger roto (kind fuera
    del vocabulario, fecha inválida, amount no numérico): el ledger es
    transcripción manual — un error debe cortar, no colarse al dashboard.
    """
    path = Path(path) if path else CHARGES_PATH
    ledger = json.loads(path.read_text())
    by_month = {}
    p2p = {}  # solo kinds pay-per-token (credits/refund) — FPA-013
    total = 0.0
    for provider, entries in (ledger.get("providers") or {}).items():
        if not isinstance(entries, list):
            raise ValueError(f"ledger: providers.{provider} no es una lista")
        for i, e in enumerate(entries):
            err = _charge_entry_error(provider, i, e)
            if err is not None:
                raise ValueError(err)
            total = round(total + _apply_charge(by_month, p2p, provider, e), 2)
    return by_month, total, p2p


def _recon_tools(charges_by_month, monthly_dicts):
    tools = set(SUBSCRIPTIONS) | set(charges_by_month)
    # tools con interacciones del tracker también entran (su efectivo importa)
    for mo in monthly_dicts.values():
        tools.update((mo.get("tools") or {}))
    return tools


def _recon_months(charges_by_month, monthly_dicts):
    # meses = los del tracker ∪ los del ledger: un mes con facturas pero sin
    # interacciones (p.ej. recargas openrouter de jul-2026) también reconcilia
    return sorted(set(monthly_dicts) | {
        m for meses_prov in charges_by_month.values() for m in meses_prov})


def _fee_month_total(mes):
    """Fee total del mes por calendario, incluso sin interacciones."""
    return round(sum(_implicit_fee_tool_mes(t, mes) for t in SUBSCRIPTIONS), 8)


def _recon_row(tool, mes, mo, ctx):
    charges_by_month, fee_cal = ctx
    return {
        "charges_real": round(float(charges_by_month.get(tool, {}).get(mes, 0.0)), 8),
        "charges_provenance": "reported",
        "subscription_fee_implicit": _implicit_fee_tool_mes(tool, mes),
        "cost_effective": round(float(
            (mo.get("tools") or {}).get(tool, {}).get("cost_effective", 0.0)), 8),
        "fee_month_total": fee_cal,
    }


def reconciliation_charges(charges_by_month, monthly_dicts):
    """coffe-a31.2: insumo de reconciliación FPA-082, por tool y mes.

    Tres medidas separadas (jamás sumadas entre sí, FPA-002):
    - charges_real: cash pagado según el ledger (*reported*);
    - subscription_fee_implicit: cuota que el calendario de suscripciones
      atribuye al tool-mes (*assumed*) — no es cash, solo reconciliación y
      plan economy;
    - cost_effective: estimado API-equivalente del tracker (*assumed*).

    Proveedores sin cargos en un mes SIEMPRE aparecen (charges_real 0.0,
    nunca vacío — FPA-008): la ausencia de factura es un dato, no un hueco.
    """
    tools = _recon_tools(charges_by_month, monthly_dicts)
    rec = defaultdict(dict)
    for mes in _recon_months(charges_by_month, monthly_dicts):
        mo = monthly_dicts.get(mes, {})
        ctx = (charges_by_month, _fee_month_total(mes))
        for tool in sorted(tools | set(mo.get("tools") or {})):
            rec[mes][tool] = _recon_row(tool, mes, mo, ctx)
    return dict(rec)


def _implicit_fee_tool_mes(tool, mes):
    """Cuota implícita del calendario para (tool, mes): suma de los fees de
    los periodos cuyo rango [start, end) solapa el mes. Complemento por
    tool de calc_subscription_fees (que solo emite el total del mes)."""
    total = 0.0
    for period in SUBSCRIPTIONS.get(tool, []):
        fee = period.get("monthly_fee", 0)
        if fee > 0:
            p_start = period["start"]
            p_end = period["end"] or "9999-12"
            if mes >= p_start[:7] and mes < p_end[:7]:
                total += fee
    return total


def _import_fpa_config():
    """Importar scripts/fpa_config.py funcionando tanto como script (sys.path
    incluye el dir del script) como módulo cargado por ruta (tests)."""
    try:
        import fpa_config
        return fpa_config
    except ImportError:
        pass
    spec = importlib.util.spec_from_file_location("fpa_config", _HERE / "fpa_config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fpa_config = _import_fpa_config()


def _compile_model_mapping(entries):
    """coffe-dnx.1: compila el bloque model_mapping de config → [(regex,
    family, version)] ordenado por priority (menor = mayor precedencia).

    Fail loud ante entradas malformadas (sin pattern/family/version) o
    patterns que no compilan: un mapeo roto clasificaría todo a "other"
    en silencio. Vía fallback (config ausente) usa la lista hardcodeada.
    """
    if not isinstance(entries, list) or not entries:
        raise ValueError(
            "model_mapping.entries debe ser una lista no vacía de "
            "{priority, pattern, family, version}")
    compiled = []
    for i, e in enumerate(entries):
        try:
            pat, fam, ver = e["pattern"], e["family"], e["version"]
            prio = e.get("priority", i)
        except (TypeError, KeyError) as exc:
            raise ValueError(
                f"model_mapping.entries[{i}] malformada (requiere "
                "pattern/family/version)") from exc
        try:
            compiled.append((re.compile(pat, re.IGNORECASE), fam, ver, prio))
        except re.error as exc:
            raise ValueError(
                f"model_mapping.entries[{i}]: regex inválida {pat!r}: {exc}") from exc
    compiled.sort(key=lambda t: t[3])
    return [(rx, fam, ver) for rx, fam, ver, _ in compiled]


def _locate_fpa_config(explicit):
    """Devuelve (source|None, cfg|None) según el orden de resolución."""
    if explicit:
        return explicit, fpa_config.load_fpa_config(explicit)  # FileNotFoundError si no existe
    for cand in (Path("config") / "fpa.json", _REPO_ROOT / "config" / "fpa.json"):
        if cand.exists():
            return cand, fpa_config.load_fpa_config(cand)
    return None, None


def _apply_fallback_config():
    """Sin config: constantes hardcodeadas + warning en stderr."""
    global SUBSCRIPTIONS, MODEL_PRICING, DEFAULT_RATES, _CACHE_WRITE_FACTOR
    global _FPA_CONFIG, _PRICING_VERSIONS, _MODEL_MAPPING, _ENERGY_COEFFS
    print(
        "WARNING: config/fpa.json no encontrado — usando SUBSCRIPTIONS/"
        "MODEL_PRICING hardcodeadas (fallback; ignorá este warning solo "
        "en máquinas sin el repo)",
        file=sys.stderr,
    )
    SUBSCRIPTIONS = _FALLBACK_SUBSCRIPTIONS
    MODEL_PRICING = _FALLBACK_MODEL_PRICING
    DEFAULT_RATES = _FALLBACK_DEFAULT_RATES
    _MODEL_MAPPING = _compile_model_mapping(_FALLBACK_MODEL_MAPPING)
    _CACHE_WRITE_FACTOR = 1.25
    _PRICING_VERSIONS = [{"effective": None, "rates": MODEL_PRICING}]
    _FPA_CONFIG = None
    _ENERGY_COEFFS = None
    return None


def _apply_fpa_config(cfg, source):
    """Valida y aplica el config F0 a los globals del módulo."""
    global SUBSCRIPTIONS, MODEL_PRICING, DEFAULT_RATES, _CACHE_WRITE_FACTOR
    global _FPA_CONFIG, _PRICING_VERSIONS, _MODEL_MAPPING, _ENERGY_COEFFS
    errors = fpa_config.validate_config(cfg)
    if errors:
        raise ValueError(f"config inválido ({source}):\n" + "\n".join(errors))
    _FPA_CONFIG = cfg
    pricing = cfg["model_pricing"]
    SUBSCRIPTIONS = cfg["subscriptions"]
    DEFAULT_RATES = pricing["default_rates"]
    # coffe-dnx.1: mapeo modelo → familia/versión desde config (orden =
    # prioridad explícita; sin bloque o vacío → fallback hardcodeado).
    _MODEL_MAPPING = _compile_model_mapping(
        cfg.get("model_mapping", {}).get("entries") or _FALLBACK_MODEL_MAPPING)
    _CACHE_WRITE_FACTOR = pricing.get("cache_write_factor", 1.25)
    _PRICING_VERSIONS = pricing.get("versions", [])
    # coffe-7mj.1: coeficientes de energía (bloque requerido, validado arriba
    # por validate_config → ValueError si falta o está malformado)
    _ENERGY_COEFFS = cfg["energy_coefficients"]
    # Compat: rates de la última versión conocida (los extractores piden
    # rates por fecha vía estimate_cost(..., when=...))
    if _PRICING_VERSIONS:
        MODEL_PRICING = max(_PRICING_VERSIONS, key=lambda v: v["effective"])["rates"]
    else:
        MODEL_PRICING = {}
    return cfg


def load_config(path=None):
    """coffe-mbz: cargar SUBSCRIPTIONS/MODEL_PRICING desde config/fpa.json.

    Resolución (en orden):
    1. `path` explícito (flag --config): si no existe → FileNotFoundError.
    2. TRACKER_CONFIG (env): ídem.
    3. config/fpa.json relativo al CWD, luego al repo (si no hay ninguno:
       fallback a constantes hardcodeadas + warning en stderr).

    Con config se valida (validate_config); errores → ValueError (fail loud,
    nunca fallback silencioso). Setea los globals del módulo y devuelve el
    config (None si fallback).
    """
    global CONFIG_SOURCE

    explicit = path or os.environ.get("TRACKER_CONFIG")
    CONFIG_SOURCE = None
    source, cfg = _locate_fpa_config(explicit)
    if source is not None:
        CONFIG_SOURCE = str(source)
    if cfg is None:
        return _apply_fallback_config()
    return _apply_fpa_config(cfg, CONFIG_SOURCE)


load_config()


def _pricing_for(when=None):
    """{family: {model: rates}} vigentes para `when` (date) — FPA-016.

    when=None → la última versión conocida (determinista para tests y
    llamados sin fecha); con fecha, la última con effective <= when, o
    default_rates si es anterior a todas.
    """
    if _FPA_CONFIG is not None:
        if when is not None:
            return fpa_config.rates_for(_FPA_CONFIG, when)
        if _PRICING_VERSIONS:
            return max(_PRICING_VERSIONS, key=lambda v: v["effective"])["rates"]
    return MODEL_PRICING


def _warn_default_rates(family, version):
    """coffe-dnx.1: warning stderr una vez por familia/versión (run ruidosa
    con 15k llamadas no escupe 15k warnings)."""
    cfg_src = CONFIG_SOURCE or "builtin-fallback"
    print(
        f"WARNING: default_rates fallback — {family}/{version} no "
        f"tiene tarifa en model_pricing (config {cfg_src}); coste "
        f"estimado a default_rates ({DEFAULT_RATES}) — "
        "provenance *assumed*",
        file=sys.stderr,
    )


def estimate_cost(*args, **kw):
    """Estimate cost from token counts at pay-per-token rates.

    `cache_read`/`cache_write` pueden pasarse por posición (quinto/sexto
    argumento) o por keyword; `when` es la fecha de la interacción (date)
    para pricing versionado por fecha efectiva (FPA-016); None → última
    versión conocida.
    """
    family, version, input_tokens, output_tokens = args[:4]
    cache_read = args[4] if len(args) > 4 else kw.get("cache_read", 0)
    cache_write = args[5] if len(args) > 5 else kw.get("cache_write", 0)
    return _estimate_cost_from(
        (input_tokens, output_tokens, cache_read, cache_write),
        (family, version), kw.get("when"))


def _estimate_cost_from(usage, model, when):
    """usage = (input_tokens, output_tokens, cache_read, cache_write);
    model = (family, version)."""
    input_tokens, output_tokens, cache_read, cache_write = usage
    family, version = model
    rates = _pricing_for(when).get(family, {}).get(version)
    if rates is None:
        # coffe-dnx.1: versión sin tarifa → DEFAULT_RATES, pero ya no en
        # silencio: contador + warning stderr (una vez por familia/versión).
        _DEFAULT_RATES_FALLBACKS[family][version] += 1
        if _DEFAULT_RATES_FALLBACKS[family][version] == 1:
            _warn_default_rates(family, version)
        rates = DEFAULT_RATES
    # Anthropic cobra cache writes a _CACHE_WRITE_FACTOR x del input rate
    input_cost = (input_tokens + cache_write * _CACHE_WRITE_FACTOR) * rates["input"]
    cache_read_cost = cache_read * rates.get("cache_read", rates["input"] * 0.1)
    output_cost = output_tokens * rates["output"]
    return round(input_cost + cache_read_cost + output_cost, 8)


def _energy_kwh(tok, j_per_token, factor):
    """coffe-7mj.1: kWh estimado de un modelo con tokens (3 decimales).

    (input + output + cache_write a peso completo — escribir KV es cómputo
    de prefill — + cache_read × factor) × J/token del tier / 3.6e6. Los
    coeficientes J/token son estimaciones de laboratorio (Luccioni et al. /
    AI Energy Score): la energía es siempre provenance "assumed".
    """
    fresh = tok["in"] + tok["out"] + tok["cache_write"]
    return round((fresh + tok["cache_read"] * factor) * j_per_token / 3.6e6, 3)


def _energy_for_month(monthly_dicts):
    """coffe-7mj.1: emisión aditiva de energía mensual por modelo.

    - Selección de versión de coeficientes por PRIMER DÍA del bucket
      mensual (tokens_by_model es agregado mensual: no se puede partir un
      mes entre versiones, a diferencia de estimate_cost(when) que es por
      interacción). Mes sin cobertura → ValueError loud nombrando el mes.
    - Modelo con interacciones pero tokens en cero (amp, <synthetic>) →
      {"kwh": null, "reason": "sin telemetría de tokens"} — nunca 0,
      que sugeriría medición (FPA-008).
    - total energy_kwh = suma de modelos con datos.
    - coffe-5ng: energy_kwh_band {low, high} = mismo cálculo con cache
      factor 0.0 y 1.0 (los extremos de la banda de sensibilidad: 0% y
      100% de acierto de caché; el nominal usa el factor del config).
      Modelos sin telemetría quedan fuera de ambos extremos, igual que
      fuera del total nominal.
    """
    if _FPA_CONFIG is None:
        return monthly_dicts  # fallback sin config: metadata.energy_* registra la razón
    energy_cfg = _ENERGY_COEFFS
    factor = energy_cfg["cache_read_energy_factor"]
    default_tier = energy_cfg["default_tier"]
    for m_key, mo in monthly_dicts.items():
        first_day = date.fromisoformat(m_key + "-01")
        version = fpa_config.energy_for(_FPA_CONFIG, first_day)
        if version is None:
            effs = [v["effective"] for v in energy_cfg.get("versions", [])]
            raise ValueError(
                f"energy_coefficients: mes {m_key} sin cobertura "
                f"(primer effective declarado: {min(effs) if effs else 'ninguna'}) "
                "— el config debe cubrir el rango de datos")
        tiers = version["tiers"]
        mapping = version.get("model_tiers", {})
        by_model = {}
        total = 0.0
        band_low = 0.0
        band_high = 0.0
        for model, tok in mo["tokens_by_model"].items():
            if not any((tok["in"], tok["out"], tok["cache_read"],
                        tok["cache_write"])):
                by_model[model] = {"kwh": None,
                                   "reason": "sin telemetría de tokens"}
                continue
            tier = mapping.get(model, default_tier)
            kwh = _energy_kwh(tok, tiers[tier], factor)
            total += kwh
            band_low += round((tok["in"] + tok["out"] + tok["cache_write"])
                              * tiers[tier] / 3.6e6, 3)
            band_high += round((tok["in"] + tok["out"] + tok["cache_write"]
                                + tok["cache_read"]) * tiers[tier] / 3.6e6, 3)
            by_model[model] = {"kwh": kwh, "tier": tier}
        mo["energy_kwh_by_model"] = by_model
        mo["energy_kwh"] = round(total, 3)
        mo["energy_kwh_band"] = {"low": round(band_low, 3),
                                 "high": round(band_high, 3)}
    return monthly_dicts


def parse_ts(ts):
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    return None

def hour_key(dt):
    return dt.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:00")


def parse_window(since=None, until=None):
    """coffe-snj: valida --since/--until (YYYY-MM-DD, extremos inclusive).

    Devuelve (date|None, date|None). ValueError si el formato es inválido
    o since > until (falla loud, nunca ventana silenciosamente invertida).
    """
    def _d(s, flag):
        if not s:
            return None
        try:
            return date.fromisoformat(s)
        except ValueError:
            raise ValueError(
                f"--{flag} inválido: {s!r} (esperado YYYY-MM-DD)") from None
    sd, ud = _d(since, "since"), _d(until, "until")
    if sd and ud and sd > ud:
        raise ValueError(f"--since {sd} es posterior a --until {ud}")
    return sd, ud


def in_window(dt):
    """True si dt cae en la ventana SINCE/UNTIL (extremos inclusive).

    Acepta datetime o date; evalúa la fecha tal cual (los extractores
    producen timestamps UTC-aware, así que la ventana es por fecha UTC).
    Sin ventana activa → True. dt None → True (los extractores ya descartan
    filas sin fecha antes de llegar acá)."""
    if dt is None:
        return True
    d = dt.date() if isinstance(dt, datetime) else dt
    if SINCE and d < SINCE:
        return False
    if UNTIL and d > UNTIL:
        return False
    return True


def _session_overlaps(s, since, until):
    first = (s.get("first_ts") or "")[:10]
    last = (s.get("last_ts_full") or s.get("first_ts") or "")[:10]
    if since and last and last < since.isoformat():
        return False
    if until and first and first > until.isoformat():
        return False
    return True


def filter_sessions(sessions, since=None, until=None):
    """Sesiones que se solapan con [since, until] (fechas, inclusive).

    Una sesión que empieza antes de `since` pero termina dentro cuenta
    (su trabajo parcial está en la ventana); sin ventana → lista completa.
    Sesiones sin fechas no se descartan (los extractores ya filtran las
    filas por fecha; una sesión sin fecha no se pierde en silencio)."""
    if not since and not until:
        return list(sessions)
    return [s for s in sessions if _session_overlaps(s, since, until)]

_SCOPE_ORGS = ("charly", "sk", "ak")
_SCOPE_SEPS = ("-gh-", "/gh/")


def _label_in_scope(p):
    return any(p == org or p.startswith(org + "-") or p.startswith(org + "/")
               for org in _SCOPE_ORGS)


def _path_org_in_scope(p, sep, org):
    # el org debe ser un segmento completo: seguido de '-', '/' o fin
    # (cubre path mungeado '-gh-charly-coffee' y uri '…/gh/charly/coffe')
    i = p.find(sep + org)
    while i != -1:
        j = i + len(sep) + len(org)
        if j >= len(p) or p[j] in "-/":
            return True
        i = p.find(sep + org, i + 1)
    return False


def in_scope(proj):
    """Scope del reporte cuando --filter charly (default): proyectos de los
    tres orgs — charly (charly-vibes), sk (sashakile) y ak (akielbowicz,
    coffe-vp8). Con --filter all entra todo.

    `proj` puede venir en dos formas: label limpio ("ak-journal", post
    clean_proj_name) o path mungeado de Claude ("-var-home-sasha-para-
    areas-dev-gh-charly-coffee"), por eso el match es por prefijo de label
    O por segmento de org en el path ("-gh-<org>-", "/gh/<org>/").
    """
    if not CHARLY_FILTER:
        return True
    p = (proj or "").lower()
    if _label_in_scope(p):
        return True
    return any(_path_org_in_scope(p, sep, org)
               for sep in _SCOPE_SEPS for org in _SCOPE_ORGS)


# Alias histórico: el nombre viejo mentía (también filtra ak/sk desde v4.5).
is_charly = in_scope

def model_details(model_id):
    """coffe-dnx.1: modelo → (familia, versión) desde config/fpa.json
    (bloque model_mapping; orden por priority, primera coincidencia gana).

    Sin match → ("other", model_id[:20]): fallback conservado (no se borra,
    solo queda visible y contado en metadata.model_mapping_coverage).
    """
    m = model_id.lower()
    for rx, family, version in _MODEL_MAPPING:
        if rx.search(m):
            return (family, version)
    return ("other", model_id[:20])


def clean_proj_name(raw):
    """Normaliza nombres de proyecto a forma canónica entre fuentes.

    Claude usa paths con '/' reemplazado por '-' (p.ej. el repo en
    <home>/para/areas/dev/gh/charly/coffe aparece como
    '-var-home-sasha-para-areas-dev-gh-charly-coffe'). El prefijo se deriva
    de Path.home() en runtime para ser independiente de la máquina.
    Pi usa nombres de directorio distintos ('-charly-atril/',
    '-sk-REPLy.jl/' → 'sk-REPLy-jl').
    """
    home_prefix = str(Path.home()).replace("/", "-") + "-para-areas-dev-gh-"
    p = raw.replace(home_prefix, "")
    p = p.strip("-/")
    p = p.replace(".jl", "-jl")  # repos Julia: REPLy.jl → REPLy-jl
    return {"charly-mibilioteca": "charly-miblioteca",  # typo en sesiones Pi
            "sk-sxAct": "sk-XAct-jl",  # sxAct no existe; repo real XAct.jl
            # coffe-vp8: Claude mungea puntos del path a '-', Pi no — un repo,
            # un label.
            "ak-akielbowicz-github-io": "ak-akielbowicz.github.io",
            # coffe-vp8: uri de Amp apuntaba a un archivo en la raíz del org
            # (gh/ak/justfile), no a un repo — el label correcto es la org.
            "ak-justfile": "ak",
            # repo renombrado coffe→coffee (bd coffe-85z): los logs históricos
            # derivan 'charly-coffe' del path viejo; se canonicaliza al nombre
            # corregido para que todo el dataset use un solo label.
            "charly-coffe": "charly-coffee"}.get(p, p)


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

    def model_change(self, entry):
        self.mc_count += 1
        mid = entry.get("modelId")
        if mid and mid not in self.model_chain:
            self.model_chain.append(mid)

    def track_model(self, model):
        if model not in self.model_chain:
            self.model_chain.append(model)

    def reset_pending(self):
        self.calls = self.errors = 0
        self.durs = []

    def flush_tail(self):
        # toolResults de cola (tras el último turno assistant, p.ej.
        # sesión abortada): se atribuyen al último turno emitido
        row = self.last_row
        if row is not None and (self.calls or self.errors or self.durs):
            row["tool_calls"] += self.calls
            row["tool_errors"] += self.errors
            row["tool_durations_ms"] += self.durs


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


def amp_proj_from_uri(uri):
    """Deriva el proyecto (org-repo) desde el uri file:// de Amp (coffe-n35).

    El proyecto canónico es el repo bajo para/areas/dev/gh/<org>/<repo>,
    nombrado como clean_proj_name ("charly-coffee", "sk-REPLy-jl") para que
    la taxonomía y project_daily lo crucen con las demás fuentes.
    Devuelve None si el uri no es derivable.
    """
    path = uri or ""
    if path.startswith("file://"):
        path = path[len("file://"):]
    marker = "/para/areas/dev/gh/"
    if marker not in path:
        return None
    rest = path.split(marker, 1)[1].strip("/").split("/")
    if len(rest) >= 2 and rest[1]:
        return clean_proj_name(f"{rest[0]}-{rest[1]}")
    return clean_proj_name(rest[0]) if rest and rest[0] else None


def _amp_row(ts, proj, session):
    return {
        "source": "amp", "tool": "amp",
        "model_raw": "amp", "model_family": "amp", "model_version": "v1",
        "project": proj,
        "timestamp": ts.isoformat(), "hour": hour_key(ts),
        "input_tokens": 0, "output_tokens": 0,
        "cache_read_tokens": 0, "cache_write_tokens": 0,
        "cost_effective": 0,
        "session_file": session,  # add-session-spend-report (hilo amp = sesión)
    }


def _amp_file_hit(f):
    """(ts, proyecto derivado) por archivo tocado, o None si fuera de
    scope/ventana o ilegible."""
    try:
        entry = json.loads(f.read_text())
    except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
        _debug_skip(f"amp: {e}")
        return None
    uri = entry.get("uri", "")
    if not is_charly(uri):  # coffe-n35: unifica con --filter
        return None
    ts = parse_ts(entry.get("timestamp"))
    if ts and in_window(ts):  # coffe-snj: ventana
        return (ts, amp_proj_from_uri(uri) or "amp-unknown")
    return None


def extract_amp():
    """Extrae de Amp (@ampcode/cli, agente autónomo). Sin costo.

    coffe-n35: el filtro es is_charly(uri) (respeta --filter) y el label
    es el proyecto derivado del uri, no "charly/amp-auto" fijo.
    """
    rows = []
    amp_dir = AMP_DIR / "file-changes"
    if not amp_dir.exists():
        return rows
    for td in amp_dir.iterdir():
        if not td.is_dir():
            continue
        hits = [h for h in (_amp_file_hit(f) for f in td.iterdir()) if h]
        for ts, proj in hits:
            rows.append(_amp_row(ts, proj, td.name))
    return rows


def _codex_rollout_file(f, rows, skipped):
    try:
        _parse_codex_rollout(f, rows, skipped)
    except OSError as e:
        if skipped is not None:
            key = f"codex:{f.name}"
            skipped[key] = skipped.get(key, 0) + 1
        elif os.environ.get("TRACKER_DEBUG"):
            print(f"  [skipped codex] {f.name}: {e}", file=sys.stderr)
    return rows


def extract_codex(sessions_dir=None, skipped=None):
    """Extrae de Codex CLI standalone (~/.codex/sessions, coffe-2sy).

    Hasta v4.4 solo entraba Codex-via-Pi (provider openai-codex); las
    sesiones del TUI/CLI eran invisibles. Formato: 1 rollout-*.jsonl = 1
    sesión bajo sessions/YYYY/MM/DD/. Cada event_msg token_count trae
    total_token_usage (acumulativo) y last_token_usage (delta del turno)
    — la fila usa el delta, así el uso acumulado no se doble-cuenta.

    Semántica de tokens: input_tokens de Codex YA incluye los cached
    (verificado con total vs suma de deltas) → fresh = input - cached,
    cache_read = cached (como reporta Anthropic). reasoning_output es
    parte del output. Cache_write siempre 0 en este log.

    Sin cost en el log (como gemini-cli, coffe-8t8): costo estimado
    pay-per-token con rates vigentes por fecha. El precio de los modelos
    gpt-6.x no es conocido → default_rates, provenance *assumed*.

    El filtro es is_charly(cwd) (respeta --filter) y el label es el
    proyecto derivado del cwd vía amp_proj_from_uri (org-repo), igual
    que Amp; fallback "codex-unknown" si el cwd no es derivable.
    """
    rows = []
    sessions_dir = sessions_dir or (CODEX_DIR / "sessions")
    if not sessions_dir.exists():
        return rows
    for day_dir in sorted(sessions_dir.glob("*/*/*")):
        if not day_dir.is_dir():
            continue
        for f in sorted(day_dir.glob("rollout-*.jsonl")):
            _codex_rollout_file(f, rows, skipped)
    return rows


class _CodexRollout:
    """Estado de un rollout: cwd, modelo y turnos (ts, last_token_usage)."""

    def __init__(self):
        self.cwd = None
        self.model = None
        self.turns = []

    def feed(self, entry):
        typ = entry.get("type")
        p = entry.get("payload") or {}
        if typ == "session_meta":
            self.cwd = p.get("cwd") or self.cwd
        elif p.get("type") == "thread_settings_applied":
            self.model = ((p.get("thread_settings") or {}).get("model")) or self.model
        elif p.get("model"):
            # formato viejo (abr-2026): turn_context trae el modelo plano
            self.model = p["model"]
        elif p.get("type") == "token_count":
            self.token_count(entry, p)

    def token_count(self, entry, p):
        ts = parse_ts(entry.get("timestamp"))
        last = (p.get("info") or {}).get("last_token_usage") or {}
        if ts and last:
            self.turns.append((ts, last))


def _codex_turn_row(ts, u, ctx):
    fam, ver, model, proj, fname = ctx
    inp = u.get("input_tokens") or 0
    cached = min(u.get("cached_input_tokens") or 0, inp)  # sanity
    fresh = inp - cached
    out = u.get("output_tokens") or 0
    cost = estimate_cost(fam, ver, fresh, out, cached, 0, when=ts.date())
    return {
        "source": "codex", "tool": "codex",
        "model_raw": model,
        "model_family": fam, "model_version": ver,
        "project": proj,
        "timestamp": ts.isoformat(), "hour": hour_key(ts),
        "input_tokens": fresh, "output_tokens": out,
        "cache_read_tokens": cached,
        "cache_write_tokens": u.get("cache_write_input_tokens") or 0,
        "cost_effective": cost,
        "session_file": fname,  # add-session-spend-report
    }


def _codex_rollout_feed(f, roll):
    with open(f) as fh:
        for line in fh:
            entry = _json_line(line)
            if entry is not None:
                roll.feed(entry)


def _codex_rollout_ctx(f, roll):
    proj = amp_proj_from_uri(roll.cwd) or "codex-unknown"
    fam, ver = model_details(roll.model or "unknown")
    return (fam, ver, roll.model or "unknown", proj, f.name)


def _parse_codex_rollout(f, rows, skipped=None):
    """1 rollout → filas por turno (token_count) en `rows`.

    Malformed lines se descartan (best-effort); una línea rota no tira la
    sesión entera. Sin meta (session_meta) o sin token_count → 0 filas.
    """
    roll = _CodexRollout()
    _codex_rollout_feed(f, roll)
    if roll.cwd is None or not roll.turns:
        return rows
    if not is_charly(roll.cwd):  # coffe-2sy: unifica con --filter
        return rows
    ctx = _codex_rollout_ctx(f, roll)
    for ts, u in roll.turns:
        if not in_window(ts):
            continue  # coffe-snj: ventana
        rows.append(_codex_turn_row(ts, u, ctx))
    return rows


_CODEX_PREAMBLE = ("<environment_context", "<turn_context", "<user_instructions",
                   "<skill>", "# AGENTS.md")


class _CodexSessionScan:
    """Escaneo de un rollout → métricas de sesión (coffe-2sy).

    duration_msgs = prompts de usuario reales (se excluyen los preámbulos
    inyectados por el harness: AGENTS.md, environment/turn context, skills
    — best-effort sobre el texto). n_turns = token_count events (turnos
    con uso). has_agent=False: los subagentes son semántica de Claude; el
    alcance se declara en sessions.agent_semantics. La ventana
    --since/--until se aplica en extract_sessions() (filter_sessions).
    """

    def __init__(self):
        self.cwd = None
        self.prompts = 0
        self.turns = 0
        self.first_ts = None
        self.last_ts = None

    def feed(self, entry):
        p = entry.get("payload") or {}
        pt = p.get("type")
        if entry.get("type") == "session_meta":
            self.cwd = p.get("cwd") or self.cwd
        elif pt == "token_count":
            self.stamp(entry, count_turn=True)
        elif pt == "message" and p.get("role") == "user":
            self.user_msg(entry, p)

    def stamp(self, entry, count_turn=False):
        ts = parse_ts(entry.get("timestamp"))
        if not ts:
            return
        if count_turn:
            self.turns += 1
        if self.first_ts is None:
            self.first_ts = ts
        self.last_ts = ts

    def user_msg(self, entry, p):
        self.stamp(entry)
        text = "".join(
            c.get("text", "") for c in (p.get("content") or [])
            if isinstance(c, dict))
        if not text.lstrip().startswith(_CODEX_PREAMBLE):
            self.prompts += 1


def _codex_scan_file(f):
    scan = _CodexSessionScan()
    with open(f) as fh:
        for line in fh:
            entry = _json_line(line)
            if entry is not None:
                scan.feed(entry)
    if scan.cwd is None or not scan.turns:
        return None
    if not is_charly(scan.cwd):
        return None
    return scan


def _codex_session_row(scan):
    return {
        "tool": "codex", "source": "codex_session_files",
        "project": amp_proj_from_uri(scan.cwd) or "codex-unknown",
        "first_ts": (scan.first_ts or scan.last_ts).isoformat()[:10],
        "first_ts_full": (scan.first_ts or scan.last_ts).isoformat(),
        "last_ts_full": scan.last_ts.isoformat() if scan.last_ts else None,
        "duration_msgs": scan.prompts,
        "n_turns": scan.turns,
        "n_tools": 0,
        "has_agent": False,
        "n_skills": None,
        "n_errors": None,
        "n_compactions": None,
    }


def extract_codex_sessions(sessions_dir=None):
    """coffe-2sy: sesiones codex — 1 rollout = 1 sesión.

    duration_msgs = prompts de usuario reales (se excluyen los preámbulos
    inyectados por el harness: AGENTS.md, environment/turn context, skills
    — best-effort sobre el texto). n_turns = token_count events (turnos
    con uso). has_agent=False: los subagentes son semántica de Claude; el
    alcance se declara en sessions.agent_semantics. La ventana
    --since/--until se aplica en extract_sessions() (filter_sessions).
    """
    sessions = []
    sessions_dir = sessions_dir or (CODEX_DIR / "sessions")
    if not sessions_dir.exists():
        return sessions
    for day_dir in sorted(sessions_dir.glob("*/*/*")):
        if not day_dir.is_dir():
            continue
        for f in sorted(day_dir.glob("rollout-*.jsonl")):
            try:
                scan = _codex_scan_file(f)
                if scan is not None:
                    sessions.append(_codex_session_row(scan))
            except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
                _debug_skip(f"codex-session {f.name}: {e}")
    return sessions


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

    def aux_call(self, entry, ts):
        usage = entry.get("usage") or {}
        if ts and usage:
            self.calls.append((ts, entry.get("model", "unknown"),
                               usage, "aux_model_call"))

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


def _omp_session_ts(meta, first):
    last = meta["last_ts"] or first
    return {
        "first_ts_full": meta["first_raw"] or first.isoformat(),
        "last_ts_full": meta["last_raw"] or (last or first).isoformat(),
    }


def _omp_session_dict(meta, calls, cwd, first):
    n_turns = sum(1 for c in calls if c[3] == "assistant_turn")
    out = {
        "tool": "omp",
        "source": "omp_session_files",
        "project": amp_proj_from_uri(cwd) or "omp-unknown",
        "first_ts": first.isoformat()[:10],
        "duration_msgs": len(meta["user_ts"]),
        "n_turns": n_turns,
        "n_tools": len(meta["tools"]),
        "has_agent": False,
        "n_skills": None,
        "n_errors": meta["n_errors"],
        "n_compactions": None,
    }
    out.update(_omp_session_ts(meta, first))
    return out


def _omp_session_row(meta, calls, sd_name):
    """Sesiones omp — 1 archivo JSONL = 1 sesión (misma semántica de reset
    que pi: cada sesión abre archivo nuevo, el archivo ES la señal).

    n_turns = llamadas principales (assistant con usage); las auxiliares
    (model_usage) facturan tokens pero no son turnos de conversación.
    n_errors es señal real del log (errorId en la llamada); skills y
    compactions sin señal en el formato omp (None — _session_stats las
    excluye de los totales, no las cuenta como 0). has_agent=False: sin
    señal de subagentes. La ventana --since/--until se aplica en
    extract_sessions() (filter_sessions). sessions_dir permite tests.
    """
    cwd = meta["cwd"] or sd_name
    if not is_charly(cwd):
        return None
    first = meta["first_ts"] or meta["session_ts"]
    n_turns = sum(1 for c in calls if c[3] == "assistant_turn")
    if not n_turns and not meta["user_ts"] and first is None:
        return None  # archivo sin actividad
    return _omp_session_dict(meta, calls, cwd, first)


def _omp_session_file(f, sessions):
    try:
        meta, calls = _omp_scan(f)
    except OSError:
        return
    if meta is None:
        return
    row = _omp_session_row(meta, calls, f.parent.name)
    if row is not None:
        sessions.append(row)
    return sessions


def extract_omp_sessions(sessions_dir=None):
    sessions = []
    sessions_dir = sessions_dir or OMP_DIR
    if not sessions_dir.exists():
        return sessions
    for sd in sorted(sessions_dir.iterdir()):
        if not sd.is_dir():
            continue
        for f in sorted(sd.glob("*.jsonl")):
            _omp_session_file(f, sessions)
    return sessions


def _claude_cache_sessions():
    """Sesiones desde el cache de Claude (extract_session_stats step)."""
    sessions = []
    cache_file = CLAUDE_DIR / "dashboard-cache.json"
    if not cache_file.exists():
        return sessions
    cache = json.loads(cache_file.read_text())
    for key, summary in cache.get("entries", {}).items():
        if summary.get("source") != "claude":
            continue
        proj = summary.get("project", "")
        if not is_charly(proj):
            continue
        tools = summary.get("tool_counts", {})
        skills = summary.get("skill_uses", {})
        sessions.append({
            "tool": "claude-cli",  # coffe-i31: provenance por tool
            "source": "claude_cache",
            "project": proj,
            "first_ts": (summary.get("first_ts") or " ")[:10],
            "first_ts_full": summary.get("first_ts"),
            "last_ts_full": summary.get("last_ts"),
            "duration_msgs": summary.get("user_messages", 0),
            "n_turns": len(summary.get("turns", [])),
            "n_tools": len(tools),
            "has_agent": "Agent" in tools,
            "n_skills": sum(skills.values()),
            "n_errors": len(summary.get("api_errors", [])),
            "n_compactions": summary.get("compactions", 0),
        })
    return sessions


def extract_session_stats():
    """Extrae estadísticas de sesión desde el cache de Claude."""
    # coffe-snj: sesiones que se solapan con la ventana --since/--until
    return filter_sessions(_claude_cache_sessions(), SINCE, UNTIL)


class _PiSessionScan:
    """coffe-i31: escaneo de 1 archivo JSONL de Pi → métricas de sesión.

    En pi el reset es /new (no /clear) y el TUI lo intercepta: nunca
    aparece como user message en el JSONL (verificado en la auditoría:
    0 /new en 981 archivos). La señal correcta de sesión/reset es el
    archivo mismo: cada /new abre JSONL nuevo.

    Métricas sin señal en pi (skills/errores) van en None — _session_stats
    las excluye de los totales en vez de contarlas como 0. n_compactions es
    real desde coffe-64z (CORR-003): las entradas type=="compaction"
    existen en los JSONL de Pi desde ~may-2026 (12 en miblioteca, review
    R5); sin entradas == 0, no None.
    has_agent=False: la semántica Agent (subagentes) es de Claude; el
    alcance se declara en sessions.agent_semantics.
    """

    def __init__(self):
        self.msgs = 0
        self.user_prompts = 0
        self.n_compactions = 0
        self.first_ts = None
        self.last_ts = None
        self.first_raw = None
        self.last_raw = None
        self.tools = set()

    def feed(self, entry):
        if entry.get("type") == "compaction":
            self.n_compactions += 1
            return
        if entry.get("type") != "message":
            return
        self.stamp(entry)
        msg = entry.get("message", {}) or {}
        role = msg.get("role")
        if role == "user":
            self.user_prompts += 1
        elif role == "assistant":
            self.assistant(msg)

    def stamp(self, entry):
        ts = parse_ts(entry.get("timestamp"))
        if ts is None:
            return
        if self.first_ts is None:
            self.first_ts = ts
            self.first_raw = entry.get("timestamp")
        self.last_ts = ts
        self.last_raw = entry.get("timestamp")

    def assistant(self, msg):
        self.msgs += 1
        if not _pi_has_tool_call(msg):
            return
        for part in (msg.get("parts") or msg.get("content") or []):
            if isinstance(part, dict) and str(part.get("type", "")).startswith("tool"):
                name = part.get("toolName") or part.get("name")
                self.tools.add(str(name) if name else "tool-call")


def _pi_session_row(proj, scan):
    if scan.first_ts is None:
        return None  # archivo sin mensajes
    return {
        "tool": "pi",
        "source": "pi_session_files",
        "project": proj,
        "first_ts": scan.first_ts.isoformat()[:10],
        "first_ts_full": scan.first_raw,
        "last_ts_full": scan.last_raw,
        "duration_msgs": scan.user_prompts,
        "n_turns": scan.msgs,
        "n_tools": len(scan.tools),
        "has_agent": False,
        "n_skills": None,
        "n_errors": None,
        "n_compactions": scan.n_compactions,
    }


def _pi_session_file(f, proj, sessions):
    try:
        scan = _PiSessionScan()
        with open(f) as fh:
            for line in fh:
                scan.feed(json.loads(line))
        row = _pi_session_row(proj, scan)
        if row is not None:
            sessions.append(row)
    except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
        _debug_skip(f"pi-session {f.name}: {e}")
    return sessions


def extract_pi_sessions(sessions_dir=None):
    """coffe-i31: sesiones pi — 1 archivo JSONL = 1 sesión.

    sessions_dir permite tests con tmpdir (como extract_pi, coffe-8t8).
    La ventana --since/--until se aplica en extract_sessions() (una sesión
    puede solaparse con la ventana — filter_sessions).
    """
    sessions = []
    sessions_dir = sessions_dir or (PI_DIR / "sessions")
    if not sessions_dir.exists():
        return sessions
    for sd in sorted(sessions_dir.iterdir()):
        if not sd.is_dir():
            continue
        proj = sd.name
        if not is_charly(proj):
            continue  # coffe-i31: scope consistente
        for f in sorted(sd.glob("*.jsonl")):
            _pi_session_file(f, proj, sessions)
    return sessions


def _amp_session_hit(f):
    """(ts, proyecto, timestamp crudo) por archivo tocado, o None."""
    try:
        entry = json.loads(f.read_text())
        uri = entry.get("uri", "")
        if not is_charly(uri):
            return None
        ts = parse_ts(entry.get("timestamp"))
        if not ts:
            return None
        raw = entry.get("timestamp")
        # coffe-372: logs amp reales usan epoch ms INT — el crudo
        # se normaliza a ISO (filter_sessions hace [:10]; un int
        # crashea con ventana --since/--until). Strings intactos.
        raw = raw if isinstance(raw, str) else ts.isoformat()
        return (ts, amp_proj_from_uri(uri) or "amp-unknown", raw)
    except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError):
        return None


def extract_amp_sessions(amp_dir=None):
    """coffe-i31: sesiones amp — 1 dir de file-changes = 1 tarea.

    Amp es un agente autónomo: has_agent=True por definición (se declara
    en sessions.agent_semantics). n_turns = archivos tocados; sin señal
    de skills/errores/compactions (None).
    """
    sessions = []
    amp_dir = amp_dir or (AMP_DIR / "file-changes")
    if not amp_dir.exists():
        return sessions
    for td in sorted(amp_dir.iterdir()):
        if not td.is_dir():
            continue
        hits = sorted(h for h in (_amp_session_hit(f) for f in td.iterdir()) if h)
        if not hits:
            continue
        first, last = hits[0][0], hits[-1][0]
        sessions.append({
            "tool": "amp",
            "source": "amp_file_changes",
            "project": hits[0][1],
            "first_ts": first.isoformat()[:10],
            "first_ts_full": hits[0][2],  # timestamp crudo del log
            "last_ts_full": hits[-1][2],
            "duration_msgs": len(hits),
            "n_turns": len(hits),
            "n_tools": 0,
            "has_agent": True,
            "n_skills": None,
            "n_errors": None,
            "n_compactions": None,
        })
    return sessions


def extract_sessions():
    """coffe-i31: sesiones de todas las tools (claude cache + pi files +
    amp tasks), con la ventana --since/--until aplicada a las nuevas
    fuentes (el claude cache ya la aplica en extract_session_stats)."""
    return (extract_session_stats()
            + filter_sessions(extract_pi_sessions(), SINCE, UNTIL)
            + filter_sessions(extract_amp_sessions(), SINCE, UNTIL)
            + filter_sessions(extract_codex_sessions(), SINCE, UNTIL)  # coffe-2sy
            + filter_sessions(extract_omp_sessions(), SINCE, UNTIL))


def collect_outcomes():
    """FPA-014: commits/releases por proyecto por mes vía token de GitHub.

    Sin GITHUB_TOKEN (o sin mapeo proyecto→repo) se emite la estructura vacía
    con la razón en metadata.outcomes — n/a explícito, nunca inventado.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return {}, "unavailable: no hay GITHUB_TOKEN — commits/releases no emisionados"
    # Con token haría falta el mapeo proyecto local → repo remoto (no existe aún);
    # F6 lo conecta. Estructura ya declarada en el schema (monthly.outcomes_by_project).
    return {}, "unavailable: mapeo proyecto→repo GitHub pendiente (ver README)"


def _session_interval(s):
    """(first, last) parseados de una sesión, o None si no computable:
    falta timestamp o solo hay fecha sin hora."""
    f = s.get("first_ts_full") or s.get("first_ts")
    l = s.get("last_ts_full") or s.get("last_ts")
    if not f or not l:
        return None
    fi, li = parse_ts(f), parse_ts(l)
    if not fi or not li or len(str(f)) < 16:  # solo fecha = sin hora, no computable
        return None
    return fi, li


def _peak_from_events(intervals):
    events = []
    for fi, li in intervals:
        events.append((fi, 1))
        events.append((li, -1))
    # cierres antes de aperturas en el mismo instante → no cuenta doble
    events.sort(key=lambda e: (e[0], e[1]))
    peak = cur = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def _peak_simultaneous_sessions(sessions):
    """FPA-120(b): máximo solapamiento de sesiones con sweep line.
    None si falta algún timestamp (no se puede computar con honestidad)."""
    intervals = []
    for s in sessions:
        iv = _session_interval(s)
        if iv is None:
            return None
        intervals.append(iv)
    return _peak_from_events(intervals)


def get_sub_cost(tool, ts_str, eff_cost):
    """
    tool, timestamp_str, effective_cost -> (real_cost, effective_cost, sub_label)
    For subscription periods: real_cost = 0 (covered by subscription).
    For pay-per-token: real_cost = effective_cost.
    """
    if tool not in SUBSCRIPTIONS:
        return eff_cost, eff_cost, "pay-per-token"
    date = (ts_str or "")[:10]
    for period in SUBSCRIPTIONS.get(tool, []):
        if period["start"] <= date and (period["end"] is None or date < period["end"]):
            if period["monthly_fee"] > 0:
                return 0.0, eff_cost, period["label"]
            else:
                return eff_cost, eff_cost, period["label"]
    return eff_cost, eff_cost, "unknown"


def _period_fee_in_month(period, m_key):
    """Fee del período activo en el mes m_key (overlap por YYYY-MM), 0 si
    el período es gratis o no solapa."""
    if period["monthly_fee"] <= 0:
        return 0.0
    p_start = period["start"]
    p_end = period["end"] or "9999-12"
    # Does this month overlap with the subscription period?
    if m_key >= p_start[:7] and m_key < p_end[:7]:
        return period["monthly_fee"]
    return 0.0


def _month_sub_fee(tools, m_key):
    # Find which months had which subscriptions active
    total = 0.0
    for tool, periods in SUBSCRIPTIONS.items():
        if tool not in tools:
            continue
        for period in periods:
            total += _period_fee_in_month(period, m_key)
    return total


def calc_subscription_fees(monthly_data):
    """
    Calculate monthly subscription fees for each month.
    Returns dict: {month_key: total_sub_fee}
    """
    sub_fees = {}
    for m_key, m_data in monthly_data.items():
        total = _month_sub_fee(m_data.get("tools", {}), m_key)
        if total > 0:
            sub_fees[m_key] = total
    return sub_fees


# --- ctx-tracker: reducer puro de contexto (epic add-context-dashboard) ------
# Bloque aislado (co-modificación coordinada de los tickets ctx-tracker):
# reduce_context(entries) -> metrics, mismo estilo que aggregate(): no toca
# disco, testeado con líneas JSONL sintéticas (tests/test_tracker_context.py).

DEFAULT_CONTEXT_THRESHOLDS = {
    "big_result_chars": 100_000,      # toolResult que cuenta como big_result
    "big_session_tokens": 100_000,    # sesión grande para agregados monthly_model
    "pathological_chars": 500_000,    # bloque assistant glitch → excluida
    "chars_per_token": 4,             # factor assumed (subestima: español/code ~3)
}


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

    def message(self, entry):
        msg = entry.get("message", {}) or {}
        role = msg.get("role")
        if role == "user":
            self.user_parts(msg)
        elif role == "assistant":
            self.assistant(msg)
        elif role == "toolResult":
            self.tool_result(msg)

    def user_parts(self, msg):
        for part in (msg.get("content") or []):
            if isinstance(part, dict) and part.get("type") == "text":
                n = len(part.get("text") or "")
                self.add("user_text", n)
                self.seg_chars += n

    def turn_model(self, msg):
        # modelo del turno: el campo propio del mensaje manda (los
        # archivos reales lo traen); fallback al último model_change
        turn_model = msg.get("model") or self.cur_model
        if turn_model:
            self.models_turns[turn_model] = self.models_turns.get(turn_model, 0) + 1
        if msg.get("provider"):
            self.cur_provider = msg["provider"]

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

def _pctl(sorted_vals, p):
    """Percentil p (0..1) con interpolación lineal sobre lista YA ordenada
    (stdlib-only, determinista)."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    idx = (len(sorted_vals) - 1) * p
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def _ctx_source_sums(rows, cpt):
    """tokens estimados por fuente (chars/cpt) sumados sobre las filas."""
    by_source = {}
    for r in rows:
        for src, n in r.get("chars_by_source", {}).items():
            by_source[src] = by_source.get(src, 0) + n / cpt
    return by_source


def _ctx_month_spend(rows):
    """spend_* por componente (sumas sobre los turnos con coste)."""
    def s(key):
        return round(sum(r[key] for r in rows
                         if r.get(key) is not None), 2)
    return {"spend_in": s("cost_in"), "spend_out": s("cost_out"),
            "spend_cr": s("cost_cr"), "spend_cw": s("cost_cw")}


def _ctx_month_row(key, rows, th, max_month):
    month, model = key
    cpt = th["chars_per_token"]
    peaks = sorted(r["peak_ctx_est"] for r in rows)
    total_turns = sum(r["turns"] for r in rows)
    by_source = _ctx_source_sums(rows, cpt)
    row = {
        "month": month,
        "model": model,
        "sessions": len(rows),
        "peak_p50": _pctl(peaks, 0.5),
        "peak_p90": _pctl(peaks, 0.9),
        "pct_big_session": round(100.0 * sum(
            1 for r in rows
            if r["peak_ctx_est"] > th["big_session_tokens"]) / len(rows), 2),
        "tokens_per_turn_by_source": {
            src: round(t / total_turns, 2)
            for src, t in sorted(by_source.items())},
        "partial": month == max_month,
    }
    row.update(_ctx_month_spend(rows))
    return row


def _ctx_validation_ratio(clean):
    # ratio de validación: tokens del ÚLTIMO turno con usage / final_ctx_est
    # (el último turno ≈ contexto final real; la suma acumulada de la sesión
    # no es comparable con el contexto de un punto en el tiempo)
    return sorted(
        r["final_turn_tokens"] / r["final_ctx_est"]
        for r in clean
        if r.get("context_source") == "pi" and r.get("final_turn_tokens")
        and r["final_ctx_est"] > 0)


def _ctx_meta(th, ratios, stubs, excluded_pathological):
    return {
        "chars_per_token": th["chars_per_token"],
        "chars_per_token_tag": "assumed",
        "chars_per_token_bias": (
            "subestima el contexto real (~40%): español/código ~3 "
            "chars/token y overhead de serialización; consistente "
            "entre eras — ver ratio de validación"),
        "validation_ratio_median": _pctl(ratios, 0.5) if ratios else None,
        "thresholds": th,
        "stubs": stubs,
        "excluded_pathological": excluded_pathological,
    }


def _ctx_clean(session_rows):
    clean = [r for r in session_rows
             if r.get("turns", 0) > 0 and not r.get("pathological")]
    stubs = sum(1 for r in session_rows if r.get("turns", 0) == 0)
    excluded = sum(1 for r in session_rows if r.get("pathological"))
    return clean, stubs, excluded


def _ctx_groups(clean):
    # agrupar por (mes, modelo dominante)
    groups = {}
    for r in clean:
        groups.setdefault((r.get("month"), r.get("model")), []).append(r)
    max_month = max((r["month"] for r in clean if r.get("month")),
                    default=None)
    return groups, max_month


def aggregate_context(session_rows, config=None):
    """Agregados de contexto por mes × modelo dominante + meta (coffe-2xj,
    tasks 1.6+1.7). Pure: session_rows son las filas que emite
    reduce_context() (+ month + tokens_reported); nada de disco.

    Set limpio: turns > 0 y no pathological — stubs y patológicas quedan
    fuera de los agregados pero contadas en meta (loss visible).
    """
    th = dict(DEFAULT_CONTEXT_THRESHOLDS)
    if config:
        th.update(config)

    clean, stubs, excluded = _ctx_clean(session_rows)
    groups, max_month = _ctx_groups(clean)
    monthly_model = [
        _ctx_month_row(key, rows, th, max_month)
        for key, rows in sorted(groups.items(),
                                key=lambda kv: (str(kv[0][0]), str(kv[0][1])))]

    return {
        "monthly_model": monthly_model,
        "meta": _ctx_meta(th, _ctx_validation_ratio(clean), stubs, excluded),
    }

_CTX_UNAVAILABLE = {
    "available": False, "reason": None,
    "sessions": [], "monthly_model": [], "meta": {},
}


def _ctx_unavailable(reason):
    out = dict(_CTX_UNAVAILABLE)
    out["reason"] = reason
    return out


def _ctx_window_ok(fdate):
    # ventana --since/--until (coffe-snj): solape por fecha del filename
    # (los filenames de sesión de Pi empiezan con la fecha)
    if not fdate:
        return True
    if SINCE and fdate < SINCE.isoformat():
        return False
    return not (UNTIL and fdate > UNTIL.isoformat())


def _ctx_file_entries(f):
    try:
        return [json.loads(line) for line in open(f) if line.strip()]
    except (json.JSONDecodeError, OSError) as e:
        _debug_skip(f"ctx {f.name}: {e}")
        return None


def _ctx_files(sd):
    return [f for f in sorted(sd.glob("*.jsonl"))
            if _ctx_window_ok(f.name[:10])]


def _ctx_project_rows(sd, th):
    """(rows, n_stubs, n_files) de un proyecto (dir de sesiones de Pi)."""
    rows = []
    n_stubs = 0
    n_files = 0
    proj = sd.name
    if not in_scope(proj):
        return rows, n_stubs, n_files
    for f in _ctx_files(sd):
        entries = _ctx_file_entries(f)
        if entries is None:
            continue
        n_files += 1
        row = reduce_context(entries, config=th, month=f.name[:7])
        row["project"] = proj
        if row["turns"] > 0:
            rows.append(row)
        else:
            n_stubs += 1
    return rows, n_stubs, n_files


def _ctx_walk(sessions_dir, th):
    """(rows, n_stubs, n_files) del walk de sesiones de Pi."""
    rows = []
    n_stubs = 0
    n_files = 0
    for sd in sorted(sessions_dir.iterdir()):
        if not sd.is_dir():
            continue
        p_rows, p_stubs, p_files = _ctx_project_rows(sd, th)
        rows += p_rows
        n_stubs += p_stubs
        n_files += p_files
    return rows, n_stubs, n_files


def extract_context_metrics(sessions_dir=None):
    """coffe-kup (tasks 1.8): camina los JSONL de Pi (mismo patrón que
    extract_pi_sessions: 1 archivo = 1 sesión, scope in_scope, month desde
    el FILENAME del archivo) y reduce_context() por sesión.

    Emite la sección context_metrics del reporte: sessions[] (filas con
    turns > 0 — stubs fuera, contados en meta.stubs), monthly_model[] y
    meta desde aggregate_context(). Si el dir de sesiones no existe emite
    available=False con razón (missing honesto, no array fantasma).

    Segundo walk aceptado por diseño (design.md del epic: "el walk extra
    agrega duración al nightly — se mide y, si pesa, --since").
    """
    sessions_dir = sessions_dir or (PI_DIR / "sessions")
    if not sessions_dir.exists():
        return _ctx_unavailable(
            f"unavailable: no existe {sessions_dir} "
            "(máquina sin logs de Pi)")
    cfg = load_config() or {}
    th = dict(DEFAULT_CONTEXT_THRESHOLDS)
    th.update(cfg.get("context_thresholds") or {})

    rows, n_stubs, n_files = _ctx_walk(sessions_dir, th)
    if not n_files:
        return _ctx_unavailable(
            "unavailable: sin archivos de sesión de Pi in-scope")
    agg = aggregate_context(rows, config=th)
    agg["meta"]["stubs"] = n_stubs  # del walk completo (los rows ya vienen limpios)
    agg["available"] = True
    agg["reason"] = None
    agg["sessions"] = rows
    return agg

# --- fin bloque ctx-tracker --------------------------------------------------


class Bucket:
    """Acumulador de métricas para un bucket de agregación (día, mes, proyecto).

    Una sola implementación del bloque de acumulación que antes estaba
    copiado 4 veces en aggregate() — el cambio de contabilidad de tokens
    se hace en UN lugar.
    """

    def __init__(self):
        self.interactions = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.cost_effective = 0.0
        self.cost_real = 0.0
        self.tools = Counter()
        self.models = Counter()
        self.first_seen = None
        self.last_seen = None
        self.skills = Counter()

    def add(self, r, real_cost):
        self.interactions += 1
        self.input_tokens += r.get("input_tokens", 0) or 0
        self.output_tokens += r.get("output_tokens", 0) or 0
        self.cache_read_tokens += r.get("cache_read_tokens", 0) or 0
        self.cache_write_tokens += r.get("cache_write_tokens", 0) or 0
        self.cost_effective += r.get("cost_effective", 0) or 0
        self.cost_real += real_cost
        self.tools[r["tool"]] += 1
        self.models[r["model_raw"]] += 1

    def to_dict(self, detail=False):
        d = {
            "interactions": self.interactions,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "cost_effective": round(self.cost_effective, 2),
            "cost_real": round(self.cost_real, 2),
            "tools": dict(self.tools.most_common()),
            "models": dict(self.models.most_common()),
        }
        if detail:
            d["subscription_fees"] = 0.0
        return d


class HourlyBucket(Bucket):
    """Bucket horario: además lleva stats detalladas por herramienta."""

    def __init__(self):
        super().__init__()
        self.tool_stats = defaultdict(Bucket)

    def add(self, r, real_cost):
        super().add(r, real_cost)
        ts = self.tool_stats[r["tool"]]
        ts.interactions += 1
        ts.input_tokens += r.get("input_tokens", 0) or 0
        ts.output_tokens += r.get("output_tokens", 0) or 0
        ts.cache_read_tokens += r.get("cache_read_tokens", 0) or 0
        ts.cache_write_tokens += r.get("cache_write_tokens", 0) or 0
        ts.cost_effective += r.get("cost_effective", 0) or 0
        ts.cost_real += real_cost

    def to_dict(self):
        d = super().to_dict()
        d["tools"] = {t: {
            "req": b.interactions, "in": b.input_tokens, "out": b.output_tokens,
            "cache_read": b.cache_read_tokens, "cache_write": b.cache_write_tokens,
            "cost_eff": round(b.cost_effective, 8), "cost_real": round(b.cost_real, 8),
        } for t, b in self.tool_stats.items()}
        return d


def _context_switches(interactions):
    """Cambios de proyecto entre requests consecutivos (por día)."""
    switches_by_day = Counter()
    prev = None
    for r in sorted(interactions, key=lambda x: x["timestamp"]):
        d = r["timestamp"][:10]
        p = clean_proj_name(r.get("project", "unknown"))
        if prev and prev[1] != p:
            switches_by_day[d] += 1
        prev = (d, p)
    return switches_by_day


def _project_daily(by_project, day_projects, proj_day):
    """Matriz densa proyecto × día para el Gantt."""
    from datetime import date as _date, timedelta as _td
    all_days = []
    if day_projects:
        d0 = _date.fromisoformat(min(day_projects))
        d1 = _date.fromisoformat(max(day_projects))
        cur = d0
        while cur <= d1:
            all_days.append(cur.isoformat())
            cur += _td(days=1)
    return {
        "days": all_days,
        "matrix": {
            p: [proj_day[p].get(d, 0) for d in all_days]
            for p in sorted(by_project, key=lambda x: -sum(proj_day[x].values()))
        },
    }


def _mt_dist(mt_hour_counts):
    mt_dist = Counter()
    for n in mt_hour_counts.values():
        if n == 1:
            mt_dist["1"] += 1
        elif n == 2:
            mt_dist["2"] += 1
        elif n == 3:
            mt_dist["3"] += 1
        else:
            mt_dist["4+"] += 1
    return mt_dist


def _mt_stats(counts):
    """(n_multi, max_n, max_keys) de un conteo {clave: proyectos}."""
    n_multi = sum(1 for n in counts.values() if n >= 2)
    max_n = max(counts.values()) if counts else 0
    max_keys = ([k for k, n in counts.items() if n == max_n]
                if counts else [])
    return n_multi, max_n, max_keys


def _mt_hourly(hour_projects, mt_hour_counts):
    total_active_hours = len(hour_projects)
    mt_hours_n, max_n, max_hours = _mt_stats(mt_hour_counts)
    top_mt_hours = sorted(mt_hour_counts.items(), key=lambda x: -x[1])[:10]
    return {
        "total_active_hours": total_active_hours,
        "hours_with_multiple_projects": mt_hours_n,
        "pct_hours_multitasking": round(100 * mt_hours_n / total_active_hours, 1) if total_active_hours else 0,
        "avg_projects_per_active_hour": round(sum(mt_hour_counts.values()) / total_active_hours, 2) if total_active_hours else 0,
        "distribution": dict(_mt_dist(mt_hour_counts).most_common()),
        "max_projects_in_one_hour": {
            "count": max_n,
            "hours": max_hours[:5],
            "projects": sorted(hour_projects[max_hours[0]]) if max_hours else [],
        },
        "top_10_hours": [
            {"hour": h, "projects": n, "list": sorted(hour_projects[h])}
            for h, n in top_mt_hours
        ],
    }


def _mt_daily(day_projects, daily):
    active_days = len(day_projects)
    mt_days = {d: len(ps) for d, ps in day_projects.items()}
    mt_days_n = sum(1 for n in mt_days.values() if n >= 2)
    top_mt_days = sorted(mt_days.items(), key=lambda x: -x[1])[:10]
    return {
        "total_active_days": active_days,
        "days_with_multiple_projects": mt_days_n,
        "pct_days_multitasking": round(100 * mt_days_n / active_days, 1) if active_days else 0,
        "avg_projects_per_active_day": round(sum(mt_days.values()) / active_days, 2) if active_days else 0,
        "top_10_days": [
            {"date": d, "projects": n, "list": sorted(day_projects[d]),
             "interactions": daily[d]["interactions"] if d in daily else 0}
            for d, n in top_mt_days
        ],
    }


def _mt_switches(switches_by_day, active_days):
    total_switches = sum(switches_by_day.values())
    top_switch_days = sorted(switches_by_day.items(), key=lambda x: -x[1])[:10]
    return {
        "total": total_switches,
        "avg_per_active_day": round(total_switches / active_days, 1) if active_days else 0,
        "max_in_one_day": top_switch_days[0][1] if top_switch_days else 0,
        "top_10_days": dict(top_switch_days),
    }


def _multitasking_block(hour_projects, day_projects, daily, switches_by_day):
    mt_hour_counts = {h: len(ps) for h, ps in hour_projects.items()}
    return {
        "description": (
            "Proyectos distintos con actividad en la misma ventana. "
            "'context_switches' cuenta cambios de proyecto entre requests "
            "consecutivos (puede inflarse por agentes paralelos en el mismo minuto)."
        ),
        "hourly": _mt_hourly(hour_projects, mt_hour_counts),
        "daily": _mt_daily(day_projects, daily),
        "context_switches": _mt_switches(switches_by_day, len(day_projects)),
    }


_LENGTH_BUCKETS = ((10, "1-10"), (50, "11-50"), (100, "51-100"),
                   (300, "101-300"), (500, "301-500"))


def _length_bucket(n):
    for cap, label in _LENGTH_BUCKETS:
        if n <= cap:
            return label
    return "500+"


_RESET_SIGNALS = {
    "claude-cli": lambda bt, commands: (
        "/clear en history.jsonl (solo claude)", commands.get("/clear", 0)),
    "pi": lambda bt, commands: (
        "archivos de sesión pi (1 archivo = 1 /new)", bt["total"]),
    "omp": lambda bt, commands: (
        "archivos de sesión omp (1 archivo = 1 sesión)", bt["total"]),
}


def _reset_signal(tool, bt, commands):
    """coffe-i31: resets por tool — señales distintas por harness
    (FPA-117/118 adaptado): claude cuenta /clear del history.jsonl;
    en pi cada /new abre un archivo nuevo (el conteo de archivos de
    sesión ES el conteo de resets); amp es 1 tarea = 1 corrida."""
    fn = _RESET_SIGNALS.get(tool)
    if fn is None:
        return f"tareas {tool} (1 dir = 1 corrida)", bt["total"]
    return fn(bt, commands)


def _fold_session(stats, s):
    n = s["n_turns"]
    stats["length_distribution"][_length_bucket(n)] += 1
    if s.get("has_agent"):
        stats["with_agent"] += 1
    # coffe-i31: None = sin señal (no 0) — excluido del total
    stats["total_api_errors"] += s.get("n_errors") or 0
    stats["total_compactions"] += s.get("n_compactions") or 0
    stats["avg_turns"] += n
    stats["avg_tools"] += s.get("n_tools") or 0
    if s.get("n_skills") is not None:
        stats["avg_skills"] += s["n_skills"]
    bt = stats["by_tool"][s.get("tool", "claude-cli")]
    bt["total"] += 1
    if s.get("has_agent"):
        bt["with_agent"] += 1
    return (n, s["duration_msgs"], s["first_ts"], s["project"])


def _session_stats(sessions, commands=None):
    """Estadísticas de sesiones (largos, autonomía, promedios, top).

    coffe-i31: multi-tool con provenance — by_tool (totals/with_agent/
    resets por tool) y agent_semantics declarando el alcance. Los campos
    sin señal en una tool (pi/amp: skills/errores/compactions) van en
    None y se excluyen de los totales, no se cuentan como 0.
    """
    stats = {
        "total_sessions": len(sessions),
        "length_distribution": Counter(),
        "with_agent": 0,
        "total_api_errors": 0,
        "total_compactions": 0,
        "avg_turns": 0,
        "avg_tools": 0,
        "avg_skills": 0,
        "agent_semantics": (
            "claude-cli: sesión con herramienta Agent (subagentes); "
            "amp: autónomo por definición (has_agent=True); "
            "pi: sin señal de subagentes (has_agent=False); "
            "omp: sin señal de subagentes (has_agent=False)"),
        "by_tool": defaultdict(lambda: {
            "total": 0, "with_agent": 0,
            "resets": {"signal": "", "count": 0}}),
    }
    longest = [_fold_session(stats, s) for s in sessions]
    if sessions:
        n = len(sessions)
        stats["avg_turns"] /= n
        stats["avg_tools"] /= n
        n_sk = sum(1 for s in sessions if s.get("n_skills") is not None)
        stats["avg_skills"] = stats["avg_skills"] / n_sk if n_sk else 0
    commands = commands or Counter()
    for tool, bt in stats["by_tool"].items():
        signal, count = _reset_signal(tool, bt, commands)
        bt["resets"] = {"signal": signal, "count": count}
    stats["by_tool"] = dict(stats["by_tool"])
    longest.sort(key=lambda x: -x[0])
    stats["top_longest_by_turns"] = [
        {"turns": t, "msgs": m, "date": d, "project": clean_proj_name(p)}
        for t, m, d, p in longest[:10]
    ]
    return stats


def _skills_from_cache(by_skill_total, skills_by_project):
    """Skills del cache de Claude (dashboard-cache.json)."""
    cache_file = CLAUDE_DIR / "dashboard-cache.json"
    if not cache_file.exists():
        return by_skill_total
    cache = json.loads(cache_file.read_text())
    for key, summary in cache.get("entries", {}).items():
        if summary.get("source") != "claude":
            continue
        proj = summary.get("project", "")
        if not is_charly(proj):
            continue
        proj_clean = clean_proj_name(proj)
        for skill, count in summary.get("skill_uses", {}).items():
            by_skill_total[skill] += count
            skills_by_project[proj_clean][skill] += count
    return by_skill_total


def _command_of(display):
    """Comando slash de un display del history ('/cmd args...'), o None."""
    stripped = display.strip()
    if not (stripped.startswith("/") and len(stripped) > 2):
        return None
    cmd = stripped.split()[0]
    return cmd if 2 <= len(cmd) <= 30 else None


def _commands_from_history(commands):
    """Comandos slash del history.jsonl (solo proyectos en scope)."""
    hist_file = CLAUDE_DIR / "history.jsonl"
    if not hist_file.exists():
        return commands
    with open(hist_file) as f:
        for line in f:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not is_charly(entry.get("project", "")):
                continue
            cmd = _command_of(entry.get("display", ""))
            if cmd:
                commands[cmd] += 1
    return commands


def collect_skills_and_commands():
    """Extrae skills (cache de Claude) y comandos slash (history.jsonl).

    Vive FUERA de aggregate() para que la agregación sea pura y testeable:
    los resultados se inyectan como parámetros.
    """
    by_skill_total = Counter()
    skills_by_project = defaultdict(Counter)  # proyecto limpio -> skill -> count
    _skills_from_cache(by_skill_total, skills_by_project)
    commands = Counter()
    _commands_from_history(commands)
    return by_skill_total, skills_by_project, commands


def _spend_groups(interactions):
    groups = defaultdict(list)
    for r in interactions:
        groups[(r["tool"], clean_proj_name(r.get("project", "unknown")),
                r.get("session_file"))].append(r)
    return groups


def _spend_identity(tool, proj, sfile, rows):
    """(session_id, fallback): el archivo ES la sesión; sin session_file
    (claude_cache_only) → id de reserva (hash)."""
    import hashlib
    if sfile:
        return Path(sfile).stem, False
    seed = f"{tool}|{proj}|{rows[0]['timestamp']}"
    return ("cache-only-"
            + hashlib.sha1(seed.encode()).hexdigest()[:12], True)


def _spend_tokens(rows):
    return {
        "in": sum(r.get("input_tokens", 0) or 0 for r in rows),
        "out": sum(r.get("output_tokens", 0) or 0 for r in rows),
        "cache_read": sum(r.get("cache_read_tokens", 0) or 0 for r in rows),
        "cache_write": sum(r.get("cache_write_tokens", 0) or 0 for r in rows),
    }


def _spend_costs(rows):
    """(cost_effective, cost_real, cost_reason)."""
    costs = [r.get("cost_effective", 0) or 0 for r in rows]
    if all(c == 0 for c in costs):
        return None, None, ("sin usage.cost en ningún turno (turnos abortados "
                            "o proveedor sin billing por token)")
    cost_eff = round(sum(costs), 8)
    cost_real = round(sum(
        get_sub_cost(r["tool"], r.get("timestamp", ""),
                     r.get("cost_effective", 0) or 0)[0]
        for r in rows), 8)
    return cost_eff, cost_real, None


def _spend_provenance(rows):
    # reported SOLO si todos los turnos traen cost.totalFromProvider;
    # mixed si conviven con estimaciones (corpus real: ~1,6% reported).
    reported_flags = [r.get("cost_reported") for r in rows]
    if all(v is True for v in reported_flags):
        return "reported"
    if any(v is True for v in reported_flags):
        return "mixed"
    return "estimated"


def _spend_duration(rows):
    lat_rows = [r for r in rows if r.get("duration_ms") is not None]
    if lat_rows:
        durs = sorted(r["duration_ms"] for r in lat_rows)
        return {"n": len(durs), "p50": _pctl(durs, 0.5),
                "p90": _pctl(durs, 0.9)}, None
    if any("duration_ms" in r for r in rows):
        reason = ("proveedor reporta durationMs pero ningún turno lo trajo "
                  "(turnos abortados o cobertura parcial de latencia)")
    else:
        reason = ("fuente sin telemetría de latencia (durationMs solo existe "
                  "en logs de Pi)")
    return None, reason


def _spend_tool_errors(rows):
    t_calls = sum(r.get("tool_calls") or 0 for r in rows)
    t_errs = sum(r.get("tool_errors") or 0 for r in rows)
    tool_durs = sorted(d for r in rows
                       for d in (r.get("tool_durations_ms") or []))
    return {"calls": t_calls, "errors": t_errs,
            "p50_ms": _pctl(tool_durs, 0.5) if tool_durs else None}


def _spend_quality(rows):
    """coffe-dnx.2: señales de calidad (Grupo A) — latencia, stop_reasons
    y errores de tool. Ausente → null + razón, NUNCA 0 fake."""
    duration_ms, duration_reason = _spend_duration(rows)
    stop_reasons = dict(Counter(
        r["stop_reason"] for r in rows if r.get("stop_reason")))
    return {"duration_ms": duration_ms, "duration_reason": duration_reason,
            "stop_reasons": stop_reasons,
            "tool_errors": _spend_tool_errors(rows)}


def _spend_routing(rows):
    """coffe-dnx.2: enrutamiento (Grupo B) — (model_chain, switches).

    model_change_idx cuenta TODOS los model_change (el 1ro fija el
    modelo inicial y NO es un cambio): switches = idx - 1. Contador
    ingenuo reportaba 100% de sesiones con cambio (100% tienen el
    model_change inicial); sesión de un solo model_change → 0.
    """
    chains = [r["model_chain"] for r in rows if r.get("model_chain")]
    chain = list(chains[-1]) if chains else list(
        dict.fromkeys(r["model_raw"] for r in rows))
    idxs = [r["model_change_idx"] for r in rows
            if r.get("model_change_idx") is not None]
    return chain, (max(0, max(idxs) - 1) if idxs else 0)


def _spend_row(key, rows):
    tool, proj, sfile = key
    rows.sort(key=lambda r: r["timestamp"])
    session_id, fallback = _spend_identity(tool, proj, sfile, rows)
    tokens_sum = _spend_tokens(rows)
    total_tok = sum(tokens_sum.values())
    row = {
        "session_id": session_id,
        "session_id_fallback": fallback,
        "project": proj,
        "day": rows[0]["hour"][:10],  # día local (misma TZ que los buckets)
        "start": rows[0]["timestamp"],
        "end": max(r["timestamp"] for r in rows),
        "crosses_midnight": len({r["hour"][:10] for r in rows}) > 1,
        "source": tool,
        "model": ({r["model_raw"] for r in rows}.pop()
                  if len({r["model_raw"] for r in rows}) == 1 else "mixed"),
        "n_interactions": len(rows),
        "tokens": tokens_sum,
        "cache_share": (round(tokens_sum["cache_read"] / total_tok, 4)
                        if total_tok else None),
    }
    cost_eff, cost_real, cost_reason = _spend_costs(rows)
    row.update({"cost_effective": cost_eff, "cost_real": cost_real,
                "cost_reason": cost_reason,
                "cost_provenance": _spend_provenance(rows)})
    row.update(_spend_quality(rows))
    chain, model_switches = _spend_routing(rows)
    row.update({"model_chain": chain, "model_switches": model_switches})
    return row


def _session_spend(interactions):
    """add-session-spend-report: filas por sesión con coste y tokens.

    Agrupa rows por (tool, project, session_file) — el archivo ES la sesión
    en pi/claude/codex (stem = uuid nativo) y el hilo en amp. Rows sin
    session_file (claude_cache_only) → session_id de reserva (hash),
    session_id_fallback: true. Sesión atribuida al día local de su primera
    interacción (los buckets diarios reparten por interacción — asimetría
    documentada en README). Coste ausente (todos los turnos a 0.0 sin
    tokens) → null con cost_reason, mismo patrón que context_metrics.
    """
    out = [_spend_row(key, rows) for key, rows in _spend_groups(interactions).items()]
    out.sort(key=lambda r: (r["day"], r["project"], r["session_id"]))
    return out


# coffe-dnx.2 (CORR-005): cobertura de latencia por fuente — MEDIDA sobre
# muestra (~400 archivos/provider, nov 2026), nunca extrapolada ni
# maquillada. Los sources sin durationMs (codex/claude-cli/gemini) reportan
# 0: session_spend.duration_ms queda null con duration_reason, no 0 fake.
LATENCY_COVERAGE_MEASURED = {
    "zai": 100.0, "minimax": 100.0, "qwen-token-plan": 100.0, "kimi": 100.0,
    "openrouter": 7.5,
    "openai-codex": 0.0, "claude-cli": 0.0, "google-gemini-cli": 0.0,
}


def _coverage_unpriced(r, fam, ver, priced_cache):
    """True si la versión no tiene tarifa en la fecha de la fila
    (determinista por (fam, ver, día), con cache por corrida)."""
    day = (r.get("timestamp") or "")[:10]
    key = (fam, ver, day)
    if key not in priced_cache:
        try:
            when = date.fromisoformat(day) if len(day) == 10 else None
        except ValueError:
            when = None
        priced_cache[key] = ver not in _pricing_for(when).get(fam, {})
    return priced_cache[key]


def _coverage_row(r, by_family, state):
    fam = r.get("model_family") or "other"
    ver = r.get("model_version") or "unknown"
    toks = ((r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
            + (r.get("cache_read_tokens") or 0) + (r.get("cache_write_tokens") or 0))
    by_family[fam]["tokens"] += toks
    by_family[fam]["rows"] += 1
    state["tokens_total"] += toks
    if fam == "other":
        state["tokens_other"] += toks
    # familia "other" = fallback del mapeo (sin match), no una versión
    # sin tarifa — se cuenta en tokens_other, no aquí.
    if fam != "other" and _coverage_unpriced(r, fam, ver, state["priced_cache"]):
        state["fallback_rows"] += 1
    return state


def _model_mapping_coverage(interactions):
    """coffe-dnx.1: cobertura del mapeo modelo → familia en esta corrida.

    Por familia: tokens (in+out+cache_read+cache_write, misma contabilidad
    de metadata.token_accounting) y filas. tokens_other_pct = % de tokens
    clasificados ("other", ...) — el KPI del ticket: antes del fix era
    ~56,8% (z-ai/glm-5.3-flash caía al fallback). default_rates_fallbacks
    se cuenta POR FILA (versión sin tarifa en model_pricing en la fecha de
    la fila): determinista para tests/golden, independiente del orden en
    que estimate_cost() fue llamada durante la extracción. Las filas
    ("other", ...) — fallback del mapeo, sin match — no cuentan como
    default_rates_fallbacks: no son una versión sin tarifa sino un modelo
    sin mapear, visible y contado en tokens_other_pct. Las llamadas a
    estimate_cost() que usaron default_rates quedan aparte en el contador
    default_rates_fallbacks() (y en el warning stderr).
    """
    by_family = defaultdict(lambda: {"tokens": 0, "rows": 0})
    state = {"tokens_total": 0, "tokens_other": 0, "fallback_rows": 0,
             "priced_cache": {}}
    for r in interactions:
        _coverage_row(r, by_family, state)
    tokens_total = state["tokens_total"]
    tokens_other = state["tokens_other"]
    return {
        "by_family": {fam: dict(st) for fam, st in sorted(by_family.items())},
        "tokens_total": tokens_total,
        "tokens_other": tokens_other,
        "tokens_other_pct": round(tokens_other / tokens_total * 100, 2)
        if tokens_total else 0.0,
        "default_rates_fallbacks": state["fallback_rows"],
        "mapping_source": CONFIG_SOURCE or "builtin-fallback",
    }


# ----------------------------------------------------------------------
# coffe-dnx.8: scorecard de cohorte (benchmark_cohort en config/fpa.json)
# ----------------------------------------------------------------------

# Umbrales de reserva (solo si config/fpa.json no está). EXCL-003:
# provisionales; los de config mandan y el reporte declara provisional.
_FALLBACK_BENCHMARK_COHORT = {
    "name": "cohorte-flash-2026",
    "models": ["glm-5.3-flash", "deepseek-v4-flash", "qwen3.8-flash",
               "qwen3.8-max", "claude-opus-4.5"],
    "reference": "claude-opus-4.5",
}
_FALLBACK_DECISION_RULE = {
    "provisional": True,
    "metric": "cost_per_accepted_task",
    "thresholds": {"cambiar_max_ratio_vs_reference": 0.8,
                   "mantener_max_ratio_vs_reference": 1.0},
}
SCORECARD_AGENT_METRICS_PATH = _REPO_ROOT / "data" / "agent_metrics.json"
# task_flags() de scripts/agent-metrics-tracker.py: bit 1 = aborted, bit 2 =
# error. Tarea "aceptada" = ni abortada ni con error (proxy de resultado —
# provenance estimated; la señal positiva de aceptación no existe todavía).
_SCORECARD_TASK_REJECTED_BITS = 1 | 2
SCORECARD_WINDOWS = ("todo", "ytd", "mes")


def _scorecard_window_filters(interactions):
    """Ventanas de período del scorecard derivadas del corpus (FPA-008:
    sin data no hay ventanas fantasma). todo = todo el corpus; ytd = desde
    el 1-ene del último año con datos; mes = último mes con datos."""
    if not interactions:
        return {}
    last = max(r["timestamp"] for r in interactions)[:10]
    ym = last[:7]
    ytd = last[:4] + "-01-01"
    return {
        "todo": (None, None),
        "ytd": (ytd, None),
        "mes": (None, ym),
    }


def _in_scorecard_window(ts, since, until_prefix):
    d = ts[:10]
    if since and d < since:
        return False
    if until_prefix and d[:7] != until_prefix:
        return False
    return True


def _scorecard_cache_factor(family, version, when=None):
    """cache_factor = cache_read_rate / input_rate del pricing VIGENTE en
    config (nunca un 1.0 silencioso — el bug que motiva el ticket: con 90%
    de cache_read a ~0.1× del input, $/Mtok sobre tokens totales invierte
    el ranking). Devuelve (factor, reason, provenance) — provenance
    reported con tarifa explícita, assumed con fallback default_rates;
    sin tarifa → (None, razón, None): FPA-008."""
    rates = _pricing_for(when).get(family, {}).get(version)
    if rates is None:
        # misma convención que estimate_cost(): fallback a default_rates
        # del config — explícito (provenance assumed), nunca 1.0 silencioso
        dr = DEFAULT_RATES
        if dr.get("input"):
            if dr.get("cache_read"):
                return (round(dr["cache_read"] / dr["input"], 8), None, "assumed")
            return (0.1, "cache_read sin tarifa en default_rates — fallback "
                         "documentado 0.1× input", "assumed")
        return None, (f"sin tarifa en model_pricing para {family}/{version} "
                      f"(config {CONFIG_SOURCE or 'builtin-fallback'})"), None
    cr = rates.get("cache_read")
    if cr is None:
        # misma convención que estimate_cost(): cache_read a 0.1× del input
        return 0.1, ("cache_read sin tarifa explícita en model_pricing — "
                     f"fallback documentado 0.1× input ({family}/{version})"), "assumed"
    return round(cr / rates["input"], 8), None, "reported"


def _scorecard_task_row(i, tasks, days, models):
    m_ix = tasks["model"][i]
    raw = models[m_ix] if isinstance(m_ix, int) and m_ix < len(models) else "?"
    fam, ver = model_details(raw)
    d_ix = tasks["day"][i]
    day = days[d_ix] if isinstance(d_ix, int) and d_ix < len(days) else None
    return (fam, ver), (
        day,
        float(tasks["cost"][i] or 0.0),
        not (int(tasks["f"][i] or 0) & _SCORECARD_TASK_REJECTED_BITS),
    )


def _scorecard_task_rows(tasks, days, models):
    n = min(len(tasks[k]) for k in ("day", "model", "f", "cost"))
    out = defaultdict(list)
    for i in range(n):
        key, row = _scorecard_task_row(i, tasks, days, models)
        out[key].append(row)
    return dict(out), None


def _scorecard_task_load():
    """(artefacto, reason|None) del agent_metrics.json columnar."""
    path = SCORECARD_AGENT_METRICS_PATH
    if not path.exists():
        return {}, f"unavailable: {path} no encontrado (artefacto coffe-dnx.3)"
    try:
        with open(path) as f:
            am = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {}, f"unavailable: {path} ilegible ({exc})"
    tasks = am.get("tasks") or {}
    need = ("day", "model", "f", "cost")
    if any(k not in tasks for k in need):
        return {}, ("unavailable: agent_metrics.json sin columnas de tareas "
                    f"{need} (artefacto coffe-dnx.3 incompleto)")
    return (tasks, am.get("days") or [], am.get("models") or []), None


def _scorecard_task_costs():
    """coffe-dnx.3: coste por tarea y por tarea aceptada, por
    (family, version). Lee el artefacto columnar de agent_metrics.json
    (tasks: day/s/model/f/cost). Aceptada = f sin bits aborted|error
    (proxy — la señal positiva de aceptación no existe aún). Devuelve
    ({(fam, ver): [(day, cost, accepted), ...]}, reason|None)."""
    load, reason = _scorecard_task_load()
    if reason is not None:
        return {}, reason
    tasks, days, models = load
    return _scorecard_task_rows(tasks, days, models)


def _rule_insufficient_ref(todo_entries, cohort, rule):
    """Veredicto datos_insuficientes si falta la referencia; None si está."""
    ref_name = cohort.get("reference")
    cpat = {e["model"]: ((e.get("data") or {}).get("cost_per_accepted_task"))
            for e in todo_entries}
    if ref_name in cpat and cpat.get(ref_name) is not None:
        return None
    reason = (f"referencia {ref_name} sin cost_per_accepted_task "
              "(datos_insuficientes: la comparación no puede calibrarse)")
    per_model = {m: {"verdict": "datos_insuficientes", "reason": reason,
                     "ratio_vs_reference": None}
                 for m in cohort.get("models", [])}
    return "datos_insuficientes", reason, per_model


def _rule_model_verdicts(todo_entries, cohort, rule):
    """(per_model, any_cambiar) por modelo con la referencia calibrada."""
    ref_name = cohort.get("reference")
    thresholds = rule.get("thresholds") or {}
    cambiar_max = thresholds.get("cambiar_max_ratio_vs_reference", 0.8)
    cpat = {e["model"]: ((e.get("data") or {}).get("cost_per_accepted_task"))
            for e in todo_entries}
    ref_cost = cpat[ref_name]
    per_model = {}
    any_cambiar = False
    for m in cohort.get("models", []):
        entry, flagged = _verdict_entry(m, cpat.get(m),
                                        (ref_name, ref_cost, cambiar_max))
        per_model[m] = entry
        any_cambiar = any_cambiar or flagged
    return cpat, per_model, any_cambiar


def _scorecard_rule_verdict(todo_entries, cohort, rule):
    """Veredicto según model_decision_rule sobre cost_per_accepted_task de
    la ventana 'todo'. Per-model: cambiar si ratio <= cambiar_max_ratio;
    mantener si ratio > cambiar_max_ratio; datos_insuficientes si falta
    cost_per_accepted_task propio o de la referencia. El veredicto global
    es datos_insuficientes si CUALQUIER modelo de la cohorte lo está
    (ticket coffe-dnx.8), luego cambiar si algún subject lo es."""
    verdict = _rule_insufficient_ref(todo_entries, cohort, rule)
    if verdict:
        return verdict
    cpat, per_model, any_cambiar = _rule_model_verdicts(todo_entries, cohort, rule)
    if any(v is None for v in cpat.values()):
        return ("datos_insuficientes",
                "falta cost_per_accepted_task en la cohorte "
                "(datos_insuficientes: la regla exige la cohorte completa)",
                per_model)
    if any_cambiar:
        return "cambiar", "al menos un subject de la cohorte cumple el umbral cambiar", per_model
    return "mantener", "ningún subject cumple el umbral cambiar", per_model


def _verdict_entry(m, cost, ref):
    """({verdict, reason, ratio_vs_reference}, any_cambiar) de 1 modelo.
    ref = (ref_name, ref_cost, cambiar_max)."""
    ref_name, ref_cost, cambiar_max = ref
    if cost is None:
        return ({"verdict": "datos_insuficientes",
                 "reason": "sin cost_per_accepted_task en la cohorte "
                           "(cruce task×modelo no joinable o sin tareas)",
                 "ratio_vs_reference": None}, False)
    ratio = round(cost / ref_cost, 4) if ref_cost else None
    if ratio is None:
        return {"verdict": "datos_insuficientes",
                "reason": "referencia con cost_per_accepted_task 0",
                "ratio_vs_reference": None}, False
    if m == ref_name:
        return {"verdict": "mantener",
                "reason": "referencia frontier: no participa del "
                          "ranking (techo comparativo)",
                "ratio_vs_reference": 1.0}, False
    if ratio <= cambiar_max:
        return ({"verdict": "cambiar",
                 "reason": (f"cost_per_accepted_task <= {cambiar_max}× la "
                            f"referencia ({ref_name})"),
                 "ratio_vs_reference": ratio}, True)
    return ({"verdict": "mantener",
             "reason": (f"cost_per_accepted_task > {cambiar_max}× la "
                        f"referencia ({ref_name})"),
             "ratio_vs_reference": ratio}, False)


class _ScorecardCtx:
    """Contexto compartido por las entradas del scorecard.
    parts = (interactions, spend_rows, task_costs, task_reason, unresolved)."""

    def __init__(self, parts):
        (self.interactions, self.spend_rows, self.task_costs,
         self.task_reason, self.unresolved) = parts


def _scorecard_tokens_sums(rows):
    return {
        "in": sum(r.get("input_tokens", 0) or 0 for r in rows),
        "out": sum(r.get("output_tokens", 0) or 0 for r in rows),
        "cache_read": sum(r.get("cache_read_tokens", 0) or 0 for r in rows),
        "cache_write": sum(r.get("cache_write_tokens", 0) or 0 for r in rows),
    }


def _scorecard_when(rows):
    try:
        return date.fromisoformat(max(r["timestamp"][:10] for r in rows))
    except ValueError:
        return None


def _scorecard_tokens(rows):
    tokens = _scorecard_tokens_sums(rows)
    cost = sum(r.get("cost_effective", 0) or 0 for r in rows)
    when = _scorecard_when(rows)
    return tokens, cost, sum(tokens.values()), when


def _scorecard_cost_weighted(pricing, tokens, cost, nulls):
    """cost_weighted_per_1m + (cache_factor, provenance); nulls anotados.
    pricing = (fam, ver, when)."""
    fam, ver, when = pricing
    cache_factor, cf_reason, cf_prov = _scorecard_cache_factor(fam, ver, when)
    if cf_reason:
        nulls.append({"field": "cache_factor", "reason": cf_reason})
    denom = (tokens["in"] + tokens["out"]
             + tokens["cache_read"] * (cache_factor or 0.0))
    if cost > 0 and denom > 0 and cache_factor is not None:
        cost_weighted = round(cost / denom * 1e6, 4)
    else:
        cost_weighted = None
        nulls.append({
            "field": "cost_weighted_per_1m",
            "reason": ("sin coste en la ventana (turnos abortados o "
                       "proveedor sin billing por token)"
                       if cost <= 0 else
                       f"denominador 0 sin cache_factor usable — {cf_reason}"),
        })
    total_tok = sum(tokens.values())
    cost_naive = (round(cost / total_tok * 1e6, 4)
                  if cost > 0 and total_tok else None)
    return cost_weighted, cost_naive, cache_factor, cf_prov


def _task_window_list(ctx, fam_ver, window):
    since, until_prefix = window
    return [t for t in (ctx.task_costs.get(fam_ver) or [])
            if _in_scorecard_window(t[0] or "", since, until_prefix)]


def _task_metrics_empty(ctx, nulls):
    nulls.append({
        "field": "cost_per_task",
        "reason": ctx.task_reason or
                  "sin tareas de este modelo en agent_metrics.json "
                  "para la ventana (cruce task×modelo vacío)",
    })
    if not ctx.task_reason:
        nulls.append({
            "field": "cost_per_accepted_task",
            "reason": "sin tareas de este modelo en la ventana",
        })
    return None, None


def _scorecard_task_metrics(ctx, fam_ver, window, nulls):
    # --- cruce coffe-dnx.3: coste por tarea / tarea aceptada --------------
    tlist = _task_window_list(ctx, fam_ver, window)
    if not tlist:
        return _task_metrics_empty(ctx, nulls)
    n_tasks = len(tlist)
    acc = [t for t in tlist if t[2]]
    cost_per_task = round(sum(t[1] for t in tlist) / n_tasks, 8) if n_tasks else None
    cost_per_acc = (round(sum(t[1] for t in acc) / len(acc), 8)
                    if acc else None)
    if cost_per_acc is None:
        nulls.append({
            "field": "cost_per_accepted_task",
            "reason": "sin tareas aceptadas en la ventana "
                      "(proxy: f sin bits aborted|error)",
        })
    return cost_per_task, cost_per_acc


def _scorecard_latency(rows, nulls):
    # --- coffe-dnx.2: latencia (medida in-corpus sobre las filas) ---------
    lat_rows = sorted(r["duration_ms"] for r in rows
                      if r.get("duration_ms") is not None)
    if lat_rows:
        return {
            "p50_ms": _pctl(lat_rows, 0.5),
            "p90_ms": _pctl(lat_rows, 0.9),
            "coverage_pct": round(len(lat_rows) / len(rows) * 100, 2),
        }
    nulls.append({
        "field": "latency",
        "reason": "sin turnos con durationMs en la ventana "
                  "(cobertura por fuente en "
                  "metadata.latency_coverage)",
    })
    return None


def _scorecard_session_mine(s, wkey):
    """True si la sesión resuelve al modelo y cae en la ventana.
    wkey = (fam, ver, since, until_prefix)."""
    fam, ver, since, until_prefix = wkey
    chain = any(model_details(mid) == (fam, ver)
                for mid in (s.get("model_chain") or []))
    return chain and _in_scorecard_window(s.get("day") or "", since,
                                          until_prefix)


def _scorecard_spend_stats(ctx, fam_ver, window):
    """(switches, api_cost, sub_cost) de las sesiones que resuelven al
    modelo y caen en la ventana. window = (since, until_prefix)."""
    mine = [s for s in ctx.spend_rows
            if _scorecard_session_mine(
                s, (*fam_ver, *window))]
    switches = sum(s["model_switches"] for s in mine)
    api_cost = sum(s.get("cost_effective") or 0 for s in mine)
    sub_cost = sum(
        max(0.0, (s.get("cost_real") or 0) - (s.get("cost_effective") or 0))
        for s in mine)
    return switches, api_cost, sub_cost


def _scorecard_tokens_only(rows):
    return {
        "in": sum(r.get("input_tokens", 0) or 0 for r in rows),
        "out": sum(r.get("output_tokens", 0) or 0 for r in rows),
        "cache_read": sum(r.get("cache_read_tokens", 0) or 0 for r in rows),
        "cache_write": sum(r.get("cache_write_tokens", 0) or 0 for r in rows),
    }


def _scorecard_data_core(rows):
    stop_reasons = dict(Counter(
        r["stop_reason"] for r in rows if r.get("stop_reason")))
    errs = sum(r.get("tool_errors") or 0 for r in rows)
    return (_scorecard_tokens_only(rows), stop_reasons,
            stop_reasons.get("aborted", 0), len(rows), errs)


def _scorecard_prov(cf_prov, latency):
    return {
        "interactions": "reported",
        "cost_effective": "estimated",
        "cost_weighted_per_1m": "estimated",
        "cost_per_1m_total_tokens": "estimated",
        "cache_factor": cf_prov,
        "cache_share": "reported",
        "tokens": "reported",
        "latency": "reported" if latency is not None else None,
        "stop_reasons": "reported",
        "aborts_per_100_turns": "reported",
        "tool_errors_per_100_turns": "reported",
    }


def _scorecard_data(rows, metrics, nulls):
    """Bloque data del entry (sin el cruce de tareas/sesiones, que se
    inserta después). metrics = (cost, total_tok, cost_weighted,
    cost_naive, cache_factor, cf_prov)."""
    cost, total_tok, cost_weighted, cost_naive, cache_factor, cf_prov = metrics
    tokens, stop_reasons, aborts, turns, t_errs = _scorecard_data_core(rows)
    latency = _scorecard_latency(rows, nulls)
    return {
        "interactions": turns,
        "cost_effective": round(cost, 8),
        "cost_weighted_per_1m": cost_weighted,
        "cost_per_1m_total_tokens": cost_naive,
        "cache_factor": cache_factor,
        "cache_share": (round(tokens["cache_read"] / total_tok, 4)
                        if total_tok else None),
        "tokens": tokens,
        "latency": latency,
        "stop_reasons": stop_reasons,
        "aborts_per_100_turns": round(aborts / turns * 100, 4) if turns else None,
        "tool_errors_per_100_turns": (round(t_errs / turns * 100, 4)
                                      if turns else None),
        "provenance": _scorecard_prov(cf_prov, latency),
        "nulls": nulls,
    }


def _scorecard_unresolved_entry(name, fam, ver, win):
    return {"model": name, "family": fam, "version": ver,
            "window": win, "data": None,
            "reason": ("modelo de la cohorte sin match en "
                       "model_mapping (coffe-dnx.1)"), "nulls": []}


def _scorecard_window_entry(ctx, name, wkey):
    """Entrada de scorecard para 1 modelo × ventana. wkey = (win, since,
    until_prefix)."""
    win, since, until_prefix = wkey
    entry = {"model": name, "family": None, "version": None,
             "window": win, "data": None, "reason": None, "nulls": []}
    fam, ver = model_details(name)
    entry["family"] = fam
    entry["version"] = ver
    if name in ctx.unresolved:
        return _scorecard_unresolved_entry(name, fam, ver, win)
    rows = [r for r in ctx.interactions
            if (r.get("model_family"), r.get("model_version")) == (fam, ver)
            and _in_scorecard_window(r["timestamp"], since, until_prefix)]
    if not rows:
        entry["reason"] = (
            "sin interacciones de este modelo en la ventana "
            f"(mapeo {fam}/{ver}) — patrón FPA-008: null con razón, "
            "nunca 0")
        return entry
    nulls = []
    tokens, cost, total_tok, when = _scorecard_tokens(rows)
    cost_weighted, cost_naive, cache_factor, cf_prov = _scorecard_cost_weighted(
        (fam, ver, when), tokens, cost, nulls)
    cost_per_task, cost_per_acc = _scorecard_task_metrics(
        ctx, (fam, ver), (since, until_prefix), nulls)
    data = _scorecard_data(rows, (cost, total_tok, cost_weighted, cost_naive,
                                  cache_factor, cf_prov), nulls)
    switches, api_cost, sub_cost = _scorecard_spend_stats(
        ctx, (fam, ver), (since, until_prefix))
    data["cost_per_task"] = cost_per_task
    data["cost_per_accepted_task"] = cost_per_acc
    data["model_switches"] = switches
    data["subscription_vs_api"] = {
        "subscription": round(sub_cost, 8),
        "api": round(api_cost, 8),
        "provenance": "estimated",
    }
    data["provenance"]["cost_per_task"] = "estimated"
    data["provenance"]["cost_per_accepted_task"] = "estimated"
    data["provenance"]["model_switches"] = "estimated"
    data["provenance"]["subscription_vs_api"] = "estimated"
    entry["data"] = data
    return entry


def _scorecard_meta_base(cohort, rule):
    return {
        "cohort_source": CONFIG_SOURCE or "builtin-fallback",
        "cohort_name": cohort.get("name"),
        "models": list(cohort.get("models") or []),
        "reference": cohort.get("reference"),
        "cache_factor_note": (
            "cost_weighted_per_1m = cost / (input_fresh + output + "
            "cache_read × cache_factor) × 1e6; cache_factor = "
            "cache_read_rate / input_rate del pricing vigente en config — "
            "nunca un 1.0 silencioso"),
        "reason": None,
        "rule": rule,
        "rule_verdict": None,
        "rule_verdict_reason": None,
        "rule_verdict_per_model": None,
    }


def _scorecard_entries(ctx, cohort, windows):
    entries = []
    todo_entries = []
    for name in cohort["models"]:
        for win, (since, until_prefix) in (windows.items() if windows
                                           else (("todo", (None, None)),)):
            entry = _scorecard_window_entry(ctx, name, (win, since, until_prefix))
            entries.append(entry)
            if win == "todo":
                todo_entries.append(entry)
    return entries, todo_entries


def _model_scorecard(interactions, spend_rows):
    """coffe-dnx.8: bloque model_scorecard[] del reporte.

    Para cada modelo de benchmark_cohort (config/fpa.json) × ventana
    (todo/ytd/mes): cost_weighted_per_1m = cost / (input_fresh + output +
    cache_read × cache_factor) — normalizado por COMPOSICIÓN de tokens, no
    sobre tokens totales (CORR-003: con 90% de cache_read a ~0.1× del
    input, $/Mtok-naive invierte el ranking). cache_factor es explícito del
    pricing config. Cruce con coffe-dnx.3 (cost_per_task /
    cost_per_accepted_task) y coffe-dnx.2 (latencia, stop_reasons, abortos,
    errores de tool, switches, cache_share). Toda cifra lleva provenance
    (reported|estimated|assumed); lo ausente → null + razón en nulls[]
    (FPA-008, nunca 0 fake). Devuelve (entries, metadata_scorecard)."""
    cfg = _FPA_CONFIG or {}
    cohort = cfg.get("benchmark_cohort") or _FALLBACK_BENCHMARK_COHORT
    rule = cfg.get("model_decision_rule") or _FALLBACK_DECISION_RULE
    meta = _scorecard_meta_base(cohort, rule)
    if not cohort.get("models"):
        meta["reason"] = "benchmark_cohort sin modelos en config"
        return [], meta

    # Resolución cohorte → (family, version) vía el MISMO mapeo config-driven
    # que el resto del reporte (coffe-dnx.1).
    unresolved = [name for name in cohort["models"]
                  if model_details(name) == ("other", name[:20])]
    task_costs, task_reason = _scorecard_task_costs()
    windows = _scorecard_window_filters(interactions)
    ctx = _ScorecardCtx((interactions, spend_rows, task_costs, task_reason,
                         set(unresolved)))
    entries, todo_entries = _scorecard_entries(ctx, cohort, windows)

    verdict, v_reason, per_model = _scorecard_rule_verdict(
        todo_entries, cohort, rule)
    meta["rule_verdict"] = verdict
    meta["rule_verdict_reason"] = v_reason
    meta["rule_verdict_per_model"] = per_model
    meta["task_costs_reason"] = task_reason
    return entries, meta


class _AggState:
    """Contenedores de acumulación de aggregate() (un lugar, sin copiar)."""

    def __init__(self):
        self.hourly = {}
        self.daily = {}
        self.monthly = {}
        self.by_project = {}
        self.hour_projects = defaultdict(set)  # hora -> proyectos distintos activos
        self.day_projects = defaultdict(set)   # día -> proyectos distintos activos
        self.proj_day = defaultdict(Counter)   # proyecto -> {día: interacciones}
        self.row_kinds = defaultdict(Counter)  # FPA-140: kinds desde filas
        # FPA-011/019: breakdown mensual por tool y modelo
        self.month_tools = defaultdict(lambda: defaultdict(lambda: {
            "interactions": 0, "cost_effective": 0.0, "cost_real": 0.0,
            "models": defaultdict(lambda: {"interactions": 0, "cost_effective": 0.0})}))
        self.month_models = defaultdict(lambda: defaultdict(lambda: {
            "interactions": 0, "cost_effective": 0.0,
            "in": 0, "out": 0, "cache_read": 0, "cache_write": 0}))
        # FPA-012/021: proyecto × mes y proyecto × modelo
        # add-project-cost-token-timeline (d1): tokens por proyecto×mes para
        # la timeline de volumen y coste del dashboard (d2)
        self.project_monthly = defaultdict(lambda: defaultdict(lambda: {
            "interactions": 0, "cost_effective": 0.0, "cost_real": 0.0,
            "tokens": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}}))
        # add-session-spend-report: gasto por proyecto × día (sparse, solo
        # días con actividad; misma semántica de coste que los buckets).
        # Aditivo: project_daily (Gantt) y project_monthly no cambian.
        self.project_daily_spend = defaultdict(lambda: defaultdict(lambda: {
            "interactions": 0, "cost_effective": 0.0, "cost_real": 0.0,
            "tokens": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}}))
        self.project_models = defaultdict(lambda: defaultdict(lambda: {
            "interactions": 0, "cost_effective": 0.0}))


def _agg_row_kind(st, r):
    kind_key = (r.get("timestamp") or "")[:7]
    if kind_key:
        st.row_kinds.setdefault(kind_key, Counter())[
            r.get("kind", "assistant_turn")] += 1
    return st


def _agg_month_tool(st, m, r, real):
    # FPA-011: tool/model del mes
    mt = st.month_tools[m][r["tool"]]
    mt["interactions"] += 1
    mt["cost_effective"] += r.get("cost_effective", 0) or 0
    mt["cost_real"] += real
    mtm = mt["models"][r["model_raw"]]
    mtm["interactions"] += 1
    mtm["cost_effective"] += r.get("cost_effective", 0) or 0
    return st


def _agg_month_model(st, m, r):
    mm = st.month_models[m][r["model_raw"]]
    mm["interactions"] += 1
    mm["cost_effective"] += r.get("cost_effective", 0) or 0
    mm["in"] += r.get("input_tokens", 0) or 0
    mm["out"] += r.get("output_tokens", 0) or 0
    mm["cache_read"] += r.get("cache_read_tokens", 0) or 0
    mm["cache_write"] += r.get("cache_write_tokens", 0) or 0
    return st


def _agg_project_tokens(bucket, r):
    bucket["tokens"]["in"] += r.get("input_tokens", 0) or 0
    bucket["tokens"]["out"] += r.get("output_tokens", 0) or 0
    bucket["tokens"]["cache_read"] += r.get("cache_read_tokens", 0) or 0
    bucket["tokens"]["cache_write"] += r.get("cache_write_tokens", 0) or 0
    return bucket


def _agg_project_month(st, key, r, real):
    """key = (proj, mes)."""
    proj, m = key
    pmm = st.project_monthly[proj][m]
    pmm["interactions"] += 1
    pmm["cost_effective"] += r.get("cost_effective", 0) or 0
    pmm["cost_real"] += real
    _agg_project_tokens(pmm, r)
    return pmm


def _agg_project_daily_spend(st, key, r, real):
    """key = (proj, día)."""
    proj, d = key
    pds = st.project_daily_spend[proj][d]
    pds["interactions"] += 1
    pds["cost_effective"] += r.get("cost_effective", 0) or 0
    pds["cost_real"] += real
    _agg_project_tokens(pds, r)
    return pds


def _agg_row_bucket(st, key, r, real):
    """Buckets hourly/daily/monthly: crear si faltan y sumar la fila."""
    h, d, m = key
    if h not in st.hourly:
        st.hourly[h] = HourlyBucket()
    if d not in st.daily:
        st.daily[d] = Bucket()
    if m not in st.monthly:
        st.monthly[m] = Bucket()
    st.hourly[h].add(r, real)
    st.daily[d].add(r, real)
    st.monthly[m].add(r, real)
    return st


def _agg_row_projects(st, key, r, real):
    """Proyectos activos por hora/día, bucket por proyecto y breakdowns."""
    h, d, proj = key
    st.hour_projects[h].add(proj)
    st.day_projects[d].add(proj)
    st.proj_day[proj][d] += 1
    if proj not in st.by_project:
        st.by_project[proj] = Bucket()
    st.by_project[proj].add(r, real)
    _agg_month_tool(st, d[:7], r, real)
    _agg_project_month(st, (proj, d[:7]), r, real)
    _agg_project_daily_spend(st, (proj, d), r, real)
    return st


def _agg_row_seen(pp, ts):
    if pp.first_seen is None or ts < pp.first_seen:
        pp.first_seen = ts
    if pp.last_seen is None or ts > pp.last_seen:
        pp.last_seen = ts
    return pp


def _agg_row(st, r):
    h = r["hour"]
    d = h[:10]
    m = d[:7]
    real, _, _ = get_sub_cost(r["tool"], r.get("timestamp", ""),
                              r.get("cost_effective", 0) or 0)

    _agg_row_bucket(st, (h, d, m), r, real)

    proj = clean_proj_name(r.get("project", "unknown"))
    _agg_row_projects(st, (h, d, proj), r, real)
    _agg_month_model(st, m, r)
    pmo = st.project_models[proj][r["model_raw"]]
    pmo["interactions"] += 1
    pmo["cost_effective"] += r.get("cost_effective", 0) or 0
    _agg_row_kind(st, r)
    # first/last seen
    _agg_row_seen(st.by_project[proj], r["timestamp"])
    return st


def _agg_apply_fees(monthly_dicts, sub_fees, monthly, daily):
    # --- Subscription fees ---
    for m_key, fee in sub_fees.items():
        if m_key in monthly:
            monthly[m_key].cost_real += fee
            monthly_dicts[m_key]["cost_real"] = round(monthly[m_key].cost_real, 2)
            monthly_dicts[m_key]["subscription_fees"] = fee
        for d_key, d_data in daily.items():
            if d_key[:7] == m_key:
                d_data.cost_real += fee / 30.0  # prorated roughly
    return monthly_dicts


def _agg_tracker_split(monthly_dicts):
    # --- coffe-a31.2: contabilidad separada tracker vs fee implícito ---
    # La suma mensual de cost_real mezcla la parte tracker (p2p real) con los
    # fees implícitos del calendario; metadata (sumatoria horaria) solo tenía
    # la parte tracker → divergencia 611.62 vs 381.62. Se emiten ambas partes
    # por mes; cost_total_real pasa a ser la suma mensual (una sola cuenta).
    for mo in monthly_dicts.values():
        fee_mes = mo.get("subscription_fees", 0.0)
        mo["cost_real_tracker"] = round(mo["cost_real"] - fee_mes, 2)
        mo["pay_per_token_provenance"] = "assumed"  # puede pasar a reported abajo
    return monthly_dicts


def _agg_charges():
    # --- coffe-a31.2 (CRG-F1): ledger de cargos reales (FPA-013 reported) ---
    try:
        charges_by_month, charges_total, charges_p2p = cargar_charges()
        return charges_by_month, charges_total, charges_p2p, True, None
    except (OSError, ValueError, json.JSONDecodeError) as e:
        return ({}, 0.0, {}, False,
                f"unavailable: ledger ilegible ({e})")


def _agg_month_tools_emit(month_tools):
    # FPA-011: breakdown por tool con coste (interacciones + coste efectivo)
    return {t: {
        "interactions": st["interactions"],
        "cost_effective": round(st["cost_effective"], 8),
        "cost_real": round(st["cost_real"], 8),
        "models": {mm: {"interactions": ms["interactions"],
                        "cost_effective": round(ms["cost_effective"], 8)}
                   for mm, ms in sorted(st["models"].items())},
    } for t, st in sorted(month_tools.items())}


def _agg_month_dicts_emit(monthly_dicts, st, opts, charges):
    charges_ok, charges_p2p = charges
    interaction_kinds = opts.get("interaction_kinds")
    outcomes = opts.get("outcomes")
    # --- Emisiones F2 (coffe-lat.3) en monthly_dicts ---
    for m_key, mo in monthly_dicts.items():
        mo["tools"] = _agg_month_tools_emit(st.month_tools.get(m_key, {}))
        # FPA-011: breakdown por modelo con coste
        mo["models"] = {m: {
            "interactions": mm_st["interactions"],
            "cost_effective": round(mm_st["cost_effective"], 8),
        } for m, mm_st in sorted(st.month_models.get(m_key, {}).items())}
        # FPA-019: tokens por mes y modelo (in/out/cache_read/cache_write)
        mo["tokens_by_model"] = {m: {
            "in": mm_st["in"], "out": mm_st["out"],
            "cache_read": mm_st["cache_read"],
            "cache_write": mm_st["cache_write"],
        } for m, mm_st in sorted(st.month_models.get(m_key, {}).items())}
        _agg_month_p2p(mo, m_key, charges_ok, charges_p2p)
        # FPA-140: kinds del mes = filas + inyectados (user prompts)
        mo["interaction_kinds"] = dict(Counter(
            {**st.row_kinds.get(m_key, {}),
             **(interaction_kinds or {}).get(m_key, {})}))
        # FPA-014: outcomes donde haya GitHub token (vacío si no, con razón)
        mo["outcomes_by_project"] = (outcomes or {}).get(m_key, {})
    return monthly_dicts


def _agg_month_p2p(mo, m_key, charges_ok, charges_p2p):
    # FPA-013 (coffe-a31.2): cargas pay-per-token del mes. Con cargas reales
    # del ledger (créditos/reembolsos — FPA-013 deja de ser assumed) la cifra
    # es *reported*; si no, el estimado tracker (assumed). Los fees
    # implícitos del calendario NO se mezclan acá. p2p real del mes según el
    # ledger: por PRESENCIA de cargas, no por monto ≠ 0 (un crédito + un
    # refund que netean $0 siguen siendo facturas reales → reported)
    tiene_p2p = charges_ok and any(
        m_key in meses for meses in charges_p2p.values())
    if tiene_p2p:
        mo["pay_per_token_charges"] = round(sum(
            meses.get(m_key, 0.0) for meses in charges_p2p.values()), 8)
        mo["pay_per_token_provenance"] = "reported"
    else:
        mo["pay_per_token_charges"] = round(mo["cost_real_tracker"], 8)
    return mo


def _agg_concurrency(hour_projects, switches_by_day, sessions):
    # --- FPA-120: concurrencia etiquetada ---
    active_hours = len(hour_projects)
    total_switches = sum(switches_by_day.values())
    peak_projects = max((len(ps) for ps in hour_projects.values()), default=0)
    peak_sessions = _peak_simultaneous_sessions(sessions)
    return {
        "distinct_projects_per_hour": {
            "measure": "parallel-agent",  # proyectos/hora puede inflarse por agentes
            "peak": peak_projects,
            "avg": round(sum(len(ps) for ps in hour_projects.values()) / active_hours, 2)
            if active_hours else 0,
        },
        "peak_simultaneous_sessions": {
            "measure": "parallel-agent",
            "peak": peak_sessions,  # None → n/a en el dashboard (FPA-008)
            "reason": None if peak_sessions is not None
            else "sesiones sin timestamps completos (first_ts/last_ts)",
        },
        "project_switches_per_active_hour": {
            "measure": "human-context-switching",
            "value": round(total_switches / active_hours, 2) if active_hours else 0,
        },
    }


def _agg_excl_n(e):
    # fila completa (1 interacción) o resumen por proyecto (contador)
    return e["interactions"] if e.get("interactions") is not None else 1


def _agg_filtered_out(excluded, hourly):
    # --- FPA-141: excluidos por el filtro charly ---
    excluded = excluded or []
    filtered_interactions = sum(_agg_excl_n(e) for e in excluded)
    filtered_cost = sum(
        (e.get("cost_effective") or 0) * _agg_excl_n(e) for e in excluded
        if e.get("cost_effective") is not None)
    total_all = sum(b.interactions for b in hourly.values()) + filtered_interactions
    return {
        "interactions": filtered_interactions,
        "cost_effective": round(filtered_cost, 2) if filtered_cost else 0.0,
        "by_tool": dict(Counter({e.get("tool", "?"): _agg_excl_n(e)
                                 for e in excluded})),
        "share": round(filtered_interactions / total_all, 4) if total_all else 0.0,
    }


def _agg_sessions_monthly(sessions):
    # --- Sesiones por mes (KPIs: coste por sesión, autonomous share) ---
    sessions_monthly = defaultdict(lambda: {"total": 0, "with_agent": 0})
    for s in sessions:
        ym = (s.get("first_ts") or "")[:7]
        if len(ym) == 7:
            sessions_monthly[ym]["total"] += 1
            if s.get("has_agent"):
                sessions_monthly[ym]["with_agent"] += 1
    return sessions_monthly


def _agg_metadata_totals(st, monthly_dicts, sub_fees, tracker_total):
    return {
        "total_interactions": sum(b.interactions for b in st.hourly.values()),
        "total_input_tokens": sum(b.input_tokens for b in st.hourly.values()),
        "total_output_tokens": sum(b.output_tokens for b in st.hourly.values()),
        "total_cache_read_tokens": sum(b.cache_read_tokens for b in st.hourly.values()),
        "total_cache_write_tokens": sum(b.cache_write_tokens for b in st.hourly.values()),
        "cost_total_effective": round(sum(b.cost_effective for b in st.hourly.values()), 2),
        # coffe-a31.2: unificada con la suma mensual (antes: sumatoria
        # horaria sin fees implícitos → divergencia 381.62 vs 611.62).
        # Los fees implícitos del calendario quedan identificados aparte
        # (subscription_fees_by_month) y NO son cash (FPA-082).
        "cost_total_real": round(sum(mo["cost_real"] for mo in monthly_dicts.values()), 2),
        "cost_real_total_tracker": tracker_total,
        "subscription_fees": round(sum(sub_fees.values()), 2),
        "total_hours": len(st.hourly),
        "total_days": len(st.daily),
        "total_months": len(st.monthly),
        "total_projects": len(st.by_project),
    }


def _agg_metadata_charges(charges):
    charges_total, charges_ok, charges_reason = charges
    return {
        # coffe-a31.2: cash cost real del ledger (provenance reported).
        # Medida separada del efectivo: jamás sumarlas (FPA-002).
        "charges_total_real": round(charges_total, 2),
        "charges_source": CHARGES_SOURCE,
        "charges_provenance": "reported" if charges_ok else "unavailable",
        "charges_reason": charges_reason,  # FPA-008: n/a con razón
    }


def _agg_metadata_energy():
    # coffe-7mj.1: energía estimada — SIEMPRE assumed (ningún proveedor
    # reporta kWh; coeficientes de laboratorio versionados en
    # config/fpa.json). Unavailable con razón solo en fallback sin config
    # (FPA-008).
    out = {
        "energy_provenance": ("assumed" if _FPA_CONFIG is not None
                              else "unavailable"),
        "energy_config_source": CONFIG_SOURCE if _FPA_CONFIG is not None else None,
        "energy_cache_read_factor": (
            _ENERGY_COEFFS["cache_read_energy_factor"]
            if _FPA_CONFIG is not None else None),
        # coffe-5ng: extremos de la banda de sensibilidad de caché que
        # emite cada mes en energy_kwh_band (0% y 100% de acierto).
        **({"energy_band_cache_factors": [0.0, 1.0]}
           if _FPA_CONFIG is not None else {}),
        "energy_method": (
            "tokens (input+output+cache_write a peso completo; "
            "cache_read × cache_read_energy_factor) × J/token del tier "
            "(config energy_coefficients versionado) / 3.6e6 — "
            "coeficientes de laboratorio (Luccioni et al. / AI Energy "
            "Score), orden de magnitud, nunca medición"),
        **({"energy_reason": "config/fpa.json no encontrado (fallback)"}
           if _FPA_CONFIG is None else {}),
    }
    return out


def _agg_metadata(st, interactions, bundle):
    if interactions:
        date_range = {
            "start": min(r["timestamp"] for r in interactions)[:10],
            "end": max(r["timestamp"] for r in interactions)[:10],
        }
    else:
        date_range = {"start": None, "end": None}
    out = {
        "date_range": date_range,
        "filter": "in-scope" if CHARLY_FILTER else "all",
        "pay_per_token_note": (
            "pay_per_token_charges es *reported* cuando el ledger tiene "
            "cargas pay-per-token reales del mes (créditos/reembolsos, "
            "FPA-013); *assumed* (estimado tokens × pricing) si no"),
        "outcomes": bundle["outcomes_reason"] or "unavailable: no emisionado en esta corrida",  # FPA-014
        "token_accounting": (
            "cache_read/cache_write se reportan aparte de input/output. "
            "cache_read no se factura a input rate (10x mas barato); "
            "total tokens = input + output + cache_read + cache_write"
        ),
        "timezone": str(LOCAL_TZ),  # FPA-142: TZ usada en buckets hourly/daily
        # coffe-mbz: fuente de verdad de suscripciones/pricing
        "config_source": CONFIG_SOURCE or "builtin-fallback",
        # coffe-dnx.1: cobertura del mapeo modelo → familia (visible y
        # contado; el fallback "other" ya no es un agujero silencioso)
        "model_mapping_coverage": _model_mapping_coverage(interactions),
        # coffe-dnx.2: cobertura de latencia por fuente (medida, CORR-005)
        "latency_coverage": {
            "measured": dict(LATENCY_COVERAGE_MEASURED),
            "method": ("% de turnos con durationMs por provider — muestreo "
                       "CORR-005 (~400 archivos/provider, nov 2026); "
                       "medido, nunca extrapolado ni maquillado"),
        },
        # coffe-dnx.8: regla de decisión del scorecard de cohorte
        "scorecard": bundle["scorecard_meta"],
    }
    out.update(_agg_metadata_totals(st, bundle["monthly_dicts"],
                                    bundle["sub_fees"], bundle["tracker_total"]))
    out.update(_agg_metadata_charges(bundle["charges"]))
    out.update(_agg_metadata_energy())
    return out


def _clean_json(o):
    if isinstance(o, defaultdict):
        return {k: _clean_json(v) for k, v in o.items()}
    if isinstance(o, Counter):
        return dict(o.most_common())
    return o


def _agg_project_sections(st):
    return {
        "projects": {p: (b.to_dict() | {
            "first_seen": (b.first_seen or "")[:10],
            "last_seen": (b.last_seen or "")[:10],
            "skills": dict(getattr(b, "skills", {}).most_common()),
        }) for p, b in sorted(st.by_project.items(),
                              key=lambda x: -x[1].cost_effective)},
        "project_daily_spend": {
            p: {day: {
                "interactions": v["interactions"],
                "cost_effective": round(v["cost_effective"], 8),
                "cost_real": round(v["cost_real"], 8),
                "tokens": dict(v["tokens"]),
            } for day, v in sorted(days.items())}
            for p, days in sorted(st.project_daily_spend.items())},
        "project_monthly": {p: dict(ms) for p, ms in st.project_monthly.items()},
        "project_models": {p: {m: {"interactions": v["interactions"],
                                   "cost_effective": round(v["cost_effective"], 8)}
                              for m, v in sorted(models.items())}
                           for p, models in st.project_models.items()},
    }


def _agg_context_default():
    # ctx-tracker (coffe-kup): sección requerida por el schema — inyectada
    # por main() desde extract_context_metrics(); si no viene, missing
    # honesto (nunca array fantasma)
    return {
        "available": False,
        "reason": "unavailable: extract_context_metrics() no corrido en "
                  "esta agregación",
        "sessions": [], "monthly_model": [],
        # meta completo igual: la sección es requerida por el schema y
        # los reports sintéticos se validan contra él
        "meta": {
            "chars_per_token": DEFAULT_CONTEXT_THRESHOLDS["chars_per_token"],
            "chars_per_token_tag": "assumed",
            "chars_per_token_bias": (
                "subestima el contexto real: español/código ~3 "
                "chars/token y overhead de serialización"),
            "validation_ratio_median": None,
            "thresholds": dict(DEFAULT_CONTEXT_THRESHOLDS),
            "stubs": 0,
            "excluded_pathological": 0,
        },
    }


def _agg_skills(opts, by_project):
    # --- Skills (inyectados; default: colección en vivo) ---
    skills_total = opts.get("skills_total")
    skills_by_project = opts.get("skills_by_project")
    commands = opts.get("commands")
    if skills_total is None or skills_by_project is None or commands is None:
        skills_total, skills_by_project, commands = collect_skills_and_commands()
    skills_total = Counter(skills_total)
    commands = Counter(commands)
    for proj_clean, sk in skills_by_project.items():
        if proj_clean in by_project:
            by_project[proj_clean].skills = Counter(sk)
    return skills_total, skills_by_project, commands


def _agg_report_sections(st, bundle):
    return {
        "hourly": {h: b.to_dict() | {"projects_active": getattr(b, "projects_active", 0)}
                   for h, b in st.hourly.items()},
        "daily": {d: b.to_dict() for d, b in st.daily.items()},
        "monthly": dict(sorted(bundle["monthly_dicts"].items())),
        "skills": dict(bundle["skills_total"].most_common(50)),
        "commands": dict(bundle["commands"].most_common(30)),
        "sessions": bundle["session_stats"],
        "context_metrics": bundle["context_metrics"] or _agg_context_default(),
        "multitasking": bundle["multitasking"],
        "project_daily": bundle["project_daily"],
        "session_spend": bundle["spend_rows"],
        # coffe-dnx.8: scorecard de cohorte (misma sesión-gasto como insumo)
        "model_scorecard": bundle["model_scorecard"],
        "concurrency": bundle["concurrency"],
        "filtered_out": bundle["filtered_out"],
        "sessions_monthly": dict(sorted(bundle["sessions_monthly"].items())),
        "subscription_config": SUBSCRIPTIONS,
        "subscription_fees_by_month": bundle["sub_fees"],
        "metadata": bundle["metadata"],
    }


def _agg_report_charges(bundle):
    charges_by_month, charges_ok = bundle["charges_by_month"], bundle["charges_ok"]
    return {
        # coffe-a31.2: cash real del ledger por proveedor y mes (provenance
        # reported), y el insumo de reconciliación FPA-082 (real vs fee
        # implícito del calendario vs efectivo estimado)
        "charges_real_by_month": charges_by_month,
        "charges_reconciliation_by_month": reconciliation_charges(
            charges_by_month, bundle["monthly_dicts"]) if charges_ok else {},
        # coffe-mbz: config de pricing cargado (no constantes muertas)
        "model_pricing_config": {
            "default_rates": DEFAULT_RATES,
            "cache_write_factor": _CACHE_WRITE_FACTOR,
            "versions": _PRICING_VERSIONS,
        },
    }


def _agg_multitasking(st, interactions):
    """(multitasking, switches_by_day): cambios de contexto + proyectos
    activos por hora."""
    switches_by_day = _context_switches(interactions)
    multitasking = _multitasking_block(st.hour_projects, st.day_projects,
                                       {d: b.to_dict() for d, b in st.daily.items()},
                                       switches_by_day)
    # Conteo de proyectos activos por hora
    for h, ps in st.hour_projects.items():
        if h in st.hourly:
            st.hourly[h].projects_active = len(ps)
    return multitasking, switches_by_day


def _agg_fees_and_charges(st, monthly_dicts, opts):
    """(sub_fees, tracker_total, charges) tras fees, split tracker,
    emisiones F2 y energía."""
    sub_fees = calc_subscription_fees(monthly_dicts)
    _agg_apply_fees(monthly_dicts, sub_fees, st.monthly, st.daily)
    _agg_tracker_split(monthly_dicts)
    tracker_total = round(sum(b.cost_real for b in st.hourly.values()), 2)

    charges_by_month, charges_total, charges_p2p, charges_ok, charges_reason = (
        _agg_charges())

    # --- Emisiones F2 (coffe-lat.3) en monthly_dicts ---
    _agg_month_dicts_emit(monthly_dicts, st, opts, (charges_ok, charges_p2p))

    # --- coffe-7mj.1: energía estimada (kWh) por mes y modelo (aditivo) ---
    _energy_for_month(monthly_dicts)
    return sub_fees, tracker_total, (charges_by_month, charges_total,
                                     charges_ok, charges_reason)


def _agg_bundle(st, interactions, sessions, opts):
    """Arma el bundle intermedio del reporte (todo lo que comparten
    secciones, metadata y cargos)."""
    multitasking, switches_by_day = _agg_multitasking(st, interactions)
    monthly_dicts = {m: b.to_dict(detail=True) for m, b in st.monthly.items()}
    sub_fees, tracker_total, charges = _agg_fees_and_charges(st, monthly_dicts,
                                                             opts)
    charges_by_month, charges_total, charges_ok, charges_reason = charges

    # --- Skills (inyectados; default: colección en vivo) ---
    skills_total, skills_by_project, commands = _agg_skills(opts, st.by_project)

    # --- Sessions ---
    session_stats = _session_stats(sessions, commands)

    # --- project_daily ---
    project_daily = _project_daily(st.by_project, st.day_projects, st.proj_day)

    # coffe-dnx.8: session_spend se calcula UNA vez (bloque del reporte +
    # insumo del scorecard de cohorte: latencia/switches/subscription split)
    spend_rows = _session_spend(interactions)
    model_scorecard, scorecard_meta = _model_scorecard(interactions, spend_rows)

    return {
        "monthly_dicts": monthly_dicts,
        "sub_fees": sub_fees,
        "skills_total": skills_total,
        "skills_by_project": skills_by_project,
        "commands": commands,
        "context_metrics": opts.get("context_metrics"),
        "multitasking": multitasking,
        "project_daily": project_daily,
        "spend_rows": spend_rows,
        "model_scorecard": model_scorecard,
        "concurrency": _agg_concurrency(st.hour_projects, switches_by_day, sessions),
        "filtered_out": _agg_filtered_out(opts.get("excluded"), st.hourly),
        "sessions_monthly": _agg_sessions_monthly(sessions),
        "session_stats": session_stats,
        "charges_by_month": charges_by_month,
        "charges_ok": charges_ok,
        "metadata": _agg_metadata(st, interactions, {
            "monthly_dicts": monthly_dicts,
            "sub_fees": sub_fees,
            "tracker_total": tracker_total,
            "charges": (charges_total, charges_ok, charges_reason),
            "scorecard_meta": scorecard_meta,
            "outcomes_reason": opts.get("outcomes_reason"),
        }),
    }


def aggregate(interactions, sessions, *args, **opts):
    """Agrega rows → reporte completo. Pura: los datos de skills/comandos
    se inyectan (ver collect_skills_and_commands()).
    Opts (posicionales o por keyword): skills_total, skills_by_project,
    commands, excluded, interaction_kinds, outcomes, outcomes_reason,
    context_metrics."""
    for name, value in zip(("skills_total", "skills_by_project", "commands",
                            "excluded", "interaction_kinds", "outcomes",
                            "outcomes_reason", "context_metrics"), args):
        opts.setdefault(name, value)
    st = _AggState()
    for r in interactions:
        _agg_row(st, r)
    bundle = _agg_bundle(st, interactions, sessions, opts)
    report = _agg_report_sections(st, bundle)
    report.update(_agg_report_charges(bundle))
    report.update(_agg_project_sections(st))
    return _clean_json(report)

def _regression_new_counts(new_rows):
    new_claude = Counter()
    for r in new_rows:
        if r.get("tool") == "claude-cli":
            new_claude[r["timestamp"][:7]] += 1
    return new_claude


def _regression_old_count(bucket):
    try:
        return bucket["tools"]["claude-cli"]["interactions"] or 0
    except (KeyError, TypeError):
        return None  # sin datos claude-cli comparables en ese mes


def _regression_month(bucket, new_claude, mes, threshold_pct):
    old_n = _regression_old_count(bucket)
    if not old_n:
        return None
    new_n = new_claude.get(mes, 0)
    if new_n >= old_n:
        return None
    drop_pct = (old_n - new_n) / old_n * 100.0
    if drop_pct <= threshold_pct:
        return None
    return {
        "mes": mes, "old": old_n, "new": new_n,
        "drop_pct": round(drop_pct, 1),
    }


def detect_monthly_regressions(new_rows, old_report, threshold_pct=DEFAULT_MONTHLY_DROP_PCT):
    """coffe-0c8: compara interacciones claude-cli por mes entre la extracción
    nueva y el reporte vigente. Devuelve una lista de dicts
    {mes, old, new, drop_pct} para los meses cuya caída supere threshold_pct.

    Scope: SOLO claude-cli — es la fuente frágil (dashboard-cache.json puede
    ser truncado por Claude Code, coffe-jg3); las demás tools tienen logs
    on-disk estables. El mes en curso se ignora (crece mes a mes, una "caída"
    a mitad de mes es ruido de reloj, no poda). Meses con old==0 o ausentes
    del reporte viejo no tienen contra qué comparar: nunca regresan.

    Shape del reporte viejo: monthly[mes]["tools"]["claude-cli"]["interactions"]
    (lo que produce aggregate(); reportes previos a esa estructura se tratan
    como sin-datos y no disparan el guard).
    """
    if not old_report:
        return []
    old_monthly = old_report.get("monthly") or {}
    if not isinstance(old_monthly, dict):
        return []

    new_claude = _regression_new_counts(new_rows)
    mes_actual = datetime.now().strftime("%Y-%m")
    regressions = []
    for mes, bucket in sorted(old_monthly.items()):
        if mes == mes_actual:
            continue
        regression = _regression_month(bucket, new_claude, mes, threshold_pct)
        if regression:
            regressions.append(regression)
    return regressions


def check_monthly_regressions(new_rows, out_path, force=False):
    """coffe-0c8: wiring del guard en main(). Lee el reporte vigente en
    out_path (si existe), corre detect_monthly_regressions y aborta con
    SystemExit(1) si hay caídas — fail-loud ANTES de pisar el dataset.
    --force lo salta explícitamente. Umbral vía TRACKER_MONTHLY_DROP_PCT.
    """
    try:
        threshold = float(os.environ.get(
            "TRACKER_MONTHLY_DROP_PCT", DEFAULT_MONTHLY_DROP_PCT))
    except ValueError:
        print(f"ADVERTENCIA: TRACKER_MONTHLY_DROP_PCT no numérico, "
              f"usando {DEFAULT_MONTHLY_DROP_PCT}%", file=sys.stderr)
        threshold = DEFAULT_MONTHLY_DROP_PCT

    old_report = None
    out_path = Path(out_path)
    if out_path.exists():
        try:
            old_report = json.loads(out_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"ADVERTENCIA: no se pudo leer el reporte vigente "
                  f"({out_path}): {e} — guard de regresión mensual desactivado",
                  file=sys.stderr)
            return

    regressions = detect_monthly_regressions(new_rows, old_report, threshold)
    if not regressions:
        return
    if force:
        print("\n--force: regresión mensual claude-cli detectada pero se "
              "escribe de todas formas:", flush=True)
        for reg in regressions:
            print(f"  {reg['mes']}: {reg['old']:,} → {reg['new']:,} "
                  f"(-{reg['drop_pct']}%)", flush=True)
        return

    print(
        "\nABORTADO (coffe-0c8): las interacciones claude-cli de uno o más "
        "meses cayeron vs el reporte vigente:",
        flush=True)
    for reg in regressions:
        print(f"  {reg['mes']}: {reg['old']:,} → {reg['new']:,} "
              f"(-{reg['drop_pct']}%)", flush=True)
    print(
        "\nCausa probable: Claude Code perdió el detalle histórico de "
        "claude-cli en la fuente:"
        "\ntruncó los turns viejos de ~/.claude/dashboard-cache.json "
        "(coffe-jg3) y los\njsonl ya no están en disco. Verificado "
        "2026-11-11: el cache vivo, el backup manual\nde ~/.claude/backups/ "
        "(POST-recorte, pese a su nombre) y todos los snapshots de\n"
        "~/.claude/cache-snapshots/ son byte-idénticos — no hay copia "
        "pre-recorte\nque restaurar."
        "\n\nSi la caída es legítima y la pérdida de fuente está "
        "confirmada, usa --force\npara escribir de todas formas y documenta "
        "la caída en el README (Nota de vigencia).",
        flush=True)
    sys.exit(1)


def _main_args():
    ap = argparse.ArgumentParser(description="Extractor de uso de IA (filtro de proyectos configurable)")
    ap.add_argument("--output", default=None,
                    help="Ruta del JSON de salida (default: data/usage_report_v3.json)")
    ap.add_argument("--force", action="store_true",
                    help="Escribir aunque los datos extraídos sean casi vacíos")
    ap.add_argument("--filter", choices=("charly", "all"), default="charly",
                    help=("Filtro de proyectos: charly = scope in-scope "
                          "(repos charly/sk/ak, default); all = sin filtro"))
    ap.add_argument("--since", default=None, metavar="YYYY-MM-DD",
                    help="Fecha inicial (inclusive) — descarta eventos anteriores (UTC)")
    ap.add_argument("--until", default=None, metavar="YYYY-MM-DD",
                    help="Fecha final (inclusive) — descarta eventos posteriores (UTC)")
    ap.add_argument("--config", default=None,
                    help="Ruta del config (default: config/fpa.json; si no hay, fallback a constantes)")
    return ap, ap.parse_args()


def _main_config(ap, args):
    global CHARLY_FILTER, SINCE, UNTIL
    CHARLY_FILTER = args.filter == "charly"
    try:
        SINCE, UNTIL = parse_window(args.since, args.until)
    except ValueError as e:
        ap.error(str(e))
    try:
        load_config(args.config)
    except FileNotFoundError as e:
        print(f"ERROR: config no encontrado: {e.filename}\n"
              "  (default: config/fpa.json; sin config hay fallback a constantes)",
              file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def _main_out_path(args):
    out_path = Path(args.output) if args.output else OUTPUT_DIR / "usage_report_v3.json"
    if (SINCE or UNTIL) and not args.output and not args.force:
        # coffe-snj: una corrida con ventana es un SUBCONJUNTO — sin --output
        # pisaría el dataset principal de data/ (el guard de arriba ya no
        # protege con ventana). Forzar explícitamente si es intencional.
        print(
            "\nABORTADO: --since/--until produce un subconjunto del histórico;"
            "\n escribirlo en data/usage_report_v3.json pisaría el dataset principal."
            "\nUsa --output RUTA para volcarlo a otro archivo, o --force si es intencional.",
            flush=True,
        )
        sys.exit(1)
    return out_path


def _main_extract(excluded):
    interactions = []
    skipped = {}
    kinds = {}       # FPA-140: eventos por kind/mes (incluye user prompts)

    claude_rows, claude_excluded = extract_claude(skipped, kinds, excluded)
    pi_rows, pi_excluded = extract_pi(skipped, kinds, excluded)
    omp_rows, omp_excluded = extract_omp(skipped, kinds, excluded)
    all_sources = [
        ("Claude", claude_rows),
        ("Pi", pi_rows),
        ("Amp", extract_amp()),
        ("Codex", extract_codex(skipped)),  # coffe-2sy: CLI standalone
        ("omp", omp_rows),
    ]
    excluded.extend(claude_excluded)
    excluded.extend(pi_excluded)
    excluded.extend(omp_excluded)

    for name, rows in all_sources:
        print(f"  {name}: {len(rows)} rows", flush=True)
        interactions.extend(rows)
    return interactions, skipped, kinds


def _main_unique(interactions):
    # Dedup across sources
    seen = set()
    unique = []
    for r in sorted(interactions, key=lambda x: x["timestamp"]):
        key = (r["timestamp"], r["tool"], r["model_raw"], r.get("source", ""), r.get("project", ""))
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


def _main_guard(args, unique, out_path):
    # coffe-snj: con ventana explícita, una extracción angosta es legítima —
    # el guard solo protege contra corridas sin datos (máquina sin logs).
    min_required = 1 if (SINCE or UNTIL) else MIN_INTERACTIONS
    if len(unique) < min_required and not args.force:
        print(
            f"\nABORTADO: solo {len(unique)} interacciones encontradas"
            f" (mínimo esperado: {min_required}).",
            "\nLos extractores leen ~/.claude, ~/.pi/agent y ~/.amp — ¿estás en la máquina con los logs?"
            "\nUsa --force para escribir de todas formas.",
            flush=True,
        )
        sys.exit(1)

    # coffe-0c8: guard fail-loud contra poda del cache frágil de Claude
    # (coffe-jg3). Compara claude-cli por mes contra el reporte vigente ANTES
    # de escribir; aborta si un mes cae más del umbral (TRACKER_MONTHLY_DROP_PCT).
    check_monthly_regressions(unique, out_path, force=args.force)


def _main_context_metrics():
    print("Context metrics...", flush=True)  # ctx-tracker (coffe-kup)
    t_ctx = time.time()
    context_metrics = extract_context_metrics()
    if context_metrics["available"]:
        print(f"  {len(context_metrics['sessions'])} sesiones pi con turnos "
              f"({context_metrics['meta'].get('excluded_pathological', 0)} "
              f"patológicas excluidas, "
              f"{context_metrics['meta'].get('stubs', 0)} stubs) "
              f"[{time.time() - t_ctx:.1f}s]", flush=True)
    else:
        print(f"  {context_metrics['reason']}", flush=True)
    return context_metrics


def _main_report(unique, sessions, ctx):
    print("Aggregating...", flush=True)
    outcomes, outcomes_reason = collect_outcomes()
    sk_total, sk_by_proj, sk_cmds = collect_skills_and_commands()
    report = aggregate(unique, sessions, skills_total=sk_total,
                       skills_by_project=sk_by_proj, commands=sk_cmds,
                       excluded=ctx["excluded"], interaction_kinds=ctx["kinds"],
                       outcomes=outcomes, outcomes_reason=outcomes_reason,
                       context_metrics=ctx["context_metrics"])
    if ctx["skipped"]:
        report["metadata"]["skipped"] = dict(ctx["skipped"])
        report["metadata"]["skipped_lines"] = sum(ctx["skipped"].values())
    if SINCE or UNTIL:
        # coffe-snj: registrar la ventana pedida (reproducibilidad)
        report["metadata"]["window"] = {
            "since": SINCE.isoformat() if SINCE else None,
            "until": UNTIL.isoformat() if UNTIL else None,
        }
    return report


def _main_write(report, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)


def _print_summary(report):
    m = report["metadata"]
    print(f"\n=== REPORT v4.3 ===")
    print(f"Period: {m['date_range']['start']} → {m['date_range']['end']} ({m['filter']})")
    print(f"Interactions: {m['total_interactions']:,}")
    print(f"Cost effective: ${m['cost_total_effective']:,.2f}")
    print(f"Cost real: ${m['cost_total_real']:,.2f}")
    print(f"  Subscription fees: ${m['subscription_fees']:,.2f}")
    print(f"  Pay-per-token: ${m['cost_total_real'] - m['subscription_fees']:,.2f}")
    print(f"Cash real (ledger, reported): ${m.get('charges_total_real', 0):,.2f}"
          f" ({m.get('charges_source', 'n/a')})")
    print(f"Hours: {m['total_hours']}, Days: {m['total_days']}, Projects: {m['total_projects']}")


def _print_monthly(report):
    print(f"\n--- Monthly ---")
    for m_name, mo in report["monthly"].items():
        tools_s = ", ".join(f"{t}:{c}" for t, c in list(mo["tools"].items())[:3])
        models_s = ", ".join(f"{mv}:{c}" for mv, c in list(mo["models"].items())[:3])
        sub = mo.get("subscription_fees", 0)
        print(f"  {m_name}: {mo['interactions']:>6} reqs  "
              f"${mo['cost_effective']:>7.2f} eff  "
              f"${mo['cost_real']:>6.2f} real"
              f"{f' (sub ${sub:.0f})' if sub else ''}  "
              f"{mo['input_tokens']//1000:>5}K in  {mo['output_tokens']//1000:>5}K out")
        print(f"       Tools: {tools_s}")
        print(f"       Models: {models_s}")


def _print_sessions(report):
    print(f"\n--- Sessions ---")
    ss = report["sessions"]
    print(f"  Total: {ss['total_sessions']}")
    print(f"  Length distribution: {dict(ss['length_distribution'])}")
    if ss['total_sessions'] > 0:
        print(f"  With Agent (autonomous): {ss['with_agent']}/{ss['total_sessions']} "
              f"({100 * ss['with_agent'] / ss['total_sessions']:.0f}%)")
    print(f"  Avg turns: {ss['avg_turns']:.0f}, Avg tools: {ss['avg_tools']:.1f}")
    print(f"  API errors: {ss['total_api_errors']}, Compactions: {ss['total_compactions']}")
    print(f"  Longest sessions:")
    for s in ss['top_longest_by_turns'][:5]:
        print(f"    {s['turns']:>4} turns | {s['msgs']:>3} msgs | {s['date']} | {s['project'][:45]}")


def _print_multitasking(report):
    print(f"\n--- Multitasking ---")
    mt = report["multitasking"]
    mh, md, mc = mt["hourly"], mt["daily"], mt["context_switches"]
    print(f"  Horas con ≥2 proyectos: {mh['hours_with_multiple_projects']}/{mh['total_active_hours']} "
          f"({mh['pct_hours_multitasking']}%)")
    print(f"  Avg proyectos/hora activa: {mh['avg_projects_per_active_hour']}")
    print(f"  Distribución por hora: {mh['distribution']}")
    mx = mh["max_projects_in_one_hour"]
    print(f"  Máx simultáneo: {mx['count']} proyectos en {mx['hours']}")
    print(f"  Días con ≥2 proyectos: {md['days_with_multiple_projects']}/{md['total_active_days']} "
          f"({md['pct_days_multitasking']}%)")
    print(f"  Context switches: {mc['total']} total, {mc['avg_per_active_day']}/día activo")
    print(f"  Top días multitasking:")
    for t in md["top_10_days"][:5]:
        print(f"    {t['date']}: {t['projects']} proyectos, {t['interactions']} reqs")


def _print_skills_commands(report):
    print(f"\n--- Skills ---")
    for skill, count in list(report['skills'].items())[:10]:
        print(f"  {skill}: {count}")

    print(f"\n--- Commands ---")
    for cmd, count in list(report['commands'].items())[:10]:
        print(f"  {cmd}: {count}")


def main():
    ap, args = _main_args()
    _main_config(ap, args)
    out_path = _main_out_path(args)

    ventana = ""
    if SINCE or UNTIL:
        ventana = f", ventana: {SINCE or 'inicio'}..{UNTIL or 'hoy'}"
    print(f"=== IA Usage Tracker v4.3 (filtro: {args.filter}{ventana}) ===", flush=True)

    excluded = []    # FPA-141: filas fuera del filtro charly
    interactions, skipped, kinds = _main_extract(excluded)
    unique = _main_unique(interactions)
    print(f"  Total: {len(interactions)} → {len(unique)} unique", flush=True)

    _main_guard(args, unique, out_path)

    print("Session stats...", flush=True)
    sessions = extract_sessions()  # coffe-i31: claude + pi + amp con provenance
    print(f"  {len(sessions)} charly sessions", flush=True)

    context_metrics = _main_context_metrics()
    report = _main_report(unique, sessions, {
        "excluded": excluded,
        "skipped": skipped,
        "kinds": kinds,
        "context_metrics": context_metrics,
    })

    _main_write(report, out_path)

    # Pretty print
    _print_summary(report)
    _print_monthly(report)
    _print_sessions(report)
    _print_multitasking(report)
    _print_skills_commands(report)

    print(f"\nDone. {out_path}")


if __name__ == "__main__":
    main()