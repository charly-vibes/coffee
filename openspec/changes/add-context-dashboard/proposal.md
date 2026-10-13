# add-context-dashboard

## Why

Investigaciones sobre las sesiones de Pi (`~/.pi/agent/sessions`, oct-2026) revelaron
métricas que el reporte actual no captura y que resultaron decisivas para entender el
uso: la distribución del *pico de contexto* por sesión (p50 75k, p90 180k tok
estimados), la atribución del contexto por fuente (assistant 51%, bash 32%, read 15%),
la ley de crecimiento (~850 tok/turno lineal en todas las eras), y la evolución por
mes × modelo (tres eras: Codex/GPT-5.x → DeepSeek/GLM-5.2 → GLM-5.3-flash), con el
coste asociado ($1,171 totales; el gasto es función del contexto, no del output:
r(cost, input+cache) ≈ 0.90–0.99).

Hoy el reporte tiene interacciones y tokens por proyecto/mes, pero no: pico de
contexto por sesión, atribución por fuente (assistant text/thinking/args, resultados
por tool), crecimiento por turno, coste por sesión desagregado, ni detección de
sesiones patológicas (markup DSML rojo de DeepSeek, usage inválido de claude-cli).

## What Changes

1. **Tracker (usage-report):** emisión aditiva de una sección `context_metrics` al
   reporte, con:
   - `sessions[]`: una fila por sesión de Pi in-scope con `month`, `project`,
     `model`, `models_turns` (atribución de turnos por modelo — las sesiones
     contienen `model_change` mid-sesión), `provider`, `turns`, `peak_ctx_est`
     (pico **por segmento de compactación**, ver design), `final_ctx_est`,
     `compactions`, atribución de chars por fuente (`asst_text`, `asst_thinking`,
     `asst_args`, `tool:<name>`), `big_results` por tool (resultados > umbral de
     config), `cost_in/out/cr/cw` (null con razón cuando no hay billing por token,
     patrón "modelos sin telemetría").
   - `monthly_model[]`: agregados por mes × modelo dominante (p50/p90 del pico,
     % sesiones > umbral de sesión grande, tok/turno por fuente, spend por
     componente).
   - `meta`: método de estimación (`chars_per_token: 4`, tag *assumed*, con
     dirección de bias documentada), ratio de validación mediano contra proveedores
     de usage confiable (`validation_ratio_median`), umbrales usados (big_result,
     pathological, big session — todos de config), conteo de sesiones excluidas por
     patología y de stubs (sesiones sin turnos).
2. **Corrección de semántica vigente:** el tracker hoy declara "sin señal en pi
   (compactions)" (`n_compactions: None`); las sesiones de Pi sí contienen entradas
   `type=="compaction"` desde ~may-2026. Esta change corrige el docstring, emite
   `n_compactions` real para Pi y ajusta `sessions.total_compactions`.
3. **Dashboard (nueva capability `context-dashboard`):** `scripts/viz-ctx.py` genera
   `data/context-dashboard.html` — HTML autocontenido, stdlib-only, SVG inline, tema
   compartido (`site_theme.py`), tags de provenancia (*reported*/*assumed*/*subscription*)
   y deep-link/export CSV como las vistas de fpa-dashboard. Vistas:
   - *Distribución*: histograma del pico de contexto por sesión + banda de percentiles
     sobre el set limpio (sin patológicas, `turns > 0`), con conteo de excluidas visible.
   - *Composición*: share de contexto por fuente, global y dentro de sesiones grandes.
   - *Tiempo*: tabla/series por mes × modelo (pico mediano, % > umbral, tok/turno),
     meses parciales marcados.
   - *Coste*: spend por modelo, split in/out/cache, spend mensual y $/sesión;
     sesiones claude-cli separadas con provenancia *subscription*.
   - *Ley de crecimiento*: dispersivo pico vs turnos con pendiente por modelo (~850
     tok/turno).
   - *Patologías*: outliers marcados con razón (glitches DSML, usage inválido de
     claude-cli) — visibles, nunca silenciosos (consistente con FPA-008).
4. **Integración:** `nightly-update.sh` regenera el dashboard nuevo (midiendo la
   duración extra del walk sobre el dir de sesiones, hoy ~1.4GB); README documenta
   la emisión y las vistas; `--check-docs` de viz-ctx.py valida consistencia
   README ↔ JSON al estilo de viz-fpa.

## Scope

- **Pi-only** para las métricas de contexto: el objeto de la investigación es Pi,
  y el walk reusa el agrupamiento por sesión existente (`extract_pi_sessions`,
  coffe-i31). Claude/Amp/Codex quedan fuera de `context_metrics` (sus formatos de
  usage difieren y no participaron de la investigación); extender es un cambio
  futuro si se pide.

## Impact

- **Affected specs:** `usage-report` (ADDED: sección `context_metrics`; MODIFIED:
  semántica de compactions para Pi), `context-dashboard` (nueva capability, 6 vistas)
- **Affected code:** `scripts/usage-tracker.py` (emisión aditiva desde el extractor
  Pi existente + corrección de docstring/semántica de compactions),
  `scripts/viz-ctx.py` (nuevo), `scripts/site_theme.py` (sin cambios,
  solo consumo), `config/fpa.json` (umbrales de contexto), `specs/usage-report-v3.schema.json`
  (campos nuevos), `tests/test_tracker_context.py` (nuevo), `tests/test_viz.py`
  (smoke del HTML), `scripts/nightly-update.sh`, `README.md`
- **Compatibilidad:** aditivo — ningún campo existente cambia de semántica salvo
  `sessions.total_compactions` (corrección: de None a real para Pi), documentada.
  Golden snapshot se regenera y se revisa el diff. El dashboard nuevo es una página
  separada (precedente: Gantt), no una vista más de fpa-dashboard (que ya tiene 31
  requirements y 6 vistas).
- **Ticket de limpieza aparte (fuera de alcance):** el escenario del requirement
  "Report contract" de `usage-report` referencia `viz-dashboard.py`, removido en
  coffe-gen.5 — se filea como bug de spec, no se arregla aquí.
