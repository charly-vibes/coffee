# Design: fix-session-accounting

## Contexto

Tres fuentes tienen semánticas de sesión distintas:

- Pi: un archivo JSONL por conversación; los forks copian el historial en un
  archivo nuevo.
- Claude: un archivo JSONL por sesión; el cache solo aporta costo.
- Amp: un directorio por tarea; una tarea puede tocar varios repos.
- omp: un archivo JSONL por conversación y un directorio anidado con los
  transcripts de subagentes.

El aggregate actual deduplica interacciones con una clave que no incluye la
identidad de sesión, y `session_spend` agrupa por `(tool, project,
session_file)`. Eso funciona para Pi/Claude, pero rompe en Amp multi-proyecto
y en omp anidado.

## Decisiones

### D1: omp anidado se camina, pero los subagentes no inflan `total_sessions`

`total_sessions` mide conversaciones de usuario. Las sesiones de subagente se
emiten en `sessions.subagents` (`{tool: {total, with_agent?, reason}}`) y sus
llamadas facturables entran a `total_interactions`, `monthly`, `projects` y
`session_spend`. Así el KPI de sesiones no se distorsiona y la cobertura de
uso queda completa. Por eso `session_spend` puede tener más filas que
`total_sessions`; README lo documenta.

### D2: identidad de sesión explícita

Cada fila lleva `source` (harness) y `session_file`/id nativo. El dedupe
global usa `(source, session_id, timestamp, model, kind, usage)` y conserva
filas con distinto `session_file`/`responseId`. Para Pi, los mensajes
copiados entre forks se deduplican por `(timestamp, model, input, output,
cache_read, cost)`; los mensajes nuevos del fork se conservan.

### D3: forks de Pi

Un fork es una conversación nueva (cuenta en `total_sessions`), pero
`context_metrics` no debe sumar dos veces el prefijo copiado. Cada fila de
contexto emite `fork_of` (id del archivo origen) cuando detecta un prefijo
idéntico; `monthly_model` y los percentiles cuentan solo los turnos nuevos
del fork. Si no se puede determinar el origen, el archivo se trata como
sesión independiente y se registra en `meta.forks_unresolved`.

### D4: Amp multi-proyecto en `session_spend`

Una fila por sesión nativa (`source` + `session_id`). Si la sesión toca más
de un proyecto, `project` es el dominante por interacciones y `projects` es
un mapa `{proyecto: {interactions, cost_effective, cost_real, tokens}}` con
la suma completa. No se duplica la sesión por proyecto; el drill-down del
dashboard puede mostrar el desglose.

### D5: compatibilidad

`source` deja de ser el proveedor en `session_spend`: pasa a ser el harness
(`pi`, `claude-cli`, `amp`, `codex`, `omp`) y se agrega `provider` (y `plan`
cuando aplique). Es un cambio de dataset versionado; se documenta en README
y se migran los consumidores en el mismo cambio.

## Riesgos

- Conteos de omp suben de forma visible (~54%): esperado y explicado en
  README; no es una regresión de coste.
- La detección de forks es heurística (prefijo idéntico). Si falla, el
  reporte debe declarar `forks_unresolved` en vez de deduplicar de más.
- El dedupe por identidad de mensaje puede ser costoso en sesiones grandes;
  se implementa con hash del payload de uso, no comparando JSON completo.