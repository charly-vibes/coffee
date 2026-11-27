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

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state  # noqa: E402

usage_state.bind_facade(globals())
usage_state._link(globals())

from usage_cli import main  # noqa: E402  (re-exportado para consumers/tests)

if __name__ == "__main__":
    main()
