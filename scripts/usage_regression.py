#!/usr/bin/env python3
"""Guard de regresiones mensuales (extraído de usage-tracker.py).

Parte de la descomposición de scripts/usage-tracker.py (coffe-kav.1):
cada módulo conserva el código original verbatim; los nombres compartidos
viajan por _link() (mismo patrón que viz_fpa_common) y el estado mutable
por usage_state.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import usage_state
from usage_state import DEFAULT_MONTHLY_DROP_PCT  # noqa: F401  (default arg, antes de _link)

def _regression_new_counts(new_rows):
    new_claude = Counter()
    for r in new_rows:
        if r.get("tool") == "claude-cli":
            new_claude[r["timestamp"][:7]] += 1
    return new_claude


def _regression_old_count(bucket):
    try:
        return bucket["tools"]["claude-cli"]["interactions"] or 0
    except (KeyError, TypeError):
        return None  # sin datos claude-cli comparables en ese mes


def _regression_month(bucket, new_claude, mes, threshold_pct):
    old_n = _regression_old_count(bucket)
    if not old_n:
        return None
    new_n = new_claude.get(mes, 0)
    if new_n >= old_n:
        return None
    drop_pct = (old_n - new_n) / old_n * 100.0
    if drop_pct <= threshold_pct:
        return None
    return {
        "mes": mes, "old": old_n, "new": new_n,
        "drop_pct": round(drop_pct, 1),
    }


def detect_monthly_regressions(new_rows, old_report, threshold_pct=DEFAULT_MONTHLY_DROP_PCT):
    """coffe-0c8: compara interacciones claude-cli por mes entre la extracción
    nueva y el reporte vigente. Devuelve una lista de dicts
    {mes, old, new, drop_pct} para los meses cuya caída supere threshold_pct.

    Scope: SOLO claude-cli — es la fuente frágil (dashboard-cache.json puede
    ser truncado por Claude Code, coffe-jg3); las demás tools tienen logs
    on-disk estables. El mes en curso se ignora (crece mes a mes, una "caída"
    a mitad de mes es ruido de reloj, no poda). Meses con old==0 o ausentes
    del reporte viejo no tienen contra qué comparar: nunca regresan.

    Shape del reporte viejo: monthly[mes]["tools"]["claude-cli"]["interactions"]
    (lo que produce aggregate(); reportes previos a esa estructura se tratan
    como sin-datos y no disparan el guard).
    """
    if not old_report:
        return []
    old_monthly = old_report.get("monthly") or {}
    if not isinstance(old_monthly, dict):
        return []

    new_claude = _regression_new_counts(new_rows)
    mes_actual = datetime.now().strftime("%Y-%m")
    regressions = []
    for mes, bucket in sorted(old_monthly.items()):
        if mes == mes_actual:
            continue
        regression = _regression_month(bucket, new_claude, mes, threshold_pct)
        if regression:
            regressions.append(regression)
    return regressions


def check_monthly_regressions(new_rows, out_path, force=False):
    """coffe-0c8: wiring del guard en main(). Lee el reporte vigente en
    out_path (si existe), corre detect_monthly_regressions y aborta con
    SystemExit(1) si hay caídas — fail-loud ANTES de pisar el dataset.
    --force lo salta explícitamente. Umbral vía TRACKER_MONTHLY_DROP_PCT.
    """
    try:
        threshold = float(os.environ.get(
            "TRACKER_MONTHLY_DROP_PCT", DEFAULT_MONTHLY_DROP_PCT))
    except ValueError:
        print(f"ADVERTENCIA: TRACKER_MONTHLY_DROP_PCT no numérico, "
              f"usando {DEFAULT_MONTHLY_DROP_PCT}%", file=sys.stderr)
        threshold = DEFAULT_MONTHLY_DROP_PCT

    old_report = None
    out_path = Path(out_path)
    if out_path.exists():
        try:
            old_report = json.loads(out_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"ADVERTENCIA: no se pudo leer el reporte vigente "
                  f"({out_path}): {e} — guard de regresión mensual desactivado",
                  file=sys.stderr)
            return

    regressions = detect_monthly_regressions(new_rows, old_report, threshold)
    if not regressions:
        return
    if force:
        print("\n--force: regresión mensual claude-cli detectada pero se "
              "escribe de todas formas:", flush=True)
        for reg in regressions:
            print(f"  {reg['mes']}: {reg['old']:,} → {reg['new']:,} "
                  f"(-{reg['drop_pct']}%)", flush=True)
        return

    print(
        "\nABORTADO (coffe-0c8): las interacciones claude-cli de uno o más "
        "meses cayeron vs el reporte vigente:",
        flush=True)
    for reg in regressions:
        print(f"  {reg['mes']}: {reg['old']:,} → {reg['new']:,} "
              f"(-{reg['drop_pct']}%)", flush=True)
    print(
        "\nCausa probable: Claude Code perdió el detalle histórico de "
        "claude-cli en la fuente:"
        "\ntruncó los turns viejos de ~/.claude/dashboard-cache.json "
        "(coffe-jg3) y los\njsonl ya no están en disco. Verificado "
        "2026-11-11: el cache vivo, el backup manual\nde ~/.claude/backups/ "
        "(POST-recorte, pese a su nombre) y todos los snapshots de\n"
        "~/.claude/cache-snapshots/ son byte-idénticos — no hay copia "
        "pre-recorte\nque restaurar."
        "\n\nSi la caída es legítima y la pérdida de fuente está "
        "confirmada, usa --force\npara escribir de todas formas y documenta "
        "la caída en el README (Nota de vigencia).",
        flush=True)
    sys.exit(1)

usage_state._link(globals())
