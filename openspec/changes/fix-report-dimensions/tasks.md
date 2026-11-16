# Tasks: fix-report-dimensions

## 1. Dimensiones

- [ ] 1.1 Test de taxonomía: `openrouter` es provider, no source; `pi` es
      source; `qwen-token-plan-individual` es plan; `sessions.by_tool` usa
      source.
- [ ] 1.2 Emitir `sources`/`providers`/`plans` en `monthly` y `projects`;
      migrar `session_spend` a `source`/`provider`/`plan`.
- [ ] 1.3 Eliminar `tools` y actualizar schema/goldens.
- [ ] 1.4 Migrar `viz-fpa.py`, `viz-index.py`, `viz-gantt.py` y tests.

## 2. Excluidos

- [ ] 2.1 Test con proyecto fuera de scope: `filtered_out` trae
      `interactions`, `sessions`, `files` y `cost_effective` con unidades
      separadas.
- [ ] 2.2 Implementar el conteo real de interacciones excluidas o `null` con
      razón; `share` usa interacciones.

## 3. Período

- [ ] 3.1 Test near-midnight: `date_range` (local) y `date_range_utc`
      difieren en un día y ambos son correctos.
- [ ] 3.2 Emitir ambos campos y alinear README/index/dashboard.

## 4. Contexto

- [ ] 4.1 Test: sesión Pi con path mungido emite `ak-catitas`, no
      `--var-home-...`.
- [ ] 4.2 Implementar `clean_proj_name`/`amp_proj_from_uri` en
      `extract_context_metrics`.

## 5. Docs y verificación

- [ ] 5.1 Actualizar README (glosario source/provider/plan, período,
      excluidos), eliminar la fila duplicada de `usage_report_v3.json`,
      corregir los tamaños de artefactos (o derivarlos) y alinear el conteo
      del índice.
- [ ] 5.2 Regenerar reporte, dashboards e índice; `--check-docs` verde.
- [ ] 5.3 Suite de tracker, viz, ctx, index y site-nav.