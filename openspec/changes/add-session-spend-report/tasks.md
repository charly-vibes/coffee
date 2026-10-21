# Tasks: add-session-spend-report

## 1. Tracker: emisión `project_daily_spend`

- [ ] 1.1 Diseñar el test: síntesis de interacciones multi-proyecto/multi-día
      y shape esperado de `project_daily_spend` (cuadre con `daily` y con
      `project_monthly`)
- [ ] 1.2 Red: `tests/test_tracker_spend.py` falla porque la sección no existe
- [ ] 1.3 Implementar emisión en `scripts/usage-tracker.py` (agregación pura,
      inyectable, sparse por proyecto/día) y registro en el schema
      `specs/usage-report-v3.schema.json`
- [ ] 1.4 Green: tests pasan; regenerar golden (`REGEN_GOLDEN=1`) y verificar
      que el diff solo añade claves nuevas

## 2. Tracker: emisión `session_spend`

- [ ] 2.1 Diseñar el test: filas por sesión (coste completo, coste `null` +
      razón, sesión que cruza medianoche, sesión con cambio de modelo)
- [ ] 2.2 Red: casos fallan porque `session_spend` no existe
- [ ] 2.3 Implementar `session_spend` en el tracker + schema; documentar el
      contra-cuadre por interacción vs. por sesión en `README.md`
- [ ] 2.4 Green + golden: diff solo con campos nuevos

## 3. Dashboard: vista "Gasto diario"

- [ ] 3.1 Diseñar el test: smoke del HTML (0 pageerrors) con reporte de
      fixture que cubra sesión sin coste, día sin actividad y deep-link
- [ ] 3.2 Red: la vista no existe
- [ ] 3.3 Implementar la vista en `scripts/viz-fpa.py` (`fpa_config.py`):
      selector de día/rango, tabla por proyecto, drill-down por sesión,
      export CSV, deep-link, provenance
- [ ] 3.4 Green: smoke en Chromium pasa; verificar cuadre total del día vs.
      bucket diario global

## 4. Cierre

- [ ] 4.1 Regenerar artefactos (`data/fpa-dashboard.html`,
      `data/usage_report_v3.json`) y validar contra schema
- [ ] 4.2 Actualizar `README.md` (secciones nuevas del reporte)
- [ ] 4.3 Suite completa: `python3 tests/test_tracker_spend.py &&
      python3 tests/test_tracker.py && python3 tests/test_viz.py`
