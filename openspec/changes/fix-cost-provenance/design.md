# Design: fix-cost-provenance

## Contexto

Hoy hay tres orígenes de coste mezclados sin marca:

1. `usage.cost.total` reportado por el proveedor (OpenRouter, omp).
2. Estimación determinista de tokens × `model_pricing` del config.
3. Estimación de fallback con `default_rates` cuando el modelo no está
   configurado, o cuando el proveedor reporta `0`/`totalFromProvider:false`.

El agregado `cost_effective` no distingue; el dashboard lo llama `reported`.

## Decisiones

### D1: `None` y `0` son distintos

`cost` se modela como `float | None`. `None` = no hay dato; `0` = el
proveedor reportó gratuidad o el plan cubre el turno. Solo `None` o
`totalFromProvider:false` sin coste numérico disparan estimación, y solo si
hay pricing conocido.

### D2: free y plan

- Modelos `:free`/`openrouter/free`: `cost_effective = 0`, `cost_real = 0`,
  provenance `reported` (el proveedor reporta 0).
- Planes (`qwen-token-plan-individual`, `kimi-coding`, suscripciones):
  `cost_real = 0` (no hay cargo por token). `cost_effective` MAY ser una
  estimación API-equivalente con provenance `assumed`; nunca se suma a
  `cost_real`.
- Si `totalFromProvider == false` y no hay pricing conocido: `null` con
  razón, no `default_rates`.

### D3: taxonomía de provenance

`reported` (factura/proveedor), `derived` (tokens × pricing configurado),
`assumed` (default rates, plan, config) y `mixed` (agregado). Se emite por
fila y por agregado (`monthly`, `projects`, `session_spend`, metadata).
`cost_real` emite su propio `cost_real_provenance` (`reported` si viene del
ledger/proveedor, `assumed` si es calendario).

### D4: dashboard

El KPI "Coste efectivo" con mezcla se etiqueta `mixed` (o `assumed` si todo
es estimado). El requisito `provenance en toda figura` se reescribe para
definir los cuatro tags y eliminar la contradicción "reported = del JSON".
La guía explica que `derived`/`assumed` no son facturas.

## Riesgos

- Cambian cifras publicadas; el README y la guía deben regenerarse en el
  mismo cambio.
- El schema debe aceptar provenance en agregados antiguos? No: el cambio es
  breaking y se regenera el dataset completo.