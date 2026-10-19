# add-ddl-effectiveness-analysis — Design

## Decisiones

### D1. Capability separada, reporte de usage intacto

`ddl-effect-tracker.py` **consume** tres familias de insumos pero emite su propio
JSON con su propio schema:

1. `data/usage_report_v3.json` (sesiones agregadas, `n_errors`, project↔cwd);
2. **walk de `~/.pi/agent/sessions`** para `recovery_p50_interactions` — el
   reporte de usage solo guarda agregados por sesión (verificado: `sessions` =
   `total_sessions`, `length_distribution`, `total_api_errors`, … sin filas por
   evento), y la recuperación necesita la secuencia error→invocaciones por
   evento. Mismo patrón de walk que el `add-context-dashboard`
   (grupo `extract_pi_sessions`, coffe-i31); corre tras usage-tracker en el
   nightly, con duración medida;
3. fuentes externas: git de la flota, gh api (cache), `ddl status`, cache de
   terapia (clases de error — no secuencias).

Razones de no-meterlo en el reporte de usage:
(1) las fuentes son de naturaleza distinta (git, gh api, ddl status, terapia) y
mezclarlas en el reporte de usage rompería el contrato `usage-report` por datos
que no son de usage; (2) el golden de `usage_report_v3` es grande y frágil — otro
campo ahí obliga a regenerar y revisar un diff de MBs por cada ajuste de umbral;
(3) frecuencia de refresh distinta: la capa git/CI cambia con cada push, la de
usage con cada sesión.

### D2. Forma del dato: panel repo × mes

`data/ddl_effect.json`:

```jsonc
{
  "metadata": { "generated_at", "seed_bootstrap", "fleet_definition",
                "managed_tools": [...13...], "provenance": {...} },
  "repos": [ { "repo", "is_control", "adoption": { "first_ddl_commit",
              "tools_first_use": {"wai": "...", ...}, "dose": 0.0-1.0,
              "dose_detail": {"present": [...], "healthy": [...], "missing": [...]} } } ],
  "monthly": [ { "repo", "month", "sessions", "interactions",
                 "errors_api_per_100", "recovery_p50_interactions",
                 "session_commit_latency_p50_h", "ci_fail_rate",
                 "revert_ratio", "fix_ratio", "test_commit_ratio",
                 "ah_check_findings", "dose",
                 "src": { "*": "reported|derived|cached|missing" } } ],
  "analysis": { "its": [...], "did": [...], "dose_response": [...] }
}
```

Panel balanceado *no* requerido: meses sin señal para un repo quedan afuera del
array (no nulls-fantasma), y `metadata.provenance.fleet` lista qué repos entraron
y por qué (actividad en sesiones in-scope).

### D3. Medición de dosis — tres fuentes, jerarquía explícita

1. **`ddl status`** (si el binario está instalado): verdad presente/sana por repo.
2. **Git:** primer commit que introduce `.ddl/` (fecha de adopción) y presencia
   de archivos-canario por herramienta bajo `.ddl/`.
3. **Sesiones:** primer uso de cada binario gestionado con cwd del repo
   (fallback cuando no hay `.ddl/`, ej. repos que usan wai sin migrar).

La dosis reportada es la fracción *sana*/13. Dosis 0 ≠ control: un repo puede
estar en el grupo de tratamiento con dosis 0 (adoptó y migró a 0 herramientas
sanas). `is_control` se define por **ausencia de `.ddl/` + topic GitHub**, no por
dosis (offline el test estructural de `.ddl/` es el autoritativo; el topic GitHub
es informativo y sale del cache de gh).

**D3b — dosis como serie temporal:** `ddl status` solo describe el presente, pero
el panel es repo × mes: `dose(month)` se deriva hacia atrás desde
`tools_first_use` + presencia histórica de canarios (git log de `.ddl/`).
`ddl status` valida la derivación contra el snapshot corriente; si difiere, gana
`ddl status` y el delta se reporta en metadata (visible, nunca silencioso).

### D4. Lazo de feedback — definiciones operativas

- **Recuperación:** dentro de una sesión Pi, para cada evento de error de tool o
  API, contar interacciones hasta la siguiente invocación *exitosa* del mismo
  tool (o fin de sesión → censurado, se reporta `recovery_censored_rate`).
  p50 por repo × mes sobre eventos no censurados, con n visible.
- **Sesión→commit:** latencia entre el último timestamp de sesión con cwd del
  repo y el timestamp del primer commit posterior cuyo mensaje matchea patrones
  de trabajo asistido (heurística documentada, bias declarado: sobrestima si hay
  trabajo manual entre medio).
- **Fallo de CI:** runs fallidos / runs totales por mes (`gh api
  /repos/{owner}/{repo}/actions/runs`, paginado, filtrando runs de PRs de dependabot
  para no mezclar señal con ruido de bots). Cache incremental:
  `data/cache/gh_runs/<repo>.json` guarda `runs` + `fetched_until`; refetch solo
  desde ahí. Sin gh/red → `null` con `src.ci = "missing"`.

### D5. Estimadores — honestos antes que elegantes

- **ITS:** media móvil trimestral pre/post con intervalo bootstrap (10k resamples,
  seed en `metadata.seed_bootstrap`, fija para reproducibilidad). **Ventana pre
  mínima: 3 meses** — repos cuya adopción precede al tracking salen del ITS con
  marca explícita, no con delta vacío. Sin regresión
  formal: n de repos es ~decenas, los IC bootstrap son más honestos que p-values.
- **DiD:** estimador simple de 2 períodos × 2 grupos por métrica, con el delta
  pre→post del grupo control restando tendencia global. Reportado con el n de
  cada celda; celdas con n < 3 no se grafican (regla dura, anti-anécdota).
- **Dosis-respuesta:** Spearman dosis × outcome por métrica, con scatter y n por
  punto. No ajuste multivariable en v1 (n chico, overfitting seguro); la guía lo
  declara.

### D6. Calidad — cuatro proxies, cada uno con su bias declarado

| Proxy | Definición | Bias conocido |
|---|---|---|
| Revert ratio | `git revert`+rebases perdidos / commits | subestima (no todo revert usa `git revert`) |
| Fix ratio | commits `fix:` post-release / releases | convención de commits requerida |
| Test-commit ratio | `test:`+archivos de test / commits | repos sin convención → 0 |
| `ah check` findings | findings por corrida (serie temporal desde cache local de ah) | solo mide specs contratadas, no todo el repo |

Denominador 0 en un mes (sin commits, sin releases): ratio → `null` con razón
(patrón "missing", no 0 fake); el dashboard lo distingue de un ratio real 0.

Los 4 van al dashboard lado a lado, nunca agregados en un "score de calidad"
compuesto (ponderarlos sería inventar pesos sin base).

### D7. Provenance — mismo estándar que fpa-dashboard

Toda cifra lleva tag: *reported* (fuente primaria), *derived* (calculada de otra
del JSON), *cached* (gh api cacheado con fecha), *missing* (null con razón).
Los meses parciales se marcan como en fpa. `ddl status` no disponible → dosis
por fallback git/sesiones con tag que lo declare (la dosis cambia de fuente, no
de significado silenciosamente).

### D8. Confusores — disclosure estructural, no exculpatoria

El dashboard reserva una banda fija (no colapsable a nil) con: madurez del repo
(edad, commits totales), mezcla de modelos del período (del reporte de usage),
y el confusor maestro en letra chica grande: *"repos con ddl son repos que
Sasha cuida más — el efecto medido incluye ese cuidado"*. Los estimadores no
intentan controlar eso; el texto sí.

## Riesgos

- **gh api sin token en el nightly:** los user-timers no cargan `gh` auth de la
  sesión → el tracker lee `GH_TOKEN` de env o de `gh auth token`, y si no hay,
  emite missing sin romper (CI pasa a provenance *missing*, el dashboard muestra
  el hueco).
- **Repo renombrado/eliminado en GitHub:** 404 → conservar cache stale y emitir
  provenance *missing* con razón (`repo_gone`); nunca refetch vacío ni 0.
- **Sesgo de reloj sesión↔commit:** latencias negativas (commit "antes" de la
  sesión por skew) se clampean a 0 y se cuentan en
  `metadata.clamped_negative_latencies` (bias visible).
- **Dosis con varianza ~0 en corte transversal:** si casi toda la flota viva tiene
  dosis ~1 (ddl init instala todo), la dosis-respuesta transversal es degenerada →
  la vista degrada explícitamente a tratamiento-vs-control (variable en el tiempo,
  no en el corte), declarado en la vista.
- **cwd de sesiones ≠ nombre de repo GitHub:** mapping explícito en config
  (`config/fpa.json` → `ddl_effect.repo_aliases`) en lugar de fuzzy matching.
- **Terapia cache frío:** primera corrida del análisis dispara `terapia` sobre
  todas las sesiones (lento); el tracker lo corre una vez y cachea en
  `data/cache/terapia_errors.json` con TTL semanal (mismo patrón que gh).
