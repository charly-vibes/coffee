# Tasks: fix-session-accounting

## 1. omp anidado

- [ ] 1.1 Test de `_omp_scan`/`extract_omp` con un fixture
      `<cwd>/<parent>/Subagent.jsonl` y un archivo directo: las llamadas del
      subagente entran una vez y `agent_semantics` declara la señal.
- [ ] 1.2 Test de solape padre/hijo: una llamada idéntica en ambos archivos
      no se cuenta dos veces.
- [ ] 1.3 Implementar el walk anidado y el dedupe por id nativo/responseId.
- [ ] 1.4 Emitir `sessions.subagents` y corregir `sessions.agent_semantics`.

## 2. Identidad de sesión y dedupe

- [ ] 2.1 Test Amp: 4 archivos con el mismo timestamp en un task ⇒ 4
      interacciones (hoy se descartan 3).
- [ ] 2.2 Test Pi: dos llamadas distintas al mismo ms ⇒ 2 filas; dos archivos
      fork con el mismo mensaje ⇒ 1 fila.
- [ ] 2.3 Implementar la clave de dedupe con `source` + identidad de sesión +
      hash de uso.
- [ ] 2.4 Test de regresión de los 13 duplicados Claude actuales.

## 3. Forks de Pi en contexto

- [ ] 3.1 Fixture de dos archivos Pi con 78 turnos copiados y 15 nuevos:
      `fork_of` se emite y `monthly_model` suma 93 turnos, no 171.
- [ ] 3.2 Implementar la detección de prefijo y `meta.forks_unresolved`.
- [ ] 3.3 Test de sesión sin fork: se cuenta completa.

## 4. session_spend por sesión

- [ ] 4.1 Test de hilo Amp que toca 4 repos: una fila, `project` dominante y
      `projects` con los 4 desgloses.
- [ ] 4.2 Actualizar `_session_spend` para agrupar por sesión nativa.
- [ ] 4.3 Emitir `source` (harness), `provider` y `plan`; migrar consumidores.

## 5. Contrato y regeneración

- [ ] 5.1 Actualizar `specs/usage-report-v3.schema.json` (campos nuevos,
      `session_spend` por sesión, `sessions.subagents`).
- [ ] 5.2 Regenerar goldens con `REGEN_GOLDEN=1`; revisar que el diff sea
      solo aditivo/semántico esperado.
- [ ] 5.3 Regenerar `data/usage_report_v3.json` y verificar +5.3k omp y
      dedupe Amp/Pi.
- [ ] 5.4 Actualizar README (semántica de subagentes, forks, session_spend).
- [ ] 5.5 Suite: `python3 -m pytest tests/test_tracker_omp.py
      tests/test_tracker_sessions.py tests/test_tracker_context.py
      tests/test_tracker.py` y `tests/test_viz.py`.