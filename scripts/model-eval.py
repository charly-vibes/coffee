#!/usr/bin/env python3
"""
model-eval.py — runner del experimento de modelos (coffe-dnx.7).

Purpose: ejecutar y registrar corridas del experimento definido por el
protocolo coffe-dnx.9 (docs/model-eval-protocol.md + config/model_eval.json):
por corrida, commit base, harness+versión, modelo, coste, latencia, tests
ejecutados y resultado, intervenciones humanas y veredicto →
data/model_eval.json; y agregar esas corridas en un reporte por modelo y
clase de tarea.

Responsibilities:
- run(): registra una corrida task×modelo con todos los campos del
  protocolo; una corrida SIN veredicto se guarda igual pero NO entra en
  las tasas de aceptación (nunca se asume aceptada).
- provenance: cada cifra lleva `measured | estimated | judged` — measured
  sale del harness, estimated de una regla declarada, judged del evaluador
  humano a ciegas (definiciones del protocolo .9).
- blind: si el evaluador declara --judged-by, el ciego quedó roto; el
  reporte lo declara con `judged_by`.
- incomplete: si la cohorte no tiene corrida con veredicto para todas las
  tareas, el reporte declara `incomplete: true` y NO promedia entre modelos.
- bead: cada corrida crea el bead `eval-<slug>` UNA sola vez (idempotente
  en re-ejecución), vía subprocess a `bd`, para que coffe-dnx.5 atribuya
  coste. Este script no crea beads por sí solo: solo cuando corre `run`.
- report: agrega por modelo y clase (bugfix|feature|docs|tests) con
  provenance en cada cifra; `--validate` auto-chequea el reporte.

Rationale: runner separado del protocolo (SCOPE-004): este script NO define
tareas ni umbrales, solo ejecuta lo declarado en .9. Es fuente independiente
de usage-tracker.py y agent-metrics-tracker.py; deliberadamente NO reutiliza
session_spend para puntuar aceptación (mediría lo mismo que ya medimos).
Stdlib-only, mismo estilo que scripts/model-cost-estimate-v0.py.

Uso:
  python3 scripts/model-eval.py run --task m-e-01 --model glm-5.3-flash \
      --cost-usd 0.42 --latency-s 310 --tests-cmd "pytest -q" --tests-exit 0 \
      --verdict accepted
  python3 scripts/model-eval.py report
  python3 scripts/model-eval.py report --validate
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "model_eval.json"
DATA_PATH = REPO_ROOT / "data" / "model_eval.json"

HARNESS_NAME = "model-eval.py"
HARNESS_VERSION = "1"

CLASSES = ("bugfix", "feature", "docs", "tests")

# Cargamos model_eval_report.py dinámicamente: scripts/ no es paquete y este
# runner corre standalone (los tests también lo cargan vía spec_from_file_location).
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "model_eval_report", Path(__file__).resolve().parent / "model_eval_report.py")
_report_mod = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(_report_mod)
figure, _mean_cost = _report_mod.figure, _report_mod._mean_cost
aggregate, validate_report = _report_mod.aggregate, _report_mod.validate_report


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_data():
    data_path = Path(DATA_PATH)
    if not data_path.exists():
        return {"schema": 1, "harness": {"name": HARNESS_NAME, "version": HARNESS_VERSION}, "runs": []}
    with open(data_path, encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    data_path = Path(DATA_PATH)
    data_path.parent.mkdir(parents=True, exist_ok=True)
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")


def bead_slug(task_id, model):
    slug = f"{task_id}-{model}".lower()
    slug = re.sub(r"[^a-z0-9.]+", "-", slug).strip("-")
    return f"eval-{slug}"


def _bd(args, dry_run=False):
    """Llama a `bd` como subprocess (igual que el resto del tooling del repo)."""
    cmd = ["bd"] + args
    if dry_run:
        print(f"[dry-run bd] {' '.join(cmd)}")
        return 0, ""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def ensure_bead(slug, task_id, model, dry_run=False):
    """Crea el bead eval-<slug> exactamente una vez (idempotente).

    Si `bd show` lo encuentra, no vuelve a crearlo; si falla la consulta,
    intenta crear y tolera "ya existe" (duplicado) como éxito.
    """
    rc, out = _bd(["show", slug], dry_run=dry_run)
    if rc == 0:
        return slug, False  # ya existía: no se crea de nuevo
    rc, out = _bd(["create", slug, f"eval: {task_id} × {model}"], dry_run=dry_run)
    if rc != 0 and "exist" not in out.lower():
        print(f"warn: bd create {slug} falló: {out}", file=sys.stderr)
    return slug, True


def _cohort_names(config):
    return [c["name"] if isinstance(c, dict) else c for c in config.get("cohort", [])]


def _lookup_task(config, task_id):
    return next((t for t in config["tasks"] if t["id"] == task_id), None)


def _run_args_error(args, config):
    """Mensaje de error si task/model no están en el protocolo; None si ok."""
    if _lookup_task(config, args.task) is None:
        return f"error: tarea {args.task} no está en {CONFIG_PATH}"
    cohort = _cohort_names(config)
    if cohort and args.model not in cohort:
        return f"error: modelo {args.model} no está en la cohorte {cohort}"
    return None


def _run_dict(args, task, slug):
    """Campos de una corrida según el protocolo (coffe-dnx.9)."""
    provenance = args.cost_provenance if args.cost_usd is not None else None
    return {
        "run_id": f"{args.task}/{args.model}",
        "task": args.task,
        "model": args.model,
        "task_class": task["class"],
        "repo": task["repo"],
        "base_commit": task["base_commit"],
        "harness": {"name": HARNESS_NAME, "version": HARNESS_VERSION},
        "cost_usd": figure(args.cost_usd, provenance) if args.cost_usd is not None else None,
        "tokens": figure(args.tokens, "measured") if args.tokens is not None else None,
        "latency_s": figure(args.latency_s, "measured") if args.latency_s is not None else None,
        "tests_executed": {
            "command": args.tests_cmd,
            "exit_code": args.tests_exit,
            "provenance": "measured",
        } if args.tests_cmd is not None else None,
        "human_interventions": figure(args.human_interventions, "measured"),
        "verdict": args.verdict,  # accepted | rejected | None (sin veredicto)
        "close_reason": args.close_reason,
        "judged_by": args.judged_by,  # presente ⇔ el ciego quedó roto
        "bead": slug,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }


def _upsert_run(data, run, existing, created):
    """Reemplaza la corrida existente (conservando bead_created) o la agrega."""
    if existing:
        run["bead_created"] = existing.get("bead_created", False)
        data["runs"][data["runs"].index(existing)] = run
    else:
        run["bead_created"] = created
        data["runs"].append(run)


def cmd_run(args):
    config = load_config()
    error = _run_args_error(args, config)
    if error:
        print(error, file=sys.stderr)
        return 2
    task = _lookup_task(config, args.task)

    data = load_data()
    run_id = f"{args.task}/{args.model}"
    existing = next((r for r in data["runs"] if r["run_id"] == run_id), None)

    slug = bead_slug(args.task, args.model)
    created = False
    if existing is None or not existing.get("bead"):
        slug, created = ensure_bead(slug, args.task, args.model, dry_run=args.dry_run_bd)

    run = _run_dict(args, task, slug)
    _upsert_run(data, run, existing, created)
    save_data(data)
    print(json.dumps({"run_id": run_id, "bead": slug, "bead_created": run["bead_created"],
                      "verdict": run["verdict"]}, ensure_ascii=False))
    return 0


def _model_report(model_runs, task_ids):
    """Tareas de la cohorte sin veredicto para un modelo + agregación por clase."""
    judged_tasks = {r["task"] for r in model_runs
                    if r.get("verdict") in ("accepted", "rejected")}
    return {
        "incomplete": sorted(task_ids - judged_tasks),
        "by_class": aggregate(model_runs, None),
    }


def _cohort_complete(cohort, task_ids, runs):
    """Cohorte completa ⇔ cada modelo de la cohorte tiene veredicto en cada tarea."""
    if not cohort:
        return False
    covered = {(r["model"], r["task"]) for r in runs
               if r.get("verdict") in ("accepted", "rejected")}
    return all((m, t) in covered for m in cohort for t in task_ids)


def _cohort_average(runs):
    """Solo con cohorte completa se promedia entre modelos."""
    all_judged = [r for r in runs if r.get("verdict") in ("accepted", "rejected")]
    accepted = [r for r in all_judged if r["verdict"] == "accepted"]
    return {
        "acceptance_rate": figure(len(accepted) / len(all_judged) if all_judged else None,
                                  "judged"),
    }


def build_report():
    config = load_config()
    data = load_data()
    cohort = _cohort_names(config)
    task_ids = {t["id"] for t in config["tasks"]}
    runs = [r for r in data["runs"] if r["task"] in task_ids]
    models_report = {
        model: _model_report([r for r in runs if r["model"] == model], task_ids)
        for model in sorted({r["model"] for r in runs})
    }
    report = {
        "schema": 1,
        "harness": {"name": HARNESS_NAME, "version": figure(HARNESS_VERSION, "measured")},
        "cohort": figure(sorted(cohort), "measured"),
        "incomplete": figure(not _cohort_complete(cohort, task_ids, runs), "measured"),
        "models": models_report,
    }
    if _cohort_complete(cohort, task_ids, runs):
        report["cohort_average"] = _cohort_average(runs)
    return report


def cmd_report(args):
    report = build_report()
    if args.validate:
        errors = validate_report(report)
        if errors:
            for e in errors:
                print(f"INVALID: {e}", file=sys.stderr)
            return 1
        print("valid: reporte con provenance en cada cifra e incomplete declarado")
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="registrar una corrida task×modelo")
    p_run.add_argument("--task", required=True)
    p_run.add_argument("--model", required=True)
    p_run.add_argument("--cost-usd", type=float)
    p_run.add_argument("--cost-provenance", choices=("measured", "estimated"), default="measured")
    p_run.add_argument("--tokens", type=int)
    p_run.add_argument("--latency-s", type=float)
    p_run.add_argument("--tests-cmd")
    p_run.add_argument("--tests-exit", type=int)
    p_run.add_argument("--human-interventions", type=int, default=0)
    p_run.add_argument("--verdict", choices=("accepted", "rejected"))
    p_run.add_argument("--close-reason")
    p_run.add_argument("--judged-by", help="declara que el ciego se rompió y quién evaluó")
    p_run.add_argument("--dry-run-bd", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_rep = sub.add_parser("report", help="agregar corridas por modelo y clase")
    p_rep.add_argument("--validate", action="store_true")
    p_rep.set_defaults(func=cmd_report)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
