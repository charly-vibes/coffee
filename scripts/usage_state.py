#!/usr/bin/env python3
"""Estado compartido (constantes, dirs, config, flags) extraído de usage-tracker.py.

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state

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

# --- maquinaria de descomposición (coffe-kav.1) ------------------------------
# Los módulos usage_* comparten namespace vía _link() (patrón viz_fpa_common)
# y el estado mutable viaja por este módulo: el facade re-exporta los nombres
# y cualquier setattr sobre él (p. ej. ut.SINCE = ... en tests) se propaga a
# todos los módulos, replicando la semántica de namespace único del script
# original.
import gc
import types

_facades = []


def _link(g):
    """Inyectar los nombres de todos los módulos usage_* en `g` (setdefault).

    Se invoca al final de cada módulo: las definiciones del módulo llamante
    están completas y las referencias hacia atrás se resuelven al vuelo
    (import_module nunca re-ejecuta un módulo en curso). Un mismo archivo
    puede ejecutarse varias veces bajo nombres distintos (el facade lo
    cargan los tests como usage_tracker, usage_tracker_window, ...).
    """
    import importlib
    import os
    here = os.path.dirname(os.path.abspath(g["__file__"]))
    siblings = [f[:-3] for f in sorted(os.listdir(here))
                if f.startswith("usage_") and f.endswith(".py")]
    for s in siblings:
        m = importlib.import_module(s)
        for k, v in vars(m).items():
            if not k.startswith("__"):
                g.setdefault(k, v)


def set_state(key, value):
    """Escribir una variable de estado y propagarla a todos los módulos
    usage_* y facades registrados (namespace único del script original)."""
    globals()[key] = value
    for name, m in tuple(sys.modules.items()):
        if name.startswith("usage_") and isinstance(m, types.ModuleType):
            m.__dict__[key] = value
    for f in _facades:
        f.__dict__[key] = value


class _FacadeModule(types.ModuleType):
    """Clase del módulo facade: intercepta setattr y propaga al estado."""

    def __setattr__(self, key, value):
        if not key.startswith("__"):
            set_state(key, value)
        super().__setattr__(key, value)


def bind_facade(g):
    """Vincular el módulo facade en ejecución: swap de __class__ (para
    interceptar setattr de los tests) + registro para set()."""
    mod = None
    for ref in gc.get_referrers(g):
        if isinstance(ref, types.ModuleType) and getattr(ref, "__dict__", None) is g:
            mod = ref
            break
    if mod is None:
        raise RuntimeError("no se encontró el módulo facade en ejecución")
    mod.__class__ = _FacadeModule
    _facades.append(mod)


usage_state._link(globals())
