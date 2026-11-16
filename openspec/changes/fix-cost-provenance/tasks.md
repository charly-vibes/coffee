# Tasks: fix-cost-provenance

## 1. Semántica de coste

- [ ] 1.1 Test Pi: `usage.cost.total=0` en `openrouter/free` ⇒
      `cost_effective=0`, `cost_real=0`, sin estimación.
- [ ] 1.2 Test plan: `qwen-token-plan-individual` con `totalFromProvider:false`
      ⇒ `cost_real=0`; `cost_effective` estimado solo si hay pricing, con
      provenance `assumed`.
- [ ] 1.3 Test `None` vs `0`: turno abortado sin coste ⇒ `null` + razón.
- [ ] 1.4 Implementar `cost: float | None` y el gate de estimación.

## 2. Provenance

- [ ] 2.1 Test de fila con coste reportado/derivado/asumido y de agregado
      `mixed`.
- [ ] 2.2 Emitir `cost_effective_provenance` en filas, `monthly`, `projects`,
      `session_spend` y metadata (desglose por tag).
- [ ] 2.3 Actualizar schema y goldens; validar que un agregado con estimación
      no se etiqueta `reported`.

## 3. Dashboard y guía

- [ ] 3.1 Test del KPI: mezcla reportada/estimada ⇒ tag `mixed`; todo
      reportado ⇒ `reported`.
- [ ] 3.2 Reescribir `provenance en toda figura` (spec) y alinear
      `build_headlines`/tags de tablas.
- [ ] 3.3 Actualizar la guía con la definición de los cuatro tags.
- [ ] 3.4 `viz-fpa --check-docs` verde.

## 4. Regeneración

- [ ] 4.1 Regenerar `data/usage_report_v3.json` y verificar que free/plan
      bajan y `cost_real` no incluye estimaciones.
- [ ] 4.2 Regenerar `data/fpa-dashboard.html` y `data/fpa-guide.html`.
- [ ] 4.3 Actualizar README (semántica de coste y provenance).
- [ ] 4.4 Suite: `tests/test_tracker_amp.py`, `test_tracker_codex.py`,
      `test_tracker_omp.py`, `test_tracker.py`, `test_fpa.py`,
      `test_fpa_guide.py`, `test_viz.py`.