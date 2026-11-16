## ADDED Requirements

### Requirement: cobertura de sesiones anidadas de omp

El tracker SHALL (debe) caminar las sesiones omp directas y las de subagente anidadas (`<cwd>/<parent>/*.jsonl`). Cada llamada facturable SHALL (debe) contarse una vez; el solape padre/hijo se resuelve por id nativo o hash de uso. Los subagentes SHALL (debe)N publicarse en `sessions.subagents`, no en `total_sessions`, y `agent_semantics` SHALL (debe) declarar la señal.

#### Scenario: subagente anidado con telemetría

- **WHEN** un archivo `sessions/<cwd>/<parent>/Planner.jsonl` contiene
  mensajes assistant con `usage`
- **THEN** cada llamada entra en `total_interactions`, `monthly`, `projects`
  y `session_spend`, y `sessions.subagents.omp.total` aumenta

#### Scenario: solape padre/hijo

- **WHEN** el mismo `responseId` (o el mismo hash de uso) aparece en el
  archivo padre y en el subagente
- **THEN** se cuenta una sola vez, con una razón de dedupe visible en
  `metadata.skipped` si se descarta

#### Scenario: señal de subagentes declarada

- **WHEN** se emite `sessions.agent_semantics`
- **THEN** omp declara que existen archivos de subagente y cómo se cuentan
  (p.ej. `"omp: subagentes en sessions.subagents; total_sessions = conversaciones directas"`)

### Requirement: dedupe con identidad de sesión

El dedupe global SHALL (debe) incluir `source` e identidad de sesión (`session_file`, id nativo o `responseId`). Filas con igual clave pero distinto archivo/responseId SHALL (debe)N conservarse. Los mensajes copiados entre forks SHALL (debe) deduplicarse por identidad de mensaje, y los eventos Amp del mismo timestamp en un task SHALL (debe)N conservarse como interacciones distintas.

#### Scenario: cuatro archivos Amp al mismo ms

- **WHEN** un task de Amp registra `parser.go`, `config.go`, `main.go` y
  `session.go` en el mismo timestamp
- **THEN** el reporte cuenta 4 interacciones, no 1

#### Scenario: llamadas Pi paralelas al mismo ms

- **WHEN** dos sesiones Pi distintas registran una llamada con el mismo
  timestamp y modelo pero distinto `responseId`/tokens
- **THEN** ambas se conservan

#### Scenario: copia de un fork de Pi

- **WHEN** dos archivos Pi contienen el mismo mensaje copiado
- **THEN** el agregado lo cuenta una vez y el reporte no lo pierde en
  silencio (`metadata.deduped_copies`)

### Requirement: forks de Pi en context_metrics

Un archivo Pi que copia el prefijo de otro SHALL (debe) marcarse `fork_of` (id del origen); `context_metrics` SHALL (debe) contar solo los turnos nuevos en `monthly_model` y percentiles. `sessions` MAY contar cada archivo como sesión separada, pero el prefijo copiado NO SHALL (debe) duplicarse en contexto. Si el origen es indeterminable, `meta.forks_unresolved` lo registra.

#### Scenario: fork con prefijo copiado

- **WHEN** el archivo A tiene 78 turnos y el archivo B tiene los mismos 78
  turnos más 15 nuevos
- **THEN** `context_metrics.monthly_model` suma 93 turnos y B emite
  `fork_of: A`

#### Scenario: fork no resuelto

- **WHEN** dos archivos comparten turnos pero no se puede identificar el
  origen
- **THEN** ambos se cuentan como sesiones independientes y
  `meta.forks_unresolved` aumenta; no se deduplica a ciegas

## MODIFIED Requirements

### Requirement: filas por sesión con coste y tokens

El tracker SHALL (debe) emitir en `session_spend` una fila por sesión nativa
in-scope (`source` + id nativo; para logs sin id, hash de reserva). La fila
SHALL (debe) incluir como mínimo: `session_id`, `project` (proyecto dominante
o `mixed`), `projects` (mapa proyecto → `interactions`/`cost_effective`/
`cost_real`/`tokens` cuando la sesión toca más de uno; requerido para hilos
Amp multi-proyecto), `day` (día local de la primera interacción), `source`
(harness: pi, claude-cli, amp, codex, omp), `provider` (proveedor/router),
`plan` (plan de suscripción o `null`), `model` dominante (o `mixed`),
`cost_effective`, `cost_real`, `tokens` con `in`/`out`/`cache_read`/
`cache_write`, y `n_interactions`. Las sesiones de subagente omp SHALL (debe)N
emitirse como filas propias (su `session_id` nativo las distingue).

#### Scenario: sesión con coste completo

- **WHEN** el tracker procesa una sesión con telemetría `usage.cost` que no
  cruza medianoche
- **THEN** la fila trae coste y tokens consistentes con la suma de sus
  interacciones, y la suma de `session_spend` del día cuadra con
  `project_daily_spend` y con el bucket diario global

#### Scenario: hilo Amp multi-proyecto

- **WHEN** un hilo Amp toca 4 repos en el mismo día
- **THEN** hay una sola fila con `project` dominante y `projects` con los 4
  desgloses; no hay 4 filas con el mismo `session_id`

#### Scenario: sesión sin identificador nativo

- **WHEN** el log de una sesión no trae identificador de sesión nativo
- **THEN** la fila se emite igual con un `session_id` de reserva derivado de
  la ruta del archivo (hash), marcando `session_id_fallback: true`