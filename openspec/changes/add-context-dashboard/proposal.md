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
     `model`, `provider`, `turns`, `peak_ctx_est`, `final_ctx_est`, `compactions`,
     atribución de chars por fuente (`asst_text`, `asst_thinking`, `asst_args`,
     `tool:<name>`), `big_results` por tool (resultados >25k chars),
     `cost_in/out/cr/cw` por sesión.
   - `monthly_model[]`: agregados por mes × modelo (p50/p90 del pico, % sesiones
     >100k, tok/turno por fuente, spend por componente).
   - `meta`: método de estimación (`chars_per_token: 4`, tag *assumed*) y conteo de
     sesiones excluidas por patología (bloque assistant >500k chars).
2. **Dashboard (nueva capability `context-dashboard`):** `scripts/viz-ctx.py` genera
   `data/context-dashboard.html` — HTML autocontenido, stdlib-only, SVG inline, tema
   compartido (`site_theme.py`), tags de provenancia (*reported*/*assumed*) y
   deep-link/export CSV como las vistas de fpa-dashboard. Vistas:
   - *Distribución*: histograma del pico de contexto por sesión + banda de percentiles.
   - *Composición*: share de contexto por fuente, global y dentro de sesiones >100k.
   - *Tiempo*: tabla/series por mes × modelo (pico mediano, % >100k, tok/turno).
   - *Coste*: spend por modelo, split in/out/cache, spend mensual y $/sesión.
   - *Ley de crecimiento*: dispersivo pico vs turnos con pendiente por modelo (~850
     tok/turno).
   - *Patologías*: outliers marcados (glitches DSML, usage inválido de claude-cli) —
     visibles, nunca silenciosos (consistente con FPA-008).
3. **Integración:** `nightly-update.sh` regenera el dashboard nuevo; README documenta
   la emisión y las vistas; `--check-docs` de viz-ctx.py valida consistencia
   README ↔ JSON al estilo de viz-fpa.

## Impact

- **Affected specs:** `usage-report` (ADDED: sección `context_metrics`),
  `context-dashboard` (nueva capability, 6 vistas)
- **Affected code:** `scripts/usage-tracker.py` (emisión aditiva desde el extractor
  Pi existente), `scripts/viz-ctx.py` (nuevo), `scripts/site_theme.py` (sin cambios,
  solo consumo), `specs/usage-report-v3.schema.json` (campos nuevos),
  `tests/test_tracker_context.py` (nuevo), `tests/test_viz.py` (smoke del HTML),
  `scripts/nightly-update.sh`, `README.md`
- **Compatibilidad:** aditivo — ningún campo existente cambia de semántica; golden
  snapshot se regenera y se revisa el diff. El dashboard nuevo es una página separada
  (precedente: Gantt), no una vista más de fpa-dashboard (que ya tiene 31
  requirements y 6 vistas).
