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
"""

import argparse
import importlib.util
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
PI_DIR = Path.home() / ".pi" / "agent"  # Gemini llega vía logs de Pi (google-gemini-cli), no hay extractor propio
AMP_DIR = Path.home() / ".amp"
CODEX_DIR = Path.home() / ".codex"  # coffe-2sy: rollouts del CLI standalone (sesiones/*/*/*/rollout-*.jsonl)
OUTPUT_DIR = Path("data")
LOCAL_TZ = datetime.now().astimezone().tzinfo  # OJO: los buckets hourly/daily usan la TZ local de la máquina que extrae

CHARLY_FILTER = True  # default del flag --filter (charly); main() lo setea desde args
SINCE = None  # date límite inferior (inclusive) de --since; None = sin límite
UNTIL = None  # date límite superior (inclusive) de --until; None = sin límite

# Umbral anti-clobber: si los extractores encuentran menos interacciones que esto
# (p.ej. máquina sin ~/.claude / ~/.pi/agent / ~/.amp), NO se escribe el reporte
# sin --force, para no destruir el dataset versionado en data/.
MIN_INTERACTIONS = 1000

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
            kind = e.get("kind")
            if kind not in CHARGES_KINDS_IA:
                raise ValueError(
                    f"ledger: {provider}[{i}] kind fuera del vocabulario IA: {kind!r}")
            amount = e.get("amount")
            if not isinstance(amount, (int, float)) or isinstance(amount, bool):
                raise ValueError(f"ledger: {provider}[{i}] amount no numérico")
            if not _ledger_date_ok(e.get("date")):
                raise ValueError(f"ledger: {provider}[{i}] date inválida: {e.get('date')!r}")
            mes = e["date"][:7]
            by_month.setdefault(provider, {}).setdefault(mes, 0.0)
            # redondeo a centavos: transcripción manual, dinero real
            by_month[provider][mes] = round(by_month[provider][mes] + amount, 2)
            if kind in CHARGES_KINDS_P2P:
                p2p.setdefault(provider, {}).setdefault(mes, 0.0)
                p2p[provider][mes] = round(p2p[provider][mes] + amount, 2)
            total = round(total + amount, 2)
    return by_month, total, p2p


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
    tools = set(SUBSCRIPTIONS) | set(charges_by_month)
    # tools con interacciones del tracker también entran (su efectivo importa)
    for mo in monthly_dicts.values():
        tools.update((mo.get("tools") or {}))
    rec = defaultdict(dict)
    fees = calc_subscription_fees(monthly_dicts)  # {mes: total} por calendario
    # meses = los del tracker ∪ los del ledger: un mes con facturas pero sin
    # interacciones (p.ej. recargas openrouter de jul-2026) también reconcilia
    meses = sorted(set(monthly_dicts) | {
        m for meses_prov in charges_by_month.values() for m in meses_prov})
    for mes in meses:
        mo = monthly_dicts.get(mes, {})
        tools_del_mes = set(mo.get("tools") or {})
        # fee total del mes por calendario, incluso sin interacciones
        fee_cal = round(sum(
            _implicit_fee_tool_mes(t, mes) for t in SUBSCRIPTIONS), 8)
        for tool in sorted(tools | tools_del_mes):
            real = round(float(charges_by_month.get(tool, {}).get(mes, 0.0)), 8)
            implicito = _implicit_fee_tool_mes(tool, mes)
            rec[mes][tool] = {
                "charges_real": real,
                "charges_provenance": "reported",
                "subscription_fee_implicit": implicito,
                "cost_effective": round(float(
                    (mo.get("tools") or {}).get(tool, {}).get("cost_effective", 0.0)), 8),
                "fee_month_total": fee_cal,
            }
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
    global SUBSCRIPTIONS, MODEL_PRICING, DEFAULT_RATES, _CACHE_WRITE_FACTOR
    global _FPA_CONFIG, _PRICING_VERSIONS, CONFIG_SOURCE, _ENERGY_COEFFS

    explicit = path or os.environ.get("TRACKER_CONFIG")
    cfg = None
    CONFIG_SOURCE = None
    if explicit:
        cfg = fpa_config.load_fpa_config(explicit)  # FileNotFoundError si no existe
        CONFIG_SOURCE = str(explicit)
    else:
        for cand in (Path("config") / "fpa.json", _REPO_ROOT / "config" / "fpa.json"):
            if cand.exists():
                cfg = fpa_config.load_fpa_config(cand)
                CONFIG_SOURCE = str(cand)
                break

    if cfg is None:
        print(
            "WARNING: config/fpa.json no encontrado — usando SUBSCRIPTIONS/"
            "MODEL_PRICING hardcodeadas (fallback; ignorá este warning solo "
            "en máquinas sin el repo)",
            file=sys.stderr,
        )
        SUBSCRIPTIONS = _FALLBACK_SUBSCRIPTIONS
        MODEL_PRICING = _FALLBACK_MODEL_PRICING
        DEFAULT_RATES = _FALLBACK_DEFAULT_RATES
        _CACHE_WRITE_FACTOR = 1.25
        _PRICING_VERSIONS = [{"effective": None, "rates": MODEL_PRICING}]
        _FPA_CONFIG = None
        _ENERGY_COEFFS = None
        return None

    errors = fpa_config.validate_config(cfg)
    if errors:
        raise ValueError(
            f"config inválido ({CONFIG_SOURCE}):\n" + "\n".join(errors)
        )

    _FPA_CONFIG = cfg
    pricing = cfg["model_pricing"]
    SUBSCRIPTIONS = cfg["subscriptions"]
    DEFAULT_RATES = pricing["default_rates"]
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


def estimate_cost(family, version, input_tokens, output_tokens, cache_read=0,
                  cache_write=0, when=None):
    """Estimate cost from token counts at pay-per-token rates.

    `when`: fecha de la interacción (date) para pricing versionado por
    fecha efectiva (FPA-016); None → última versión conocida.
    """
    rates = _pricing_for(when).get(family, {}).get(version, DEFAULT_RATES)
    # Anthropic cobra cache writes a _CACHE_WRITE_FACTOR x del input rate
    input_cost = (input_tokens + cache_write * _CACHE_WRITE_FACTOR) * rates["input"]
    cache_read_cost = cache_read * rates.get("cache_read", rates["input"] * 0.1)
    output_cost = output_tokens * rates["output"]
    return round(input_cost + cache_read_cost + output_cost, 8)


def _energy_kwh(in_tok, out_tok, cache_read, cache_write, j_per_token,
                factor):
    """coffe-7mj.1: kWh estimado de un modelo con tokens (3 decimales).

    (input + output + cache_write a peso completo — escribir KV es cómputo
    de prefill — + cache_read × factor) × J/token del tier / 3.6e6. Los
    coeficientes J/token son estimaciones de laboratorio (Luccioni et al. /
    AI Energy Score): la energía es siempre provenance "assumed".
    """
    fresh = in_tok + out_tok + cache_write
    return round((fresh + cache_read * factor) * j_per_token / 3.6e6, 3)


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
        return  # fallback sin config: metadata.energy_* registra la razón
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
            kwh = _energy_kwh(tok["in"], tok["out"], tok["cache_read"],
                              tok["cache_write"], tiers[tier], factor)
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


def filter_sessions(sessions, since=None, until=None):
    """Sesiones que se solapan con [since, until] (fechas, inclusive).

    Una sesión que empieza antes de `since` pero termina dentro cuenta
    (su trabajo parcial está en la ventana); sin ventana → lista completa.
    Sesiones sin fechas no se descartan (los extractores ya filtran las
    filas por fecha; una sesión sin fecha no se pierde en silencio)."""
    if not since and not until:
        return list(sessions)
    kept = []
    for s in sessions:
        first = (s.get("first_ts") or "")[:10]
        last = (s.get("last_ts_full") or s.get("first_ts") or "")[:10]
        if since and last and last < since.isoformat():
            continue
        if until and first and first > until.isoformat():
            continue
        kept.append(s)
    return kept

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
    for org in ("charly", "sk", "ak"):
        if (p == org or p.startswith(org + "-") or p.startswith(org + "/")):
            return True
        for sep in ("-gh-", "/gh/"):
            # el org debe ser un segmento completo: seguido de '-', '/' o fin
            # (cubre path mungeado '-gh-charly-coffee' y uri '…/gh/charly/coffe')
            i = p.find(sep + org)
            while i != -1:
                j = i + len(sep) + len(org)
                if j >= len(p) or p[j] in "-/":
                    return True
                i = p.find(sep + org, i + 1)
    return False


# Alias histórico: el nombre viejo mentía (también filtra ak/sk desde v4.5).
is_charly = in_scope

def model_details(model_id):
    m = model_id.lower()
    if "claude-opus-4-7" in m: return ("claude", "opus-4.7")
    if "claude-opus-4-6" in m: return ("claude", "opus-4.6")
    if "claude-opus-4-5" in m: return ("claude", "opus-4.5")
    if "claude-sonnet-4-6" in m: return ("claude", "sonnet-4.6")
    if "claude-haiku" in m: return ("claude", "haiku")
    if "gpt-6.1" in m: return ("codex", "gpt-6.1")  # coffe-2sy: gpt-6.1-sol etc. (precio API desconocido → default_rates, *assumed*)
    if "gpt-6" in m: return ("codex", "gpt-6")  # coffe-2sy: variantes (gpt-6-luna); familia codex, no "other"
    if "gpt-5.5" in m: return ("codex", "gpt-5.5")
    if "gpt-5.4" in m: return ("codex", "gpt-5.4")
    if "gpt-5.3" in m: return ("codex", "gpt-5.3")
    if "gemini-3.1" in m: return ("gemini", "gemini-3.1")
    if "gemini-3-" in m: return ("gemini", "gemini-3-pro")
    if "gemini-2.5" in m: return ("gemini", "gemini-2.5")
    if "deepseek" in m: return ("deepseek", "v4-flash")
    if "kimi" in m: return ("kimi", "k2")
    if "synthetic" in m: return ("claude", "synthetic")
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
    # Step 1: Read JSONL files
    jsonl_rows = []
    projects_dir = CLAUDE_DIR / "projects"
    if not projects_dir.exists():
        return [], []  # máquina sin logs de Claude (el guard de main() se encarga del resto)
    for pd in projects_dir.iterdir():
        if not pd.is_dir(): continue
        proj = pd.name
        if not is_charly(proj):
            # FPA-141: registrar lo excluido, no descartarlo en silencio
            n_files = len(list(pd.glob("*.jsonl")))
            excluded_rows.append({"project": proj, "tool": "claude-cli",
                                  "interactions": n_files, "cost_effective": None,
                                  "timestamp": None})
            continue
        for f in pd.glob("*.jsonl"):
            try:
                with open(f) as fh:
                    for line in fh:
                        entry = json.loads(line)
                        ts = parse_ts(entry.get("timestamp"))
                        if not ts: continue
                        if not in_window(ts): continue  # coffe-snj: ventana --since/--until
                        if entry.get("type") == "user":
                            if kinds is not None:
                                kinds.setdefault(ts.strftime("%Y-%m"), Counter())["user_prompt"] += 1
                            continue
                        if entry.get("type") == "assistant":
                            msg = entry.get("message", {})
                            if isinstance(msg, str): msg = json.loads(msg)
                            if kinds is not None:
                                kinds.setdefault(ts.strftime("%Y-%m"), Counter())[
                                    _kind_of_claude(entry, msg)] += 1
                            usage = msg.get("usage", {}) or {}
                            model = msg.get("model", "unknown")
                            fam, ver = model_details(model)
                            inp = usage.get("input_tokens", 0) or 0
                            out = usage.get("output_tokens", 0) or 0
                            cache_r = usage.get("cache_read_input_tokens", 0) or 0
                            cache_c = usage.get("cache_creation_input_tokens", 0) or 0
                            cost = estimate_cost(fam, ver, inp, out, cache_r, cache_c,
                                                 when=ts.date())
                            jsonl_rows.append({
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
                            })
            except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
                if skipped is not None:
                    k = f"claude:{f.name}"
                    skipped[k] = skipped.get(k, 0) + 1
                elif os.environ.get("TRACKER_DEBUG"):
                    print(f"  [skipped] {f.name}: {e}", file=sys.stderr)

    # Step 2: Read dashboard cache for cost overrides
    cache_cost_lookup = {}
    cache_file = CLAUDE_DIR / "dashboard-cache.json"
    if cache_file.exists():
        cache = json.loads(cache_file.read_text())
        for key, summary in cache.get("entries", {}).items():
            if summary.get("source") != "claude": continue
            proj = summary.get("project", "")
            if not is_charly(proj): continue
            for turn in summary.get("turns", []):
                ts = parse_ts(turn.get("ts"))
                if not ts: continue
                if not in_window(ts): continue  # coffe-snj: ventana
                model = turn.get("model", "unknown")
                cost = turn.get("cost", 0) or 0
                # Build lookup key: (timestamp_iso, model, project)
                lk = (ts.isoformat(), model, proj)
                # Keep the first (earliest) entry if multiple matches
                if lk not in cache_cost_lookup:
                    cache_cost_lookup[lk] = cost

    # Step 3: Merge cache costs into JSONL rows
    merged = []
    for row in jsonl_rows:
        lk = (row["timestamp"], row["model_raw"], row["project"])
        if lk in cache_cost_lookup:
            row["cost_effective"] = cache_cost_lookup[lk]
            row["source"] = "claude_cache_merged"
        merged.append(row)

    # Step 4: Add any cache entries that weren't in JSONL (shouldn't happen, but safety)
    cache_keys = {(r["timestamp"], r["model_raw"], r["project"]) for r in merged}
    for (ts, model, proj), cost in cache_cost_lookup.items():
        if (ts, model, proj) not in cache_keys:
            fam, ver = model_details(model)
            merged.append({
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
            })

    return merged, excluded_rows


def extract_pi(skipped=None, kinds=None, excluded=None, sessions_dir=None):
    """Extrae sesiones Pi. kinds/excluded como en extract_claude
    (FPA-140/141). Devuelve (rows, excluded_rows). sessions_dir permite
    tests con tmpdir (coffe-8t8)."""
    rows = []
    excluded_rows = []
    sessions_dir = sessions_dir or (PI_DIR / "sessions")
    if not sessions_dir.exists(): return rows, excluded_rows
    for sd in sessions_dir.iterdir():
        if not sd.is_dir(): continue
        proj = sd.name
        if not is_charly(proj):
            excluded_rows.append({"project": proj, "tool": "pi",
                                  "interactions": len(list(sd.glob("*.jsonl"))),
                                  "cost_effective": None, "timestamp": None})
            continue
        for f in sd.glob("*.jsonl"):
            try:
                with open(f) as fh:
                    for line in fh:
                        entry = json.loads(line)
                        if entry.get("type") != "message": continue
                        msg = entry.get("message", {}) or {}
                        role = msg.get("role")
                        if role == "user":
                            # FPA-140: user prompts se cuentan, no generan fila de coste
                            ts_u = parse_ts(entry.get("timestamp"))
                            if kinds is not None and ts_u is not None and in_window(ts_u):
                                kinds.setdefault(ts_u.strftime("%Y-%m"), Counter())["user_prompt"] += 1
                            continue
                        if role != "assistant": continue
                        usage = msg.get("usage", {}) or {}
                        # Cost can be a dict {input, output, cacheRead, cacheWrite, total} or a number
                        cost_info = usage.get("cost", {})
                        if isinstance(cost_info, dict):
                            cost = cost_info.get("total", 0) or 0
                        else:
                            cost = cost_info or 0
                        model = msg.get("model", "unknown")
                        provider = entry.get("provider", msg.get("provider", ""))
                        ts = parse_ts(entry.get("timestamp"))
                        if not ts: continue
                        if not in_window(ts): continue  # coffe-snj: ventana
                        if kinds is not None:
                            kind = "tool_call" if _pi_has_tool_call(msg) else "assistant_turn"
                            kinds.setdefault(ts.strftime("%Y-%m"), Counter())[kind] += 1
                        fam, ver = model_details(model)
                        tool_map = {"openai-codex": "codex", "claude-cli": "claude-cli",
                                    "google-gemini-cli": "gemini-cli", "gemini-cli": "gemini-cli",
                                    "openrouter": "openrouter", "github-copilot": "copilot",
                                    "anthropic": "claude-cli"}
                        tool = tool_map.get(provider, provider)
                        inp = usage.get("input_tokens") or usage.get("input", 0) or 0
                        out = usage.get("output_tokens") or usage.get("output", 0) or 0
                        cache_r = usage.get("cacheRead") or usage.get("cache_read") or 0
                        cache_w = usage.get("cacheWrite") or usage.get("cache_write") or 0
                        if not cost and (inp or out or cache_r):
                            # coffe-8t8: los logs de gemini-cli (Pi) no traen
                            # cost → estimación pay-per-token con los rates
                            # vigentes en la fecha (FPA-016). Sin tokens no
                            # hay base de estimación: queda 0.0 real.
                            cost = estimate_cost(fam, ver, inp, out,
                                                 cache_r or 0, cache_w or 0,
                                                 when=ts.date())
                        rows.append({
                            "source": "pi",
                            "tool": tool,
                            "model_raw": model,
                            "model_family": fam,
                            "model_version": ver,
                            "project": proj,
                            "timestamp": ts.isoformat(),
                            "hour": hour_key(ts),
                            "input_tokens": inp,
                            "output_tokens": out,
                            "cache_read_tokens": cache_r or 0,
                            "cache_write_tokens": cache_w or 0,
                            "cost_effective": cost,
                            "kind": "tool_call" if _pi_has_tool_call(msg) else "assistant_turn",
                        })
            except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
                if skipped is not None:
                    k = f"pi:{f.name}"
                    skipped[k] = skipped.get(k, 0) + 1
                elif os.environ.get("TRACKER_DEBUG"):
                    print(f"  [skipped] {f.name}: {e}", file=sys.stderr)
    return rows, excluded_rows


def _pi_has_tool_call(msg):
    """FPA-140: detección best-effort de tool calls en mensajes Pi
    (formato AI SDK: parts/content con type tool-*/toolUse)."""
    for container in (msg.get("parts"), msg.get("content"), msg.get("toolCalls")):
        if isinstance(container, list):
            for part in container:
                if isinstance(part, dict):
                    ptype = str(part.get("type", ""))
                    if ptype.startswith("tool") or "toolCall" in ptype:
                        return True
                elif isinstance(part, str) and part in ("tool-call", "tool_use"):
                    return True
        elif isinstance(container, list) and container:
            return False
    return False


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


def extract_amp():
    """Extrae de Amp (@ampcode/cli, agente autónomo). Sin costo.

    coffe-n35: el filtro es is_charly(uri) (respeta --filter) y el label
    es el proyecto derivado del uri, no "charly/amp-auto" fijo.
    """
    rows = []
    amp_dir = AMP_DIR / "file-changes"
    if not amp_dir.exists(): return rows
    for td in amp_dir.iterdir():
        if not td.is_dir(): continue
        hits = []  # (ts, proyecto derivado) por archivo tocado
        for f in td.iterdir():
            try:
                entry = json.loads(f.read_text())
                uri = entry.get("uri", "")
                if not is_charly(uri):  # coffe-n35: unifica con --filter
                    continue
                ts = parse_ts(entry.get("timestamp"))
                if ts and in_window(ts):  # coffe-snj: ventana
                    proj = amp_proj_from_uri(uri) or "amp-unknown"
                    hits.append((ts, proj))
            except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
                if os.environ.get("TRACKER_DEBUG"):
                    print(f"  [skipped] amp: {e}", file=sys.stderr)
        if hits:
            for ts, proj in hits:
                rows.append({
                    "source": "amp", "tool": "amp",
                    "model_raw": "amp", "model_family": "amp", "model_version": "v1",
                    "project": proj,
                    "timestamp": ts.isoformat(), "hour": hour_key(ts),
                    "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0,
                    "cost_effective": 0,
                })
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
    if not sessions_dir.exists(): return rows
    for day_dir in sorted(sessions_dir.glob("*/*/*")):
        if not day_dir.is_dir(): continue
        for f in sorted(day_dir.glob("rollout-*.jsonl")):
            try:
                _parse_codex_rollout(f, rows, skipped)
            except OSError as e:
                if skipped is not None:
                    skipped[f"codex:{f.name}"] = skipped.get(f"codex:{f.name}", 0) + 1
                elif os.environ.get("TRACKER_DEBUG"):
                    print(f"  [skipped codex] {f.name}: {e}", file=sys.stderr)
    return rows


def _parse_codex_rollout(f, rows, skipped=None):
    """1 rollout → filas por turno (token_count) en `rows`.

    Malformed lines se descartan (best-effort); una línea rota no tira la
    sesión entera. Sin meta (session_meta) o sin token_count → 0 filas.
    """
    cwd = model = None
    turns = []  # (ts, last_token_usage) por turno
    with open(f) as fh:
        for line in fh:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            typ = entry.get("type")
            p = entry.get("payload") or {}
            if typ == "session_meta":
                cwd = p.get("cwd") or cwd
            elif p.get("type") == "thread_settings_applied":
                model = ((p.get("thread_settings") or {}).get("model")) or model
            elif p.get("model"):
                # formato viejo (abr-2026): turn_context trae el modelo plano
                model = p["model"]
            elif p.get("type") == "token_count":
                ts = parse_ts(entry.get("timestamp"))
                last = (p.get("info") or {}).get("last_token_usage") or {}
                if ts and last:
                    turns.append((ts, last))
    if cwd is None or not turns:
        return
    if not is_charly(cwd):  # coffe-2sy: unifica con --filter
        return
    proj = amp_proj_from_uri(cwd) or "codex-unknown"
    fam, ver = model_details(model or "unknown")
    for ts, u in turns:
        if not in_window(ts): continue  # coffe-snj: ventana
        inp = u.get("input_tokens") or 0
        cached = min(u.get("cached_input_tokens") or 0, inp)  # sanity
        fresh = inp - cached
        out = u.get("output_tokens") or 0
        cost = estimate_cost(fam, ver, fresh, out, cached, 0, when=ts.date())
        rows.append({
            "source": "codex", "tool": "codex",
            "model_raw": model or "unknown",
            "model_family": fam, "model_version": ver,
            "project": proj,
            "timestamp": ts.isoformat(), "hour": hour_key(ts),
            "input_tokens": fresh, "output_tokens": out,
            "cache_read_tokens": cached,
            "cache_write_tokens": u.get("cache_write_input_tokens") or 0,
            "cost_effective": cost,
        })


def extract_codex_sessions(sessions_dir=None):
    """coffe-2sy: sesiones codex — 1 rollout = 1 sesión.

    duration_msgs = prompts de usuario reales (se excluyen los preámbulos
    inyectados por el harness: AGENTS.md, environment/turn context, skills
    — best-effort sobre el texto). n_turns = token_count events
    (turnos con uso). has_agent=False: los subagentes son semántica de
    Claude; el alcance se declara en sessions.agent_semantics. La ventana
    --since/--until se aplica en extract_sessions() (filter_sessions).
    """
    sessions = []
    sessions_dir = sessions_dir or (CODEX_DIR / "sessions")
    if not sessions_dir.exists(): return sessions
    _PREAMBLE = ("<environment_context", "<turn_context", "<user_instructions",
                 "<skill>", "# AGENTS.md")
    for day_dir in sorted(sessions_dir.glob("*/*/*")):
        if not day_dir.is_dir(): continue
        for f in sorted(day_dir.glob("rollout-*.jsonl")):
            try:
                cwd = None
                prompts = turns = 0
                first_ts = last_ts = None
                with open(f) as fh:
                    for line in fh:
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        p = entry.get("payload") or {}
                        pt = p.get("type")
                        if entry.get("type") == "session_meta":
                            cwd = p.get("cwd") or cwd
                        elif pt == "token_count":
                            ts = parse_ts(entry.get("timestamp"))
                            if ts:
                                turns += 1
                                if first_ts is None: first_ts = ts
                                last_ts = ts
                        elif (pt == "message" and p.get("role") == "user"):
                            ts = parse_ts(entry.get("timestamp"))
                            if ts:
                                if first_ts is None: first_ts = ts
                                last_ts = ts
                            text = "".join(
                                c.get("text", "") for c in (p.get("content") or [])
                                if isinstance(c, dict))
                            if not text.lstrip().startswith(_PREAMBLE):
                                prompts += 1
                if cwd is None or not turns: continue
                if not is_charly(cwd): continue
                sessions.append({
                    "tool": "codex", "source": "codex_session_files",
                    "project": amp_proj_from_uri(cwd) or "codex-unknown",
                    "first_ts": (first_ts or last_ts).isoformat()[:10],
                    "first_ts_full": (first_ts or last_ts).isoformat(),
                    "last_ts_full": last_ts.isoformat() if last_ts else None,
                    "duration_msgs": prompts,
                    "n_turns": turns,
                    "n_tools": 0,
                    "has_agent": False,
                    "n_skills": None,
                    "n_errors": None,
                    "n_compactions": None,
                })
            except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
                if os.environ.get("TRACKER_DEBUG"):
                    print(f"  [skipped codex-session] {f.name}: {e}", file=sys.stderr)
    return sessions


def extract_session_stats():
    """Extrae estadísticas de sesión desde el cache de Claude."""
    sessions = []
    cache_file = CLAUDE_DIR / "dashboard-cache.json"
    if not cache_file.exists(): return sessions
    cache = json.loads(cache_file.read_text())
    for key, summary in cache.get("entries", {}).items():
        if summary.get("source") != "claude": continue
        proj = summary.get("project", "")
        if not is_charly(proj): continue
        n_turns = len(summary.get("turns", []))
        tools = summary.get("tool_counts", {})
        skills = summary.get("skill_uses", {})
        n_skills = sum(skills.values())
        n_errors = len(summary.get("api_errors", []))
        n_compactions = summary.get("compactions", 0)
        has_agent = "Agent" in tools
        fs = summary.get("first_ts")
        ls = summary.get("last_ts")
        msgs = summary.get("user_messages", 0)
        sessions.append({
            "tool": "claude-cli",  # coffe-i31: provenance por tool
            "source": "claude_cache",
            "project": proj,
            "first_ts": (fs or " ")[:10],
            "first_ts_full": fs,
            "last_ts_full": ls,
            "duration_msgs": msgs,
            "n_turns": n_turns,
            "n_tools": len(tools),
            "has_agent": has_agent,
            "n_skills": n_skills,
            "n_errors": n_errors,
            "n_compactions": n_compactions,
        })
    # coffe-snj: sesiones que se solapan con la ventana --since/--until
    return filter_sessions(sessions, SINCE, UNTIL)


def extract_pi_sessions(sessions_dir=None):
    """coffe-i31: sesiones pi — 1 archivo JSONL = 1 sesión.

    En pi el reset es /new (no /clear) y el TUI lo intercepta: nunca
    aparece como user message en el JSONL (verificado en la auditoría:
    0 /new en 981 archivos). La señal correcta de sesión/reset es el
    archivo mismo: cada /new abre JSONL nuevo.

    Métricas sin señal en pi (skills/errores/compactions) van en None —
    _session_stats las excluye de los totales en vez de contarlas como 0.
    has_agent=False: la semántica Agent (subagentes) es de Claude; el
    alcance se declara en sessions.agent_semantics.

    sessions_dir permite tests con tmpdir (como extract_pi, coffe-8t8).
    La ventana --since/--until se aplica en extract_sessions() (una sesión
    puede solaparse con la ventana — filter_sessions).
    """
    sessions = []
    sessions_dir = sessions_dir or (PI_DIR / "sessions")
    if not sessions_dir.exists(): return sessions
    for sd in sorted(sessions_dir.iterdir()):
        if not sd.is_dir(): continue
        proj = sd.name
        if not is_charly(proj): continue  # coffe-i31: scope consistente
        for f in sorted(sd.glob("*.jsonl")):
            try:
                msgs = user_prompts = 0
                first_ts = last_ts = None
                first_raw = last_raw = None
                tools = set()
                with open(f) as fh:
                    for line in fh:
                        entry = json.loads(line)
                        if entry.get("type") != "message": continue
                        ts = parse_ts(entry.get("timestamp"))
                        if ts is not None:
                            if first_ts is None:
                                first_ts = ts
                                first_raw = entry.get("timestamp")
                            last_ts = ts
                            last_raw = entry.get("timestamp")
                        msg = entry.get("message", {}) or {}
                        role = msg.get("role")
                        if role == "user":
                            user_prompts += 1
                        elif role == "assistant":
                            msgs += 1
                            if _pi_has_tool_call(msg):
                                for part in (msg.get("parts") or msg.get("content") or []):
                                    if isinstance(part, dict) and str(part.get("type", "")).startswith("tool"):
                                        name = part.get("toolName") or part.get("name")
                                        tools.add(str(name) if name else "tool-call")
                if first_ts is None: continue  # archivo sin mensajes
                sessions.append({
                    "tool": "pi",
                    "source": "pi_session_files",
                    "project": proj,
                    "first_ts": first_ts.isoformat()[:10],
                    "first_ts_full": first_raw,
                    "last_ts_full": last_raw,
                    "duration_msgs": user_prompts,
                    "n_turns": msgs,
                    "n_tools": len(tools),
                    "has_agent": False,
                    "n_skills": None,
                    "n_errors": None,
                    "n_compactions": None,
                })
            except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError) as e:
                if os.environ.get("TRACKER_DEBUG"):
                    print(f"  [skipped pi-session] {f.name}: {e}", file=sys.stderr)
    return sessions


def extract_amp_sessions(amp_dir=None):
    """coffe-i31: sesiones amp — 1 dir de file-changes = 1 tarea.

    Amp es un agente autónomo: has_agent=True por definición (se declara
    en sessions.agent_semantics). n_turns = archivos tocados; sin señal
    de skills/errores/compactions (None).
    """
    sessions = []
    amp_dir = amp_dir or (AMP_DIR / "file-changes")
    if not amp_dir.exists(): return sessions
    for td in sorted(amp_dir.iterdir()):
        if not td.is_dir(): continue
        hits = []  # (ts, proyecto derivado, timestamp crudo) por archivo
        for f in td.iterdir():
            try:
                entry = json.loads(f.read_text())
                uri = entry.get("uri", "")
                if not is_charly(uri):
                    continue
                ts = parse_ts(entry.get("timestamp"))
                if ts:
                    raw = entry.get("timestamp")
                    # coffe-372: logs amp reales usan epoch ms INT — el crudo
                    # se normaliza a ISO (filter_sessions hace [:10]; un int
                    # crashea con ventana --since/--until). Strings intactos.
                    raw = raw if isinstance(raw, str) else ts.isoformat()
                    hits.append((ts, amp_proj_from_uri(uri) or "amp-unknown", raw))
            except (json.JSONDecodeError, OSError, ValueError, TypeError, KeyError):
                continue
        if not hits: continue
        hits.sort()
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
            + filter_sessions(extract_codex_sessions(), SINCE, UNTIL))  # coffe-2sy


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


def _peak_simultaneous_sessions(sessions):
    """FPA-120(b): máximo solapamiento de sesiones con sweep line.
    None si falta algún timestamp (no se puede computar con honestidad)."""
    intervals = []
    for s in sessions:
        f = s.get("first_ts_full") or s.get("first_ts")
        l = s.get("last_ts_full") or s.get("last_ts")
        if not f or not l:
            return None
        fi, li = parse_ts(f), parse_ts(l)
        if not fi or not li or len(str(f)) < 16:  # solo fecha = sin hora, no computable
            return None
        intervals.append((fi, li))
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


def calc_subscription_fees(monthly_data):
    """
    Calculate monthly subscription fees for each month.
    Returns dict: {month_key: total_sub_fee}
    """
    sub_fees = {}
    for m_key, m_data in monthly_data.items():
        # Find which months had which subscriptions active
        total = 0.0
        tools = m_data.get("tools", {})
        for tool, periods in SUBSCRIPTIONS.items():
            if tool not in tools:
                continue
            for period in periods:
                if period["monthly_fee"] > 0:
                    p_start = period["start"]
                    p_end = period["end"] or "9999-12"
                    # Does this month overlap with the subscription period?
                    if m_key >= p_start[:7] and m_key < p_end[:7]:
                        total += period["monthly_fee"]
        if total > 0:
            sub_fees[m_key] = total
    return sub_fees


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


def _multitasking_block(hour_projects, day_projects, daily, switches_by_day):
    mt_hour_counts = {h: len(ps) for h, ps in hour_projects.items()}
    mt_dist = Counter()
    for n in mt_hour_counts.values():
        if n == 1: mt_dist["1"] += 1
        elif n == 2: mt_dist["2"] += 1
        elif n == 3: mt_dist["3"] += 1
        else: mt_dist["4+"] += 1

    total_active_hours = len(hour_projects)
    mt_hours_n = sum(1 for n in mt_hour_counts.values() if n >= 2)
    top_mt_hours = sorted(mt_hour_counts.items(), key=lambda x: -x[1])[:10]
    max_n = max(mt_hour_counts.values()) if mt_hour_counts else 0
    max_hours = [h for h, n in mt_hour_counts.items() if n == max_n] if mt_hour_counts else []

    active_days = len(day_projects)
    mt_days = {d: len(ps) for d, ps in day_projects.items()}
    mt_days_n = sum(1 for n in mt_days.values() if n >= 2)
    top_mt_days = sorted(mt_days.items(), key=lambda x: -x[1])[:10]
    total_switches = sum(switches_by_day.values())
    top_switch_days = sorted(switches_by_day.items(), key=lambda x: -x[1])[:10]

    return {
        "description": (
            "Proyectos distintos con actividad en la misma ventana. "
            "'context_switches' cuenta cambios de proyecto entre requests "
            "consecutivos (puede inflarse por agentes paralelos en el mismo minuto)."
        ),
        "hourly": {
            "total_active_hours": total_active_hours,
            "hours_with_multiple_projects": mt_hours_n,
            "pct_hours_multitasking": round(100 * mt_hours_n / total_active_hours, 1) if total_active_hours else 0,
            "avg_projects_per_active_hour": round(sum(mt_hour_counts.values()) / total_active_hours, 2) if total_active_hours else 0,
            "distribution": dict(mt_dist.most_common()),
            "max_projects_in_one_hour": {
                "count": max_n,
                "hours": max_hours[:5],
                "projects": sorted(hour_projects[max_hours[0]]) if max_hours else [],
            },
            "top_10_hours": [
                {"hour": h, "projects": n, "list": sorted(hour_projects[h])}
                for h, n in top_mt_hours
            ],
        },
        "daily": {
            "total_active_days": active_days,
            "days_with_multiple_projects": mt_days_n,
            "pct_days_multitasking": round(100 * mt_days_n / active_days, 1) if active_days else 0,
            "avg_projects_per_active_day": round(sum(mt_days.values()) / active_days, 2) if active_days else 0,
            "top_10_days": [
                {"date": d, "projects": n, "list": sorted(day_projects[d]),
                 "interactions": daily[d]["interactions"] if d in daily else 0}
                for d, n in top_mt_days
            ],
        },
        "context_switches": {
            "total": total_switches,
            "avg_per_active_day": round(total_switches / active_days, 1) if active_days else 0,
            "max_in_one_day": top_switch_days[0][1] if top_switch_days else 0,
            "top_10_days": dict(top_switch_days),
        },
    }


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
            "pi: sin señal de subagentes (has_agent=False)"),
        "by_tool": defaultdict(lambda: {
            "total": 0, "with_agent": 0,
            "resets": {"signal": "", "count": 0}}),
    }
    longest = []
    for s in sessions:
        n = s["n_turns"]
        if n <= 10: stats["length_distribution"]["1-10"] += 1
        elif n <= 50: stats["length_distribution"]["11-50"] += 1
        elif n <= 100: stats["length_distribution"]["51-100"] += 1
        elif n <= 300: stats["length_distribution"]["101-300"] += 1
        elif n <= 500: stats["length_distribution"]["301-500"] += 1
        else: stats["length_distribution"]["500+"] += 1
        if s.get("has_agent"):
            stats["with_agent"] += 1
        # coffe-i31: None = sin señal (no 0) — excluido del total
        stats["total_api_errors"] += s.get("n_errors") or 0
        stats["total_compactions"] += s.get("n_compactions") or 0
        stats["avg_turns"] += n
        stats["avg_tools"] += s.get("n_tools") or 0
        if s.get("n_skills") is not None:
            stats["avg_skills"] += s["n_skills"]
        longest.append((n, s["duration_msgs"], s["first_ts"], s["project"]))

        bt = stats["by_tool"][s.get("tool", "claude-cli")]
        bt["total"] += 1
        if s.get("has_agent"): bt["with_agent"] += 1

    if sessions:
        n = len(sessions)
        stats["avg_turns"] /= n
        stats["avg_tools"] /= n
        n_sk = sum(1 for s in sessions if s.get("n_skills") is not None)
        stats["avg_skills"] = stats["avg_skills"] / n_sk if n_sk else 0

    # coffe-i31: resets por tool — señales distintas por harness
    # (FPA-117/118 adaptado): claude cuenta /clear del history.jsonl;
    # en pi cada /new abre un archivo nuevo (el conteo de archivos de
    # sesión ES el conteo de resets); amp es 1 tarea = 1 corrida.
    commands = commands or Counter()
    for tool, bt in stats["by_tool"].items():
        if tool == "claude-cli":
            bt["resets"] = {"signal": "/clear en history.jsonl (solo claude)",
                            "count": commands.get("/clear", 0)}
        elif tool == "pi":
            bt["resets"] = {"signal": "archivos de sesión pi (1 archivo = 1 /new)",
                            "count": bt["total"]}
        else:
            bt["resets"] = {"signal": f"tareas {tool} (1 dir = 1 corrida)",
                            "count": bt["total"]}
    stats["by_tool"] = dict(stats["by_tool"])

    longest.sort(key=lambda x: -x[0])
    stats["top_longest_by_turns"] = [
        {"turns": t, "msgs": m, "date": d, "project": clean_proj_name(p)}
        for t, m, d, p in longest[:10]
    ]
    return stats


def collect_skills_and_commands():
    """Extrae skills (cache de Claude) y comandos slash (history.jsonl).

    Vive FUERA de aggregate() para que la agregación sea pura y testeable:
    los resultados se inyectan como parámetros.
    """
    by_skill_total = Counter()
    skills_by_project = defaultdict(Counter)  # proyecto limpio -> skill -> count
    cache_file = CLAUDE_DIR / "dashboard-cache.json"
    if cache_file.exists():
        cache = json.loads(cache_file.read_text())
        for key, summary in cache.get("entries", {}).items():
            if summary.get("source") != "claude": continue
            proj = summary.get("project", "")
            if not is_charly(proj): continue
            proj_clean = clean_proj_name(proj)
            for skill, count in summary.get("skill_uses", {}).items():
                by_skill_total[skill] += count
                skills_by_project[proj_clean][skill] += count

    commands = Counter()
    hist_file = CLAUDE_DIR / "history.jsonl"
    if hist_file.exists():
        with open(hist_file) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                display = entry.get("display", "")
                proj = entry.get("project", "")
                if not is_charly(proj): continue
                stripped = display.strip()
                if stripped.startswith("/") and len(stripped) > 2:
                    cmd = stripped.split()[0]
                    if 2 <= len(cmd) <= 30:
                        commands[cmd] += 1

    return by_skill_total, skills_by_project, commands


def aggregate(interactions, sessions, skills_total=None, skills_by_project=None,
              commands=None, excluded=None, interaction_kinds=None,
              outcomes=None, outcomes_reason=None):
    """Agrega rows → reporte completo. Pura: los datos de skills/comandos
    se inyectan (ver collect_skills_and_commands())."""
    hourly = {}
    daily = {}
    monthly = {}
    by_project = {}
    hour_projects = defaultdict(set)  # hora -> proyectos distintos activos
    day_projects = defaultdict(set)   # día -> proyectos distintos activos
    proj_day = defaultdict(Counter)   # proyecto -> {día: interacciones}
    row_kinds = defaultdict(Counter)  # FPA-140: kinds desde filas (assistant/tool)
    # FPA-011/019: breakdown mensual por tool y modelo
    month_tools = defaultdict(lambda: defaultdict(lambda: {
        "interactions": 0, "cost_effective": 0.0, "cost_real": 0.0,
        "models": defaultdict(lambda: {"interactions": 0, "cost_effective": 0.0})}))
    month_models = defaultdict(lambda: defaultdict(lambda: {
        "interactions": 0, "cost_effective": 0.0,
        "in": 0, "out": 0, "cache_read": 0, "cache_write": 0}))
    # FPA-012/021: proyecto × mes y proyecto × modelo
    # add-project-cost-token-timeline (d1): tokens por proyecto×mes para la
    # timeline de volumen y coste del dashboard (d2)
    project_monthly = defaultdict(lambda: defaultdict(lambda: {
        "interactions": 0, "cost_effective": 0.0, "cost_real": 0.0,
        "tokens": {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}}))
    project_models = defaultdict(lambda: defaultdict(lambda: {
        "interactions": 0, "cost_effective": 0.0}))

    for r in interactions:
        h = r["hour"]
        d = h[:10]
        m = d[:7]
        real, _, _ = get_sub_cost(r["tool"], r.get("timestamp", ""), r.get("cost_effective", 0) or 0)

        if h not in hourly: hourly[h] = HourlyBucket()
        if d not in daily: daily[d] = Bucket()
        if m not in monthly: monthly[m] = Bucket()
        hourly[h].add(r, real)
        daily[d].add(r, real)
        monthly[m].add(r, real)

        proj = clean_proj_name(r.get("project", "unknown"))
        hour_projects[h].add(proj)
        day_projects[d].add(proj)
        proj_day[proj][d] += 1
        if proj not in by_project: by_project[proj] = Bucket()
        by_project[proj].add(r, real)
        # FPA-011: tool/model del mes
        mt = month_tools[m][r["tool"]]
        mt["interactions"] += 1
        mt["cost_effective"] += r.get("cost_effective", 0) or 0
        mt["cost_real"] += real
        mtm = mt["models"][r["model_raw"]]
        mtm["interactions"] += 1
        mtm["cost_effective"] += r.get("cost_effective", 0) or 0
        mm = month_models[m][r["model_raw"]]
        mm["interactions"] += 1
        mm["cost_effective"] += r.get("cost_effective", 0) or 0
        mm["in"] += r.get("input_tokens", 0) or 0
        mm["out"] += r.get("output_tokens", 0) or 0
        mm["cache_read"] += r.get("cache_read_tokens", 0) or 0
        mm["cache_write"] += r.get("cache_write_tokens", 0) or 0
        # FPA-012/021
        pmm = project_monthly[proj][m]
        pmm["interactions"] += 1
        pmm["cost_effective"] += r.get("cost_effective", 0) or 0
        pmm["cost_real"] += real
        pmm["tokens"]["in"] += r.get("input_tokens", 0) or 0
        pmm["tokens"]["out"] += r.get("output_tokens", 0) or 0
        pmm["tokens"]["cache_read"] += r.get("cache_read_tokens", 0) or 0
        pmm["tokens"]["cache_write"] += r.get("cache_write_tokens", 0) or 0
        pmo = project_models[proj][r["model_raw"]]
        pmo["interactions"] += 1
        pmo["cost_effective"] += r.get("cost_effective", 0) or 0
        # FPA-140: interacciones por kind — filas (assistant/tool) + inyectados
        # (user prompts de extractores)
        kind_key = (r.get("timestamp") or "")[:7]
        if kind_key:
            row_kinds.setdefault(kind_key, Counter())[r.get("kind", "assistant_turn")] += 1
        # first/last seen
        ts = r["timestamp"]
        pp = by_project[proj]
        if pp.first_seen is None or ts < pp.first_seen:
            pp.first_seen = ts
        if pp.last_seen is None or ts > pp.last_seen:
            pp.last_seen = ts

    # --- Multitasking + context switches ---
    switches_by_day = _context_switches(interactions)
    multitasking = _multitasking_block(hour_projects, day_projects,
                                       {d: b.to_dict() for d, b in daily.items()},
                                       switches_by_day)

    # Conteo de proyectos activos por hora
    for h, ps in hour_projects.items():
        if h in hourly:
            hourly[h].projects_active = len(ps)

    # --- Subscription fees ---
    monthly_dicts = {m: b.to_dict(detail=True) for m, b in monthly.items()}
    sub_fees = calc_subscription_fees(monthly_dicts)
    for m_key, fee in sub_fees.items():
        if m_key in monthly:
            monthly[m_key].cost_real += fee
            monthly_dicts[m_key]["cost_real"] = round(monthly[m_key].cost_real, 2)
            monthly_dicts[m_key]["subscription_fees"] = fee
        for d_key, d_data in daily.items():
            if d_key[:7] == m_key:
                d_data.cost_real += fee / 30.0  # prorated roughly

    # --- coffe-a31.2: contabilidad separada tracker vs fee implícito ---
    # La suma mensual de cost_real mezcla la parte tracker (p2p real) con los
    # fees implícitos del calendario; metadata (sumatoria horaria) solo tenía
    # la parte tracker → divergencia 611.62 vs 381.62. Se emiten ambas partes
    # por mes; cost_total_real pasa a ser la suma mensual (una sola cuenta).
    for m_key, mo in monthly_dicts.items():
        fee_mes = mo.get("subscription_fees", 0.0)
        mo["cost_real_tracker"] = round(mo["cost_real"] - fee_mes, 2)
        mo["pay_per_token_provenance"] = "assumed"  # puede pasar a reported abajo
    tracker_total = round(sum(b.cost_real for b in hourly.values()), 2)

    # --- coffe-a31.2 (CRG-F1): ledger de cargos reales (FPA-013 reported) ---
    try:
        charges_by_month, charges_total, charges_p2p = cargar_charges()
        charges_ok, charges_reason = True, None
    except (OSError, ValueError, json.JSONDecodeError) as e:
        charges_by_month, charges_total, charges_p2p = {}, 0.0, {}
        charges_ok, charges_reason = False, f"unavailable: ledger ilegible ({e})"

    # --- Emisiones F2 (coffe-lat.3) en monthly_dicts ---
    for m_key, mo in monthly_dicts.items():
        # FPA-011: breakdown por tool con coste (interacciones + coste efectivo)
        mo["tools"] = {t: {
            "interactions": st["interactions"],
            "cost_effective": round(st["cost_effective"], 8),
            "cost_real": round(st["cost_real"], 8),
            "models": {mm: {"interactions": ms["interactions"],
                            "cost_effective": round(ms["cost_effective"], 8)}
                       for mm, ms in sorted(st["models"].items())},
        } for t, st in sorted(month_tools.get(m_key, {}).items())}
        # FPA-011: breakdown por modelo con coste
        mo["models"] = {m: {
            "interactions": st["interactions"],
            "cost_effective": round(st["cost_effective"], 8),
        } for m, st in sorted(month_models.get(m_key, {}).items())}
        # FPA-019: tokens por mes y modelo (in/out/cache_read/cache_write)
        mo["tokens_by_model"] = {m: {
            "in": st["in"], "out": st["out"],
            "cache_read": st["cache_read"], "cache_write": st["cache_write"],
        } for m, st in sorted(month_models.get(m_key, {}).items())}
        # FPA-013 (coffe-a31.2): cargas pay-per-token del mes. Con cargas
        # reales del ledger (créditos/reembolsos — FPA-013 deja de ser
        # assumed) la cifra es *reported*; si no, el estimado tracker
        # (assumed). Los fees implícitos del calendario NO se mezclan acá.
        # p2p real del mes según el ledger: por PRESENCIA de cargas, no por
        # monto ≠ 0 (un crédito + un refund que netean $0 siguen siendo
        # facturas reales → reported)
        tiene_p2p = charges_ok and any(
            m_key in meses for meses in charges_p2p.values())
        if tiene_p2p:
            mo["pay_per_token_charges"] = round(sum(
                meses.get(m_key, 0.0)
                for meses in charges_p2p.values()), 8)
            mo["pay_per_token_provenance"] = "reported"
        else:
            mo["pay_per_token_charges"] = round(
                mo["cost_real_tracker"], 8)
        # FPA-140: kinds del mes = filas + inyectados (user prompts)
        mo["interaction_kinds"] = dict(Counter(
            {**row_kinds.get(m_key, {}),
             **(interaction_kinds or {}).get(m_key, {})}))
        # FPA-014: outcomes donde haya GitHub token (vacío si no, con razón)
        mo["outcomes_by_project"] = (outcomes or {}).get(m_key, {})

    # --- coffe-7mj.1: energía estimada (kWh) por mes y modelo (aditivo) ---
    _energy_for_month(monthly_dicts)

    # --- Skills (inyectados; default: colección en vivo) ---
    if skills_total is None or skills_by_project is None or commands is None:
        skills_total, skills_by_project, commands = collect_skills_and_commands()
    skills_total = Counter(skills_total)
    commands = Counter(commands)
    for proj_clean, sk in skills_by_project.items():
        if proj_clean in by_project:
            by_project[proj_clean].skills = Counter(sk)

    # --- Sessions ---
    session_stats = _session_stats(sessions, commands)

    # --- project_daily ---
    project_daily = _project_daily(by_project, day_projects, proj_day)

    # --- FPA-120: concurrencia etiquetada ---
    active_hours = len(hour_projects)
    total_switches = sum(switches_by_day.values())
    peak_projects = max((len(ps) for ps in hour_projects.values()), default=0)
    peak_sessions = _peak_simultaneous_sessions(sessions)
    concurrency = {
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

    # --- FPA-141: excluidos por el filtro charly ---
    excluded = excluded or []

    def _excl_n(e):
        # fila completa (1 interacción) o resumen por proyecto (contador)
        return e["interactions"] if e.get("interactions") is not None else 1

    filtered_interactions = sum(_excl_n(e) for e in excluded)
    filtered_cost = sum(
        (e.get("cost_effective") or 0) * _excl_n(e) for e in excluded
        if e.get("cost_effective") is not None)
    total_all = sum(b.interactions for b in hourly.values()) + filtered_interactions
    filtered_out = {
        "interactions": filtered_interactions,
        "cost_effective": round(filtered_cost, 2) if filtered_cost else 0.0,
        "by_tool": dict(Counter({e.get("tool", "?"): _excl_n(e)
                                 for e in excluded})),
        "share": round(filtered_interactions / total_all, 4) if total_all else 0.0,
    }

    # --- Sesiones por mes (KPIs: coste por sesión, autonomous share) ---
    sessions_monthly = defaultdict(lambda: {"total": 0, "with_agent": 0})
    for s in sessions:
        ym = (s.get("first_ts") or "")[:7]
        if len(ym) == 7:
            sessions_monthly[ym]["total"] += 1
            if s.get("has_agent"):
                sessions_monthly[ym]["with_agent"] += 1

    def clean(o):
        if isinstance(o, defaultdict):
            return {k: clean(v) for k, v in o.items()}
        if isinstance(o, Counter):
            return dict(o.most_common())
        return o

    return clean({
        "metadata": {
            "date_range": {
                "start": min(r["timestamp"] for r in interactions)[:10] if interactions else None,
                "end": max(r["timestamp"] for r in interactions)[:10] if interactions else None,
            },
            "filter": "in-scope" if CHARLY_FILTER else "all",
            "total_interactions": sum(b.interactions for b in hourly.values()),
            "total_input_tokens": sum(b.input_tokens for b in hourly.values()),
            "total_output_tokens": sum(b.output_tokens for b in hourly.values()),
            "total_cache_read_tokens": sum(b.cache_read_tokens for b in hourly.values()),
            "total_cache_write_tokens": sum(b.cache_write_tokens for b in hourly.values()),
            "cost_total_effective": round(sum(b.cost_effective for b in hourly.values()), 2),
            # coffe-a31.2: unificada con la suma mensual (antes: sumatoria
            # horaria sin fees implícitos → divergencia 381.62 vs 611.62).
            # Los fees implícitos del calendario quedan identificados aparte
            # (subscription_fees_by_month) y NO son cash (FPA-082).
            "cost_total_real": round(sum(mo["cost_real"] for mo in monthly_dicts.values()), 2),
            "cost_real_total_tracker": tracker_total,
            "subscription_fees": round(sum(sub_fees.values()), 2),
            # coffe-a31.2: cash cost real del ledger (provenance reported).
            # Medida separada del efectivo: jamás sumarlas (FPA-002).
            "charges_total_real": round(charges_total, 2),
            "charges_source": CHARGES_SOURCE,
            "charges_provenance": "reported" if charges_ok else "unavailable",
            "charges_reason": charges_reason,  # FPA-008: n/a con razón
            # coffe-7mj.1: energía estimada — SIEMPRE assumed (ningún
            # proveedor reporta kWh; coeficientes de laboratorio versionados
            # en config/fpa.json). Unavailable con razón solo en fallback
            # sin config (FPA-008).
            "energy_provenance": "assumed" if _FPA_CONFIG is not None else "unavailable",
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
            "timezone": str(LOCAL_TZ),  # FPA-142: TZ usada en buckets hourly/daily
            "pay_per_token_note": (
                "pay_per_token_charges es *reported* cuando el ledger tiene "
                "cargas pay-per-token reales del mes (créditos/reembolsos, "
                "FPA-013); *assumed* (estimado tokens × pricing) si no"),
            "outcomes": outcomes_reason or "unavailable: no emisionado en esta corrida",  # FPA-014
            "token_accounting": (
                "cache_read/cache_write se reportan aparte de input/output. "
                "cache_read no se factura a input rate (10x mas barato); "
                "total tokens = input + output + cache_read + cache_write"
            ),
            "total_hours": len(hourly),
            "total_days": len(daily),
            "total_months": len(monthly),
            "total_projects": len(by_project),
            # coffe-mbz: fuente de verdad de suscripciones/pricing
            "config_source": CONFIG_SOURCE or "builtin-fallback",
        },
        "hourly": {h: b.to_dict() | {"projects_active": getattr(b, "projects_active", 0)}
                   for h, b in hourly.items()},
        "daily": {d: b.to_dict() for d, b in daily.items()},
        "monthly": dict(sorted(monthly_dicts.items())),
        "projects": {p: (b.to_dict() | {
            "first_seen": (b.first_seen or "")[:10],
            "last_seen": (b.last_seen or "")[:10],
            "skills": dict(getattr(b, "skills", {}).most_common()),
        }) for p, b in sorted(by_project.items(), key=lambda x: -x[1].cost_effective)},
        "skills": dict(skills_total.most_common(50)),
        "commands": dict(commands.most_common(30)),
        "sessions": session_stats,
        "multitasking": multitasking,
        "project_daily": project_daily,
        "project_monthly": {p: dict(ms) for p, ms in project_monthly.items()},
        "project_models": {p: {m: {"interactions": v["interactions"],
                                   "cost_effective": round(v["cost_effective"], 8)}
                              for m, v in sorted(models.items())}
                           for p, models in project_models.items()},
        "concurrency": concurrency,
        "filtered_out": filtered_out,
        "sessions_monthly": dict(sorted(sessions_monthly.items())),
        "subscription_config": SUBSCRIPTIONS,
        "subscription_fees_by_month": sub_fees,
        # coffe-a31.2: cash real del ledger por proveedor y mes (provenance
        # reported), y el insumo de reconciliación FPA-082 (real vs fee
        # implícito del calendario vs efectivo estimado)
        "charges_real_by_month": charges_by_month,
        "charges_reconciliation_by_month": reconciliation_charges(
            charges_by_month, monthly_dicts) if charges_ok else {},
        # coffe-mbz: config de pricing cargado (no constantes muertas)
        "model_pricing_config": {
            "default_rates": DEFAULT_RATES,
            "cache_write_factor": _CACHE_WRITE_FACTOR,
            "versions": _PRICING_VERSIONS,
        },
    })

def main():
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
    args = ap.parse_args()

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

    ventana = ""
    if SINCE or UNTIL:
        ventana = f", ventana: {SINCE or 'inicio'}..{UNTIL or 'hoy'}"
    print(f"=== IA Usage Tracker v4.3 (filtro: {args.filter}{ventana}) ===", flush=True)

    interactions = []
    skipped = {}
    kinds = {}       # FPA-140: eventos por kind/mes (incluye user prompts)
    excluded = []    # FPA-141: filas fuera del filtro charly

    claude_rows, claude_excluded = extract_claude(skipped, kinds, excluded)
    pi_rows, pi_excluded = extract_pi(skipped, kinds, excluded)
    all_sources = [
        ("Claude", claude_rows),
        ("Pi", pi_rows),
        ("Amp", extract_amp()),
        ("Codex", extract_codex(skipped)),  # coffe-2sy: CLI standalone
    ]
    excluded.extend(claude_excluded)
    excluded.extend(pi_excluded)

    for name, rows in all_sources:
        print(f"  {name}: {len(rows)} rows", flush=True)
        interactions.extend(rows)

    # Dedup across sources
    seen = set()
    unique = []
    for r in sorted(interactions, key=lambda x: x["timestamp"]):
        key = (r["timestamp"], r["tool"], r["model_raw"], r.get("source", ""), r.get("project", ""))
        if key not in seen:
            seen.add(key)
            unique.append(r)

    print(f"  Total: {len(interactions)} → {len(unique)} unique", flush=True)

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

    print("Session stats...", flush=True)
    sessions = extract_sessions()  # coffe-i31: claude + pi + amp con provenance
    print(f"  {len(sessions)} charly sessions", flush=True)

    print("Aggregating...", flush=True)
    outcomes, outcomes_reason = collect_outcomes()
    report = aggregate(unique, sessions, *collect_skills_and_commands(),
                       excluded=excluded, interaction_kinds=kinds,
                       outcomes=outcomes, outcomes_reason=outcomes_reason)
    if skipped:
        report["metadata"]["skipped"] = dict(skipped)
        report["metadata"]["skipped_lines"] = sum(skipped.values())
    if SINCE or UNTIL:
        # coffe-snj: registrar la ventana pedida (reproducibilidad)
        report["metadata"]["window"] = {
            "since": SINCE.isoformat() if SINCE else None,
            "until": UNTIL.isoformat() if UNTIL else None,
        }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    # Pretty print
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

    print(f"\n--- Skills ---")
    for skill, count in list(report['skills'].items())[:10]:
        print(f"  {skill}: {count}")

    print(f"\n--- Commands ---")
    for cmd, count in list(report['commands'].items())[:10]:
        print(f"  {cmd}: {count}")

    print(f"\nDone. {out_path}")


if __name__ == "__main__":
    main()