# Proposal: fix-cost-provenance

## Why

El reporte trata `coste = 0` como "sin dato" y estima a precio de lista:

- `extract_pi()` y `extract_omp()` hacen `if not cost and tokens` → estiman
  con `default_rates`. En el dataset vigente, modelos `:free` (p.ej.
  `openrouter/free`) y planes (`qwen-token-plan-individual`) aportan ~$278 de
  `cost_real` inventado; ~54% del `cost_effective` total es estimado.
- El dashboard etiqueta el KPI "Coste efectivo" como `reported`
  (`scripts/viz-fpa.py:224`), aunque el spec llama *assumed* al coste
  efectivo (`fpa-dashboard:687,720`). El propio requisito genérico de
  provenance (`:35-38`) se contradice: define `reported` = "del JSON".
- No hay provenance por cifra: un consumidor del JSON no puede separar
  costo reportado, derivado de tokens y asumido por config.

## What Changes

- **usage-report**: `cost` opcional con `None` ≠ `0`; free/plan no se
  estiman; `cost_effective_provenance` por fila/agregado
  (`reported|derived|assumed|mixed`); metadata con desglose.
- **fpa-dashboard**: provenance explícita en KPIs, tablas y guía; un
  agregado con estimaciones no se etiqueta `reported`; se resuelve la
  contradicción del spec.
- **BREAKING (dataset)**: cambian los importes de free/plan y aparece el
  campo de provenance. Migración: consumidores leen provenance y no asumen
  `reported`.
- README/guía actualizados con la definición canónica.

## Impact

- Affected specs: `usage-report`, `fpa-dashboard`.
- Affected code: `scripts/usage-tracker.py` (`extract_pi`, `extract_omp`,
  `estimate_cost`, `aggregate`, metadata), `specs/usage-report-v3.schema.json`,
  `scripts/viz-fpa.py` (`build_headlines`, KPIs, tags),
  `scripts/viz_fpa_guide.py`, tests, `data/usage_report_v3.json`,
  `data/fpa-dashboard.html`, `README.md`.
- Coste: los totales de free/plan bajan; `cost_real` deja de incluir costo
  estimado. El resto de la contabilidad no cambia.

## Tickets

- coffe-c15.2 (0 vs ausente), coffe-c15.3 (provenance).
