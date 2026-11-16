# Tasks: fix-fpa-view-navigation

## 1. Test primero

- [ ] 1.1 `tests/test_viz.py`: derivar la lista de vistas de los
      `a.tab[data-view]` renderizados (8 vistas) y assertar que la carga
      inicial muestra exactamente una.
- [ ] 1.2 Assertar que cada tab deja visible solo su vista (incluidas
      `gasto` y `energy`).
- [ ] 1.3 Red: el test falla con el HTML actual (Gasto diario visible junto
      a Resumen/Hábitos).

## 2. Fix

- [ ] 2.1 Agregar `#gasto` a la regla base de hide y a
      `body[data-view="gasto"] #gasto`.
- [ ] 2.2 Verificar que el estado inicial (`summary`) no deja `gasto`
      visible.
- [ ] 2.3 Regenerar `data/fpa-dashboard.html`.

## 3. Verificación

- [ ] 3.1 `python3 -m pytest tests/test_viz.py` verde; altura de Hábitos
      < 2 viewports a 1280×900.
- [ ] 3.2 Smoke de las 8 tabs en desktop y mobile (390×844).
- [ ] 3.3 Actualizar README si la lista de vistas publicadas cambia.