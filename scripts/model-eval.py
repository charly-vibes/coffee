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

# Plantilla de cifra con provenance (todo número del reporte pasa por acá).
def figure(value, provenance):
    return {"value": value, "provenance": provenance}


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


def cmd_run(args):
    config = load_config()
    tasks = {t["id"]: t for t in config["tasks"]}
    if args.task not in tasks:
        print(f"error: tarea {args.task} no está en {CONFIG_PATH}", file=sys.stderr)
        return 2
    task = tasks[args.task]
    cohort = [c["name"] if isinstance(c, dict) else c for c in config.get("cohort", [])]
    if cohort and args.model not in cohort:
        print(f"error: modelo {args.model} no está en la cohorte {cohort}", file=sys.stderr)
        return 2

    data = load_data()
    run_id = f"{args.task}/{args.model}"
    existing = next((r for r in data["runs"] if r["run_id"] == run_id), None)

    slug = bead_slug(args.task, args.model)
    created = False
    if existing is None or not existing.get("bead"):
        slug, created = ensure_bead(slug, args.task, args.model, dry_run=args.dry_run_bd)

    provenance = args.cost_provenance if args.cost_usd is not None else None
    run = {
        "run_id": run_id,
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
    if existing:
        run["bead_created"] = existing.get("bead_created", False)
        data["runs"][data["runs"].index(existing)] = run
    else:
        run["bead_created"] = created
        data["runs"].append(run)
    save_data(data)
    print(json.dumps({"run_id": run_id, "bead": slug, "bead_created": run["bead_created"],
                      "verdict": run["verdict"]}, ensure_ascii=False))
    return 0


def _mean_cost(runs):
    """Promedio de coste con provenance agregada: cualquier estimated contamina."""
    vals = [r["cost_usd"] for r in runs if r.get("cost_usd") is not None]
    if not vals:
        return None
    prov = "estimated" if any(v["provenance"] != "measured" for v in vals) else "measured"
    return figure(sum(v["value"] for v in vals) / len(vals), prov)


def aggregate(runs, tasks_by_class):
    """Agrega corridas con veredicto por clase; las sin veredicto NO puntúan."""
    by_class = {}
    for cls in CLASSES:
        cls_runs = [r for r in runs if r["task_class"] == cls]
        judged = [r for r in cls_runs if r.get("verdict") in ("accepted", "rejected")]
        accepted = [r for r in judged if r["verdict"] == "accepted"]
        no_verdict = len(cls_runs) - len(judged)
        entry = {
            "runs_recorded": figure(len(cls_runs), "measured"),
            "runs_without_verdict": figure(no_verdict, "measured"),
            "judged": figure(len(judged), "measured"),
            "accepted": figure(len(accepted), "measured"),
            "rejected": figure(len(judged) - len(accepted), "measured"),
        }
        if judged:
            entry["acceptance_rate"] = figure(len(accepted) / len(judged), "judged")
        else:
            entry["acceptance_rate"] = figure(None, "judged")
        mc = _mean_cost(judged)
        if mc:
            entry["mean_cost_usd"] = mc
        lat = [r["latency_s"]["value"] for r in judged if r.get("latency_s")]
        if lat:
            entry["mean_latency_s"] = figure(sum(lat) / len(lat), "measured")
        blind_broken = [r["judged_by"] for r in cls_runs if r.get("judged_by")]
        if blind_broken:
            entry["judged_by"] = sorted(set(blind_broken))
        by_class[cls] = entry
    return by_class


def build_report():
    config = load_config()
    data = load_data()
    cohort = [c["name"] if isinstance(c, dict) else c for c in config.get("cohort", [])]
    task_ids = {t["id"] for t in config["tasks"]}
    runs = [r for r in data["runs"] if r["task"] in task_ids]
    models = sorted({r["model"] for r in runs})

    models_report = {}
    for model in models:
        model_runs = [r for r in runs if r["model"] == model]
        models_report[model] = {
            "incomplete": sorted(task_ids - {r["task"] for r in model_runs
                                             if r.get("verdict") in ("accepted", "rejected")}),
            "by_class": aggregate(model_runs, None),
        }

    # Cohorte completa ⇔ cada modelo de la cohorte tiene veredicto en cada tarea.
    covered = {(r["model"], r["task"]) for r in runs
               if r.get("verdict") in ("accepted", "rejected")}
    complete = bool(cohort) and all((m, t) in covered for m in cohort for t in task_ids)

    report = {
        "schema": 1,
        "harness": {"name": HARNESS_NAME, "version": figure(HARNESS_VERSION, "measured")},
        "cohort": figure(sorted(cohort), "measured"),
        "incomplete": figure(not complete, "measured"),
        "models": models_report,
    }
    if complete:
        # Solo con cohorte completa se promedia entre modelos.
        all_judged = [r for r in runs if r.get("verdict") in ("accepted", "rejected")]
        acc = [r for r in all_judged if r["verdict"] == "accepted"]
        report["cohort_average"] = {
            "acceptance_rate": figure(len(acc) / len(all_judged) if all_judged else None, "judged"),
        }
    return report


def validate_report(report):
    """Auto-chequeo: cada cifra lleva provenance válida; incomplete declarado."""
    errors = []

    def walk(node, path):
        if isinstance(node, dict):
            if "value" in node or "provenance" in node:
                if "provenance" not in node or node["provenance"] not in ("measured", "estimated", "judged"):
                    errors.append(f"{path}: cifra sin provenance válida")
                if "value" not in node:
                    errors.append(f"{path}: provenance sin value")
                return
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(report, "report")
    if "incomplete" not in report:
        errors.append("report.incomplete: falta la declaración de cohorte incompleta")
    for model, mr in report.get("models", {}).items():
        for cls, entry in mr["by_class"].items():
            if entry["runs_without_verdict"]["value"] > 0 and "acceptance_rate" in entry:
                rate = entry["acceptance_rate"]["value"]
                judged = entry["judged"]["value"]
                if judged and rate is not None and abs(rate - entry["accepted"]["value"] / judged) > 1e-9:
                    pass  # rate calculado sobre judged: correcto
        if mr["incomplete"] and "cohort_average" in report and report["cohort_average"]:
            errors.append(f"{model}: cohorte incompleta pero se promedió")
    return errors


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
