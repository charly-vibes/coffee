## 1. Tracker

- [ ] 1.1 Extractor Pi: acumular por sesión `turns`, pico/final de contexto
      (chars/4), atribución de chars por fuente (`asst_text`, `asst_thinking`,
      `asst_args`, tool results por `toolName`), `compactions`, `big_results`
      (>25k chars) — puro, inyectable como `aggregate()`
- [ ] 1.2 Coste por sesión: sumar `cost.in/out/cacheRead/cacheWrite` por sesión y
      marcar `context_source` (pi vs claude-cli)
- [ ] 1.3 Detección de patologías: sesión con bloque assistant >500k chars →
      excluida de percentiles, contada en `meta.excluded_pathological`
- [ ] 1.4 Agregados `monthly_model[]` por mes × modelo: p50/p90 del pico, % >100k,
      tok/turno por fuente, spend por componente
- [ ] 1.5 Tests: `tests/test_tracker_context.py` con filas sintéticas (sesión
      normal, con compaction, patológica, claude-cli, multi-mes) + regenerar golden
      (`REGEN_GOLDEN=1`, revisar diff)
- [ ] 1.6 `specs/usage-report-v3.schema.json`: requerir la sección nueva

## 2. Dashboard

- [ ] 2.1 `scripts/viz-ctx.py`: lee `usage_report_v3.json` → pre-calcular en Python
      (histograma, shares, pendientes OLS por modelo, tablas mes × modelo)
- [ ] 2.2 Vista *Distribución*: histograma del pico + banda de percentiles, tags
      *assumed* en el factor chars/4
- [ ] 2.3 Vista *Composición*: share por fuente global y en sesiones >100k (SVG
      stacked inline, tooltips)
- [ ] 2.4 Vista *Tiempo*: mes × modelo (pico mediano, % >100k, tok/turno) con
      months parciales marcados como fpa-dashboard
- [ ] 2.5 Vista *Coste*: spend por modelo, split in/out/cache, spend mensual,
      $/sesión; sesiones claude-cli separadas con provenancia *subscription*
- [ ] 2.6 Vista *Ley de crecimiento*: dispersivo pico vs turnos por modelo con
      pendiente OLS pre-calculada
- [ ] 2.7 Vista *Patologías*: outliers listados con razón visible
- [ ] 2.8 Cross-cutting: deep-link por hash, export CSV/SVG, tema compartido,
      smoke en Chromium vía `tests/test_viz.py` (0 pageerrors, charts renderizan)

## 3. Cierre

- [ ] 3.1 `python3 tests/test_tracker.py` y `python3 tests/test_viz.py` verdes
- [ ] 3.2 `--check-docs` de viz-ctx.py: consistencia README ↔ JSON
- [ ] 3.3 README: documentar emisión nueva + dashboard (tabla de scripts y de data/)
- [ ] 3.4 `nightly-update.sh`: paso de regeneración del dashboard de contexto
- [ ] 3.5 `bd close` de los tickets y push
