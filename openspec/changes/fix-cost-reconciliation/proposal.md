# Proposal: fix-cost-reconciliation

## Why

Dos medidas que el dashboard presenta como equivalentes no reconcilian:

- `daily[].cost_real` suma `fee/30` por **día activo**; la suma diaria es
  $1.757,69 contra $1.806,24 mensual (abril: $107,41 vs $139,99).
  `project_daily_spend` excluye fees ($1.606,26), así que el “cuadre” del
  README solo vale para `cost_effective`.
- El calendario de `config/fpa.json` y el ledger divergen: mayo 2026 suma
  $59,99 asumidos (claude $20) pero el ledger registra $139,99 (claude Max
  $100). La alerta `verify-plan` dice “45.0× el precio del plan ($20)”;
  con la cuota real es 9.0×.
- `metadata.charges_total_real` ($1.716,72) incluye $200 pre-período; el
  cash del período es $1.516,72 y el alcance no está declarado.

## What Changes

- **usage-report**: `daily[].cost_real` y `project_daily_spend[].cost_real`
  excluyen fees de suscripción; los fees viven solo en `monthly[].cost_real`.
  Se emiten `subscription_fee_assumed`, `subscription_fee_reported` y
  `subscription_fee_source`; el cash del ledger conserva su alcance y se
  agrega `charges_total_real_period`.
- **fpa-dashboard**: alertas `verify-plan` usan la cuota reportada del ledger
  cuando existe (con evidencia de ambas); economía de suscripción distingue
  fee reportado vs asumido; la vista Gasto diario declara que su serie real
  no incluye fees.
- **BREAKING (dataset)**: cambia la semántica de `daily[].cost_real` (ya no
  incluye fees). Migración: usar `monthly` para fees y `daily`/`project_daily_spend`
  para cash tracker; documentado en README.

## Impact

- Affected specs: `usage-report`, `fpa-dashboard`.
- Affected code: `scripts/usage-tracker.py` (`aggregate`, emisión mensual,
  reconciliación), `scripts/viz-fpa.py` (alertas, economía, Gasto diario),
  `config/fpa.json` (calendario), `data/charges.json`, tests, README,
  dashboards.
- Datos: `cost_total_real` no cambia (mensual), pero la suma diaria sí; el
  cash del ledger no cambia.

## Dependencies

- Implementar después de `fix-cost-provenance`: comparte los campos de coste
  por fila/agregado y la provenance de `cost_real`/`cost_effective`.