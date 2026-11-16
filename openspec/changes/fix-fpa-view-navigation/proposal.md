# Proposal: fix-fpa-view-navigation

## Why

La vista "Gasto diario" se agregó sin incluirla en el mecanismo de una vista
a la vez:

- El CSS oculta `#summary, #cost, #breakdown, #habits, #outlook, #energy,
  #data` y omite `#gasto`; tampoco existe `body[data-view="gasto"] #gasto`.
  Al cargar se ven Resumen + Gasto diario (2.155 px @1280×900); al cambiar a
  Hábitos quedan Hábitos + Gasto diario (2.076 px).
- El smoke `tests/test_viz.py:61` declara 6 vistas y no incluye `gasto` ni
  `energy`, así que la regresión no se detecta por estado; solo falla por
  altura (`2076 not less than 1800`).

## What Changes

- **fpa-dashboard**: el CSS de hide/show enumera las 8 vistas
  (`summary, cost, breakdown, gasto, habits, outlook, energy, data`), y el
  estado inicial muestra exactamente una.
- **test**: la lista de vistas del smoke se deriva de las tabs renderizadas
  (no hardcodeada), y verifica carga inicial + cada tab.
- Sin cambios de datos, schema ni cifras.

## Impact

- Affected specs: `fpa-dashboard`.
- Affected code: `scripts/viz-fpa.py` (CSS/JS de vistas),
  `tests/test_viz.py`, `data/fpa-dashboard.html`.
- Riesgo: bajo; es una corrección de contrato existente y un test más
  estricto.