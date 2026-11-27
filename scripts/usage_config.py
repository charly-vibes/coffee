#!/usr/bin/env python3
"""Carga de config FPA, pricing, coste y energía (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state
import importlib.util  # noqa: F401  (necesario antes de _link)
import os  # noqa: F401
import re  # noqa: F401  (load_config() corre en import, antes de _link)
from usage_state import (_HERE, _REPO_ROOT, _FALLBACK_SUBSCRIPTIONS,  # noqa: F401
                         _FALLBACK_MODEL_PRICING, _FALLBACK_DEFAULT_RATES,
                         _FALLBACK_MODEL_MAPPING, _DEFAULT_RATES_FALLBACKS)

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
    print(
        "WARNING: config/fpa.json no encontrado — usando SUBSCRIPTIONS/"
        "MODEL_PRICING hardcodeadas (fallback; ignorá este warning solo "
        "en máquinas sin el repo)",
        file=sys.stderr,
    )
    usage_state.set_state("SUBSCRIPTIONS", _FALLBACK_SUBSCRIPTIONS)
    usage_state.set_state("MODEL_PRICING", _FALLBACK_MODEL_PRICING)
    usage_state.set_state("DEFAULT_RATES", _FALLBACK_DEFAULT_RATES)
    usage_state.set_state("_MODEL_MAPPING",
                    _compile_model_mapping(_FALLBACK_MODEL_MAPPING))
    usage_state.set_state("_CACHE_WRITE_FACTOR", 1.25)
    usage_state.set_state("_PRICING_VERSIONS",
                    [{"effective": None, "rates": MODEL_PRICING}])
    usage_state.set_state("_FPA_CONFIG", None)
    usage_state.set_state("_ENERGY_COEFFS", None)
    return None


def _apply_fpa_config(cfg, source):
    """Valida y aplica el config F0 a los globals del módulo."""
    errors = fpa_config.validate_config(cfg)
    if errors:
        raise ValueError(f"config inválido ({source}):\n" + "\n".join(errors))
    usage_state.set_state("_FPA_CONFIG", cfg)
    pricing = cfg["model_pricing"]
    usage_state.set_state("SUBSCRIPTIONS", cfg["subscriptions"])
    usage_state.set_state("DEFAULT_RATES", pricing["default_rates"])
    # coffe-dnx.1: mapeo modelo → familia/versión desde config (orden =
    # prioridad explícita; sin bloque o vacío → fallback hardcodeado).
    usage_state.set_state("_MODEL_MAPPING", _compile_model_mapping(
        cfg.get("model_mapping", {}).get("entries") or _FALLBACK_MODEL_MAPPING))
    usage_state.set_state("_CACHE_WRITE_FACTOR",
                    pricing.get("cache_write_factor", 1.25))
    usage_state.set_state("_PRICING_VERSIONS", pricing.get("versions", []))
    # coffe-7mj.1: coeficientes de energía (bloque requerido, validado arriba
    # por validate_config → ValueError si falta o está malformado)
    usage_state.set_state("_ENERGY_COEFFS", cfg["energy_coefficients"])
    # Compat: rates de la última versión conocida (los extractores piden
    # rates por fecha vía estimate_cost(..., when=...))
    if _PRICING_VERSIONS:
        usage_state.set_state("MODEL_PRICING", max(
            _PRICING_VERSIONS, key=lambda v: v["effective"])["rates"])
    else:
        usage_state.set_state("MODEL_PRICING", {})
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

    explicit = path or os.environ.get("TRACKER_CONFIG")
    usage_state.set_state("CONFIG_SOURCE", None)
    source, cfg = _locate_fpa_config(explicit)
    if source is not None:
        usage_state.set_state("CONFIG_SOURCE", str(source))
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

usage_state._link(globals())
