## Context

`project_monthly` ya existe (FPA-012) con `interactions` + `cost_effective` por
proyecto/mes. La vista necesita la misma granularidad con tokens y `cost_real`.

## Goals / Non-Goals

- **Goal:** granularidad proyecto × mes completa (interacciones, coste efectivo,
  cost_real, tokens), vista de timeline en el dashboard.
- **Non-Goal:** granularidad proyecto × día (el Gantt ya cubre actividad por día;
  coste diario por proyecto queda fuera por ruido); no tocar la granularidad
  existente `by_project`.

## Decisions

- **D1 — emisión aditiva en `project_monthly`:** reutilizar la estructura
  existente y agregar los campos, no crear un bloque paralelo. Los keys nuevos
  (`tokens`, `cost_real`) replican la semántica de `month_models`/`month_tools`.
- **D2 — vista por selección de proyecto, no small multiples:** un selector de
  proyecto + serie mensual completa es legible con ~40 proyectos; small multiples
  con 40 proyectos harían el HTML ilegible. La comparación entre proyectos queda
  en la tabla de aggregation de coste por proyecto existente.
- **D3 — tokens como volumen total (in+out+cache_read) y desglose on demand:**
  el área principal es volumen total; el desglose por componente es tooltip.

## Risks / Trade-offs

- HTML crece con otra vista (~tamanno comparable a las otras vistas); aceptado.
- `cost_real` en `project_monthly` puede inducir a creer que es cash medido: es
  *assumed* (sub_cost del calendario) como siempre; el dashboard lo taggea.

## Migration Plan

Aditivo. Regenerar golden + reporte; ninguna acción en datos existentes.

## Open Questions

- (ninguna)
