## 1. Tracker

- [x] 1.1 `aggregate()`: acumular tokens (in/out/cache_read/cache_write) y
      `cost_real` en `project_monthly[proj][m]` junto a interactions y
      cost_effective existentes (puro, sin I/O)
- [x] 1.2 Casos en `tests/test_tracker.py`: proyecto/mes con tokens y cost_real,
      proyecto con múltiples meses, mes con coste 0.0 + tokens > 0
- [x] 1.3 `specs/usage-report-v3.schema.json`: requerir los campos nuevos y
      regenerar golden (`REGEN_GOLDEN=1`, revisar diff)
## 2. Dashboard

- [x] 2.1 `viz-fpa.py`: pre-calcular serie mensual coste efectivo y tokens por
      proyecto; pre-calcular la matemática de las áreas SVG en Python
- [x] 2.2 Vista "Proyectos: timeline": selector de proyecto, toggle coste/tokens,
      área SVG inline con tooltips, *assumed* en coste efectivo, deep-link por
      hash, export CSV/SVG
- [x] 2.3 Razón visible cuando coste efectivo = 0.0 con tokens > 0 (FPA-008)
## 3. Cierre

- [x] 3.1 `python3 tests/test_tracker.py` y `python3 tests/test_viz.py` verdes
- [x] 3.2 `viz-fpa.py --check-docs` consistencia README ↔ JSON
- [x] 3.3 README: documentar la vista y la emisión nueva
