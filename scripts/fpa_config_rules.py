#!/usr/bin/env python3
"""fpa_config_rules.py — validadores por sección del config FP&A (driver: validate_config)."""

import re

REQUIRED_SECTIONS = (
    "taxonomy",
    "subscriptions",
    "model_pricing",
    "energy_coefficients",
    "budgets",
    "alert_thresholds",
    "premium_models",
    "working_hours",
    "lifecycle",
    "ctas",
    "language",
    "retro",
)

_MONTH_RE = re.compile(r"^[0-9]{4}-[0-9]{2}$")
_HHMM_RE = re.compile(r"^[0-9]{2}:[0-9]{2}$")
_DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


def _missing_sections(cfg):
    return [f"sección requerida ausente: {section}"
            for section in REQUIRED_SECTIONS if section not in cfg]


def _validate_taxonomy(cfg):
    taxonomy = cfg.get("taxonomy", {})
    if not isinstance(taxonomy, dict):
        return ["taxonomy debe ser un objeto"]
    errors = []
    for key in ("rules", "overrides", "fallback"):
        if key not in taxonomy:
            errors.append(f"taxonomy.{key} ausente")
    for i, rule in enumerate(taxonomy.get("rules", [])):
        if not isinstance(rule, dict) or "match" not in rule or "category" not in rule:
            errors.append(f"taxonomy.rules[{i}] sin match/category")
    return errors


def _validate_repo_entries(entries, key, owners):
    errors = []
    for entry in entries:
        if not isinstance(entry, str) or "/" not in entry:
            errors.append(f"repos.{key}: entrada sin 'org/repo': {entry!r}")
        elif entry.split("/", 1)[0] not in owners:
            errors.append(f"repos.{key}: org desconocida {entry!r} "
                          f"(owners: {sorted(owners)})")
    return errors


def _validate_url_overrides(repos):
    errors = []
    for label, url in (repos.get("url_overrides") or {}).items():
        if not isinstance(url, str) or not url.startswith("https://"):
            errors.append(f"repos.url_overrides[{label!r}]: URL no https: {url!r}")
    return errors


def _validate_repos(repos):
    if not isinstance(repos, dict):
        return ["repos debe ser un objeto"]
    owners = repos.get("owners") or {}
    if not isinstance(owners, dict) or not owners:
        return ["repos.owners ausente o vacío (label-prefix → org GH)"]
    errors = []
    for key in ("private", "no_remote"):
        errors += _validate_repo_entries(repos.get(key) or [], key, owners)
    return errors + _validate_url_overrides(repos)


def _validate_subscription_entry(tool, i, entry):
    if not isinstance(entry, dict):
        return [f"subscriptions.{tool}[{i}] no es objeto"]
    errors = []
    if not _DATE_RE.match(str(entry.get("start", ""))):
        errors.append(f"subscriptions.{tool}[{i}].start inválida")
    end = entry.get("end")
    if end is not None and not _DATE_RE.match(str(end)):
        errors.append(f"subscriptions.{tool}[{i}].end inválida")
    if not isinstance(entry.get("monthly_fee"), (int, float)):
        errors.append(f"subscriptions.{tool}[{i}].monthly_fee inválido")
    return errors


def _validate_subscription_entries(tool, entries):
    if not isinstance(entries, list) or not entries:
        return [f"subscriptions.{tool}: lista vacía o no lista"]
    errors = []
    for i, entry in enumerate(entries):
        errors += _validate_subscription_entry(tool, i, entry)
    return errors


def _validate_subscriptions(subscriptions):
    if not isinstance(subscriptions, dict):
        return ["subscriptions debe ser un objeto"]
    errors = []
    for tool, entries in subscriptions.items():
        errors += _validate_subscription_entries(tool, entries)
    return errors


def _validate_pricing(pricing):
    if not isinstance(pricing, dict):
        return ["model_pricing debe ser un objeto"]
    errors = []
    if "default_rates" not in pricing:
        errors.append("model_pricing.default_rates ausente")
    for i, version in enumerate(pricing.get("versions", [])):
        if not isinstance(version, dict) or not _DATE_RE.match(str(version.get("effective", ""))):
            errors.append(f"model_pricing.versions[{i}].effective inválida")
    return errors


def _valid_energy_tiers(tiers):
    return isinstance(tiers, dict) and all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0
        for v in tiers.values()) \
        and {"flash", "mid", "frontier"}.issubset(tiers)


def _validate_energy_factors(energy):
    errors = []
    factor = energy.get("cache_read_energy_factor")
    if not isinstance(factor, (int, float)) or isinstance(factor, bool) \
            or not 0 <= factor <= 1:
        errors.append(f"energy_coefficients.cache_read_energy_factor "
                      f"inválido: {factor!r} (número en [0, 1])")
    tiers = energy.get("tiers")
    if not _valid_energy_tiers(tiers):
        errors.append("energy_coefficients.tiers debe ser objeto con "
                      "J/token > 0 por tier (mínimo flash/mid/frontier)")
    default_tier = energy.get("default_tier")
    if not isinstance(default_tier, str) or not isinstance(tiers, dict) \
            or default_tier not in tiers:
        errors.append(f"energy_coefficients.default_tier inválido: "
                      f"{default_tier!r} (debe ser un tier declarado)")
    return errors


def _validate_energy_model_tiers(i, mapping, vtiers):
    errors = []
    for model, tier in mapping.items():
        if tier not in vtiers:
            errors.append(f"energy_coefficients.versions[{i}]"
                          f".model_tiers[{model!r}]: tier "
                          f"desconocido {tier!r}")
    return errors


def _validate_energy_version(i, version, tiers):
    if not isinstance(version, dict) or not _DATE_RE.match(str(version.get("effective", ""))):
        return [f"energy_coefficients.versions[{i}].effective inválida"]
    errors = []
    vtiers = version.get("tiers")
    if not isinstance(vtiers, dict):
        errors.append(f"energy_coefficients.versions[{i}].tiers "
                      "debe ser objeto")
    elif isinstance(tiers, dict) and any(k not in tiers for k in vtiers):
        errors.append(f"energy_coefficients.versions[{i}].tiers usa "
                      "tiers no declarados en el bloque raíz")
    mapping = version.get("model_tiers")
    if not isinstance(mapping, dict):
        errors.append(f"energy_coefficients.versions[{i}].model_tiers "
                      "debe ser objeto (modelo → tier declarado)")
    elif isinstance(vtiers, dict):
        errors += _validate_energy_model_tiers(i, mapping, vtiers)
    return errors


def _validate_energy_versions(versions, tiers):
    errors = []
    for i, version in enumerate(versions):
        errors += _validate_energy_version(i, version, tiers)
    return errors


def _validate_energy(energy):
    """coffe-7mj.1: energía estimada (kWh) — bloque requerido, fail-loud."""
    if not isinstance(energy, dict):
        return ["energy_coefficients debe ser un objeto (ausente en "
                "configs previos a add-energy-estimates)"]
    errors = _validate_energy_factors(energy)
    tiers = energy.get("tiers")
    versions = energy.get("versions")
    if not isinstance(versions, list) or not versions:
        errors.append("energy_coefficients.versions ausente o vacía")
    errors += _validate_energy_versions(versions or [], tiers)
    return errors


def _validate_budgets(budgets):
    errors = []
    if "start_month" in budgets and not _MONTH_RE.match(str(budgets["start_month"])):
        errors.append(f"budgets.start_month inválido: {budgets['start_month']}")
    for key in ("cash_monthly", "effective_monthly", "target_per_1k"):
        val = budgets.get(key)
        if not isinstance(val, (int, float)) or val < 0:
            errors.append(f"budgets.{key} inválido: {val}")
    return errors


def _validate_working_hours(wh):
    errors = []
    for day in wh.get("days", []):
        if not isinstance(day, int) or not 1 <= day <= 7:
            errors.append(f"working_hours.days inválido: {day}")
    for key in ("start", "end"):
        if key in wh and not _HHMM_RE.match(str(wh[key])):
            errors.append(f"working_hours.{key} inválido: {wh[key]}")
    return errors


def _validate_optional_thresholds(cfg):
    """Umbrales opcionales de F5 (FPA-118/119): solo si la clave existe."""
    errors = []
    for section, key in (("timeline", "gap_days"), ("sessions", "long_turns")):
        if section not in cfg:
            continue
        val = cfg[section].get(key) if isinstance(cfg[section], dict) else None
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            errors.append(f"{section}.{key} inválido: {val}")
    return errors


def _validate_alert_thresholds(thresholds):
    errors = []
    for key, val in thresholds.items():
        if not isinstance(val, (int, float)) or isinstance(val, bool) or val < 0:
            errors.append(f"alert_thresholds.{key} inválido: {val}")
    return errors


def _validate_cta_targets(ctas):
    """FPA-168: primary + secondary con target no vacío."""
    errors = []
    secondary = ctas.get("secondary", [])
    if not isinstance(secondary, list):
        errors.append("ctas.secondary debe ser una lista")
        secondary = []
    for cta in [ctas.get("primary", {}), *secondary]:
        if not isinstance(cta, dict) or not cta.get("target"):
            errors.append(f"cta con target vacío: {cta.get('label', '?') if isinstance(cta, dict) else cta}")
    return errors


def _validate_cta_alert_actions(actions):
    """FPA-167/168: alert_actions con target obligatorio (nunca vacío)."""
    if not isinstance(actions, dict):
        return ["ctas.alert_actions debe ser un objeto"]
    errors = []
    for rule, act in actions.items():
        if not isinstance(act, dict) or not act.get("target"):
            errors.append(f"cta con target vacío: {rule}")
        elif not act.get("label"):
            errors.append(f"cta sin label: {rule}")
    return errors


def _validate_ctas(cfg):
    """FPA-167/168: CTAs y alert_actions con target/label obligatorios."""
    if "ctas" not in cfg:
        return []
    ctas = cfg["ctas"]
    if not isinstance(ctas, dict):
        return ["ctas debe ser un objeto"]
    errors = _validate_cta_targets(ctas)
    errors += _validate_cta_alert_actions(ctas.get("alert_actions", {}))
    return errors


def validate_config(cfg):
    """Validar el config; devuelve lista de errores ([] = válido)."""
    errors = []
    site_name = cfg.get("site_name")
    if not isinstance(site_name, str) or not site_name.strip():
        errors.append("site_name ausente o vacío (nombre llano del "
                      "dashboard, change update-fpa-site-integration)")
    errors += _missing_sections(cfg)
    errors += _validate_taxonomy(cfg)
    repos = cfg.get("repos")
    if repos is not None:  # coffe-vp8: bloque opcional pero con forma estricta
        errors += _validate_repos(repos)
    errors += _validate_subscriptions(cfg.get("subscriptions", {}))
    errors += _validate_pricing(cfg.get("model_pricing", {}))
    errors += _validate_energy(cfg.get("energy_coefficients"))
    errors += _validate_budgets(cfg.get("budgets", {}))
    errors += _validate_working_hours(cfg.get("working_hours", {}))
    errors += _validate_optional_thresholds(cfg)
    errors += _validate_alert_thresholds(cfg.get("alert_thresholds", {}))
    errors += _validate_ctas(cfg)
    return errors
