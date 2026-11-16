# Design: fix-cost-reconciliation

## Contexto

Hay tres medidas de dinero:

1. `cost_effective` — valuación API-equivalente (assumed/derived).
2. `cost_real` — tracker: p2p + fees de suscripción del calendario (assumed).
3. `cash` — ledger `data/charges.json` (reported).

El bug no es que difieran (es esperado), sino que `daily.cost_real` intenta
prorratear fees con `fee/30` por día activo y no reconcilia con `monthly`.
Además, las alertas usan el fee asumido aunque el ledger tenga la factura.

## Decisiones

### D1: fees solo en el nivel mensual

`daily[].cost_real` y `project_daily_spend[].cost_real` miden el gasto
tracker p2p (sin fees). La suma de todos los días/proyectos de un mes
coincide con `cost_real_total_tracker`; `monthly[].cost_real` sigue sumando
los fees una vez. Se documenta la asimetría (ya existe para sesiones que
cruzan medianoche).

### D2: fee asumido vs reportado

`monthly[m]` emite:

- `subscription_fee_assumed`: calendario de `config/fpa.json` (assumed).
- `subscription_fee_reported`: suma de kind `subscription` del ledger para
  ese mes (nullable; `reported`).
- `subscription_fee_source`: `ledger|config|none`.
- `cost_real` usa reported si existe; si no, assumed. El cash sigue siendo
  ledger-only.

### D3: alertas con evidencia doble

`verify-plan` compara el coste efectivo del mes contra la cuota
`subscription_fee_reported` si existe; si no, contra la asumida. La evidencia
incluye ambas y el `source`. Un plan sin factura no dispara “múltiplo de
plan” con una cuota asumida obsoleta.

### D4: alcance del ledger

`metadata.charges_total_real` sigue siendo el total del ledger (todas las
fechas); se agrega `charges_total_real_period` restringido a los meses del
reporte. README y dashboard declaran que el cash del período excluye cargos
pre-período (p.ej. el crédito de $200 de oct-2025).

## Riesgos

- Cambia `daily.cost_real`; los gráficos que lo usen deben migrar a
  `cost_effective` o declarar que no incluye fees.
- Si el ledger tiene una suscripción con fecha fuera del mes, la asignación
  mensual debe ser por fecha de cargo (no por período del plan).
- La alerta puede volverse silenciosa si no hay ledger; en ese caso debe
  mostrar `n/a` con razón, no un falso positivo.