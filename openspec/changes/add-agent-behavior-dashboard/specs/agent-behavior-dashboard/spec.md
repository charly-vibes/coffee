# Spec: agent-behavior-dashboard

## ADDED Requirements

### Requirement: El dataset de comportamiento del agente declara tareas, cobertura y provenance
El tracker SHALL emitir `data/agent_metrics.json` validable contra
`specs/agent-metrics.schema.json`, con serie diaria, series compactas por
tarea (`day`, `s`, `sp`, `src`, `model`, `f`), panel de repos por mes,
`metadata.task_definition` y `metadata.coverage` por fuente (con `note` o
razón cuando la fuente no expone turnos con timestamp o señal de aborto).

#### Scenario: Fuente sin señal de aborto
- **WHEN** una fuente (claude-cli) no registra `stopReason`
- **THEN** su entrada de cobertura declara `abort_signal: false` con la razón
- **AND** el dashboard informa la tasa de abortos como piso, nunca como 0.

#### Scenario: Dataset inválido
- **WHEN** el dataset no cumple el schema
- **THEN** `--validate` sale con código distinto de 0 y lista los errores.

### Requirement: Las métricas se recalculan por período con percentiles exactos
El dashboard SHALL ofrecer las ventanas período completo, YTD, año,
trimestre, mes y todo rango contiguo de ≥2 meses, pre-calculadas en Python
sobre las tareas de la ventana; el P90 de una ventana NO se deriva de P90
mensuales.

#### Scenario: Ventana con tareas
- **WHEN** el usuario elige un mes con tareas
- **THEN** los KPIs muestran prompts/día, P50/P90/P99 de duración, horas de
  agente y tasas de aborto/reintento calculados sobre las tareas de ese mes.

#### Scenario: Ventana sin tareas
- **WHEN** la ventana no tiene tareas
- **THEN** la duración se muestra `n/a` y las cifras derivadas quedan en
  `null`, sin inventar ceros.

### Requirement: Repos creados y mantenidos salen de git local
El dashboard SHALL contar repos creados (primer commit en la ventana) y
mantenidos (>5 días con commit en la ventana, con "vuelven" cuando hay
commits en ≥2 meses distintos), leyendo `git log` de las raíces de
`agent_behavior.repo_roots` sin depender de la API de GitHub.

#### Scenario: Repo con commits sólo en un mes
- **WHEN** un repo tiene >5 días con commit pero todos en un solo mes de la ventana
- **THEN** cuenta como mantenido pero no como "vuelve".

### Requirement: Cada cifra declara provenance y el sitio enlaza el dashboard
El dashboard SHALL mostrar provenance por cifra (`reported`/`derived`),
declarar los límites (interacciones ≠ prompts) y estar enlazado desde
`index.html` con back-link al índice.

#### Scenario: Página publicada
- **WHEN** se regenera el sitio
- **THEN** `data/agent-dashboard.html` aparece en `index.html` y tiene
  `href="../index.html"` (guard `tests/test_site_nav.py`).
