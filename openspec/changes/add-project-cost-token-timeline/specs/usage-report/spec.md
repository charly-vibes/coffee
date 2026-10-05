## ADDED Requirements

### Requirement: granularidad proyecto × mes con tokens y cost_real

El tracker SHALL (debe) emitir en `project_monthly[proj][m]`, junto a las
cifras existentes de `interactions` y `cost_effective` (FPA-012), los campos:
`cost_real` (USD *assumed* vía sub_cost, misma semántica que los buckets
horarios/diarios/mensuales) y `tokens` con `in`/`out`/`cache_read`/
`cache_write` (misma semántica que `tokens_by_model` en `month_models`).
La emisión SHALL (debe) ser aditiva: ningún campo existente de
`project_monthly` cambia de semántica.

#### Scenario: proyecto con tokens y cost_real por mes

- **WHEN** el tracker genera el reporte
- **THEN** cada entrada `project_monthly[proj][m]` incluye `cost_real` y
  `tokens` (`in`/`out`/`cache_read`/`cache_write`) consistentes con la suma
  de sus interacciones

#### Scenario: emisión aditiva no rompe el golden existente

- **WHEN** se regenera el golden con `REGEN_GOLDEN=1`
- **THEN** el diff solo muestra los campos nuevos; las cifras existentes son
  byte a byte idénticas

### Requirement: granularidad proyecto × día fuera de alcance

El tracker NO SHALL (debe) emitir coste efectivo ni tokens por proyecto y día
en esta emisión; la granularidad proyecto × mes es la de la vista del dashboard.
La actividad por proyecto/día sigue cubierta por `project_daily` (Gantt).

#### Scenario: sin coste diario por proyecto

- **WHEN** se inspecciona el reporte en busca de coste por proyecto y día
- **THEN** no existe tal campo; solo `project_monthly` y los buckets globales
