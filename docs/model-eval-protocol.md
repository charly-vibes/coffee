# Protocolo del experimento de modelos (coffe-dnx.9)

**Objetivo.** Medir si un modelo acepta tareas reales de repos propios: proporción de tareas *aceptadas*, coste por tarea y latencia, comparando la cohorte. Es un protocolo de diseño: no define umbrales de decisión (eso es `model_decision_rule`, coffe-dnx.8) ni instrumenta nada (runner: coffe-dnx.7).

## Cohorte (`benchmark_cohort`)

`glm-5.3-flash`, `deepseek-v4-flash`, `qwen3.8-flash`, `qwen3.8-max` + la referencia frontier `claude-opus-4.5` (solo techo comparativo, no compite). Debe coincidir con `benchmark_cohort` de coffe-dnx.8.

## Tareas

`config/model_eval.json` declara 40 tareas reales (nada sintético tipo SWE-bench), cada una con: `repo`, `base_commit` (SHA verificado con `git rev-parse`), `prompt` literal, `acceptance` (comando ejecutable **o** criterio escrito verificable; nunca "que funcione"), `class` (bugfix | feature | docs | tests), `difficulty` (easy | medium | hard) y `bead` de origen opcional. Selección explícita: cuadrícula **clase × dificultad × repo** sobre beads abiertas de `.beads/issues.jsonl` (coffee + siblings bajo `agent_behavior.repo_roots` de `config/fpa.json`). Armar la cohorte no cierra ni modifica beads.

## Procedimiento

1. **Mismo estado de repo:** checkout del repo en el `base_commit` de la tarea; árbol limpio, sin cambios locales.
2. **Mismo harness y versión:** un solo runner y una versión fijada para toda la corrida (se registra en el reporte); mismas flags, mismo presupuesto de contexto y tiempo para todos los modelos.
3. **Orden aleatorizado:** el orden tarea×modelo se baraja (semilla registrada) antes de correr; ningún modelo corre todas las tareas primero.
4. **Evaluación a ciegas:** quien evalúa ve el diff y la salida de la acceptance sin saber qué modelo la produjo.
5. **Veredicto:** por tarea, `aceptado` / `rechazado`, más coste (USD y tokens) y latencia medidos por el harness.

## Definiciones

- **measured:** sale directamente del harness o del sistema, sin interpretación (tokens, coste USD, latencia, exit code del comando de acceptance).
- **estimated:** derivado por una regla declarada cuando no hay medición (p. ej. coste a precio de lista por falta de `usage.cost`, prorrata de suscripciones); lleva marca `estimated`/`assumed` en el reporte.
- **judged:** decidido por el evaluador humano a ciegas (aceptación de criterios escritos, calidad del diff); un solo evaluador por tarea para toda la cohorte.
- **"aceptado":** los criterios de acceptance de la tarea se cumplen, la suite de tests del repo queda verde, y no requirió corrección humana sustancial (más que ajustes triviales de formato o nombres). Todo lo demás es rechazado, con `close_reason` breve.

## Riesgos declarados

- **Contaminación por repo conocido:** los repos son públicos/locales del autor y pueden estar en el entrenamiento de los modelos o en sesiones previas del autor. Se declara, no se corrige; la referencia frontier acota el sesgo.
- **Tareas que cruzan beads:** algunas tareas comparten archivos con otras (marcadas en `notes`/prompt). Las corridas son independientes por checkout; ninguna tarea depende del resultado de otra.
- **Sesgo de selección:** las tareas son las que el autor consideró vale la pena beadar. Se mitiga publicando el criterio de selección (cuadrícula) y el muestreo, no ocultándolo.
