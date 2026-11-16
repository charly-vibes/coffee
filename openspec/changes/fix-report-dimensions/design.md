# Design: fix-report-dimensions

## Contexto

El dataset nació con una sola dimensión (`tool`) cuando las fuentes eran
Claude/Pi/Amp. Al sumar Pi con OpenRouter, omp, planes Qwen/Kimi y Codex CLI,
`tool` pasó a significar cosas distintas en distintas secciones.

## Decisiones

### D1: tres dimensiones canónicas

- `source`: harness que produce el log (`pi`, `claude-cli`, `amp`, `codex`,
  `omp`). Es la dimensión de `sessions.by_tool` y de `filtered_out.by_tool`.
- `provider`: proveedor/router que factura (`anthropic`, `openrouter`,
  `openai`, `minimax`, `mistral`, `github-copilot`, ...).
- `plan`: plan de suscripción cuando el proveedor factura por plan
  (`qwen-token-plan-individual`, `kimi-coding`, `claude-max`, ...).

`model` sigue siendo el id crudo. Un modelo `:free` puede tener provider
`openrouter` y plan `null`.

### D2: reemplazo, no alias

`monthly[].tools` y `projects[].tools` se reemplazan por
`monthly[].sources`, `monthly[].providers`, `monthly[].plans` (y sus pares
en `projects`). `session_spend[].source` pasa a harness; se agregan
`provider` y `plan`. No se mantiene `tools` como alias: el dataset se
regenera y se migran todos los consumidores en el mismo cambio (principio
de cutover limpio).

### D3: excluidos con unidades

`filtered_out` publica `interactions`, `sessions`, `files` y
`cost_effective` por separado. `share` se calcula con interacciones. Un
extractor que solo puede contar archivos (Pi/Claude) no puede llenar
`interactions` con archivos: emite `files` y deja `interactions=null` con
razón, o cuenta las filas si las tiene.

### D4: dos períodos explícitos

`metadata.date_range` es el rango local de `daily`/`hourly`;
`metadata.date_range_utc` es el rango UTC de los timestamps. Los
consumidores declaran cuál usan. README/index/dashboard usan el local para
"datos hasta"; el período de extracción y los logs pueden verse en UTC.

### D5: labels de contexto

`extract_context_metrics()` normaliza `project` con `clean_proj_name()` o
`amp_proj_from_uri()`, igual que el resto del reporte; el path crudo queda
en `project_raw` solo si hace falta para debugging.

## Riesgos

- Cambio breaking del schema; cualquier consumidor externo debe migrar.
- Tres dimensiones pueden triplicar el tamaño de `monthly`/`projects`; se
  emiten solo las dimensiones con datos (sparse) y se mide el tamaño.
- `filtered_out.cost_effective` puede ser `null` si no hay precios de los
  proyectos excluidos; se muestra razón, no 0 fake.