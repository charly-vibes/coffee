# Proposal: fix-report-dimensions

## Why

El reporte usa "tool" para tres conceptos incompatibles y varias cifras de
alcance no están separadas:

- `monthly[].tools` / `projects[].tools` / `session_spend[].source` mezclan
  harness (`pi`, `claude-cli`, `amp`, `codex`, `omp`), proveedor/router
  (`openrouter`, `minimax`, `openai`, `mistral`) y plan
  (`qwen-token-plan-individual`, `kimi-coding`). El 84,6% de las
  interacciones aparece como `openrouter`, que no es una herramienta.
- El índice publica "5 proveedores" leyendo `sessions.by_tool` (harness),
  mientras el dashboard dice "herramientas" para proveedores/planes.
- `filtered_out.interactions` cuenta archivos de sesión, no interacciones:
  5.868 reportado vs 19.550 mensajes Pi fuera de scope (7,0%).
- `metadata.date_range` usa timestamps UTC y los buckets usan TZ local:
  README dice 2026-11-15, metadata/índice 2026-11-16.
- `context_metrics.sessions[].project` emite el label mungido de Pi
  (`--var-home-...-ak-catitas--`), no `ak-catitas`.

## What Changes

- **usage-report**: dimensiones explícitas `source` (harness), `provider`
  (router/API) y `plan` (suscripción); `monthly`/`projects` emiten
  `sources`/`providers`/`plans`; `session_spend` separa `source`/`provider`/
  `plan`.
- **usage-report**: `filtered_out` emite `interactions`, `sessions`, `files`
  y `cost_effective` (nullable con razón); `share` usa interacciones.
- **usage-report**: `metadata.date_range` = rango local de buckets;
  `metadata.date_range_utc` = rango UTC de timestamps.
- **fpa-dashboard**: glosario y labels usan la dimensión correcta; el índice
  deriva su conteo de `metadata` y no de `sessions.by_tool`.
- **context-dashboard**: labels de proyecto normalizados en Patologías.
- **BREAKING (dataset)**: `tools` desaparece en favor de `sources`,
  `providers` y `plans`; `session_spend.source` cambia de proveedor a
  harness. Migración: consumidores leen la dimensión que necesitan; el
  schema se versiona y se regenera el dataset.

## Impact

- Affected specs: `usage-report`, `fpa-dashboard`, `context-dashboard`.
- Affected code: `scripts/usage-tracker.py` (`aggregate`, extractores,
  `extract_context_metrics`), `specs/usage-report-v3.schema.json`,
  `scripts/viz-fpa.py`, `scripts/viz-index.py`, `scripts/viz-ctx.py`,
  tests, dashboards, README y `index.html`.
- Datos: los totales no cambian; cambia la forma de agrupar y las etiquetas.
  El reporte puede crecer por las tres dimensiones (acotado).

## Dependencies

- Implementar después de `fix-session-accounting`: ese cambio introduce
  `source`/`provider`/`plan` en `session_spend` y la identidad de sesión. Este
  cambio generaliza la taxonomía a `monthly`/`projects` y migra los
  consumidores.

## Tickets

- coffe-c15.5 (taxonomía), coffe-c15.6 (filtered_out), coffe-c15.7 (período), coffe-c15.8 (labels de contexto), coffe-c15.11 (README).
