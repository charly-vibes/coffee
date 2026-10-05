# add-project-cost-token-timeline

## Why

El dashboard muestra coste efectivo mensual por proyecto (aggregation de coste
por proyecto) pero NO una línea de tiempo por proyecto: no hay forma de ver
cómo el coste y el volumen de tokens de un proyecto evolucionan mes a mes, ni
comparar la curva de coste entre proyectos a lo largo del reporte. La agregación
actual por proyecto solo tiene totales (`by_project`) y por mes solo
`interactions`+`cost_effective` (FPA-012), sin tokens.

## What Changes

1. **Tracker (usage-report):** emisión aditiva de `project_monthly[m].tokens`
   (`in`/`out`/`cache_read`/`cache_write` por proyecto y mes) y
   `project_monthly[m].cost_real`, junto a las cifras existentes. El campo de
   coste efectivo por proyecto/mes sigue existiendo igual.
2. **Dashboard (fpa-dashboard):** nueva vista "Proyectos: timeline" que renderiza,
   por proyecto, una serie mensual de coste efectivo y volumen de tokens
   (stacked área/SVG inline, stdlib-only, mismo tema), con: selector de proyecto,
   alternancia coste/tokens, tooltips, tag *assumed* en el coste efectivo,
   deep-link por hash y export CSV como las demás vistas. Los meses con coste
   efectivo 0.0 pero tokens > 0 muestran la razón (modelos con pricing faltante)
   en vez de un área vacía silenciosa.

## Impact

- **Affected specs:** `usage-report` (emisión aditiva), `fpa-dashboard` (nueva
  vista)
- **Affected code:** `scripts/usage-tracker.py` (emisión), `scripts/viz-fpa.py`
  (vista), `specs/usage-report-v3.schema.json` (campos requeridos nuevos),
  `tests/test_tracker.py` (casos de emisión), `README.md` (documentación)
- **Compatibilidad:** aditivo — ningún campo existente cambia de semántica; el
  golden snapshot se regenera y se revisa el diff
