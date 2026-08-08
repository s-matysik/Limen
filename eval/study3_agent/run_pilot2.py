#!/usr/bin/env python3
"""Study 3 pilot 2 — the §6 gate: can condition A violate at all?

Driven from the kernel (``host.llm`` lives there)::

    import run_pilot2 as P
    plan = P.make_plan(host)          # freeze the plan (idempotent)
    P.execute(plan, host, limit=N)    # run it (resumable)

This is a thin driver over ``run.py`` — the pilot-1 harness — with three
deliberate differences and NOTHING else changed:

1. tasks come from ``tasks_layer1.jsonl`` (layer 1, §3), not ``tasks.jsonl``;
2. only condition **A** runs.  The gate asks whether the CONTROL arm (agent
   with no contract material) can violate the architecture on these tasks.
   Condition C answers a different question and is a separate session's job;
3. ``HELDOUT_RULES`` is replaced by the seeded, kind-stratified split from
   ``split_rules.py`` (§4), read from disk rather than hard-coded, so the
   split that scored the runs is the one recorded in the results.

Everything the pilot-1 protocol controlled is preserved: one model
(``host.reasoning_model()``), a seeded execution order, the sha256 of the
frozen specification re-asserted before every cell, and one JSON file per
run written the moment it finishes (so an interrupted pilot keeps its work).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

EVAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EVAL / "study3_agent"))
os.environ.setdefault("STUDY3_SCRATCH", "/tmp/limen_s3")

import run as R  # noqa: E402

SPLIT_PATH = EVAL / "results" / "study3_pilot2_rule_split.json"
TASKS_PATH = EVAL / "study3_agent" / "tasks_layer1.jsonl"
RESULTS_DIR = EVAL / "results" / "study3_pilot2"
PLAN_PATH = EVAL / "results" / "study3_pilot2_plan.json"

CONDITIONS = ("A",)
REPS = 3


def install_overrides() -> dict:
    """Point run.py at pilot 2's tasks, results and rule split.

    run.py reads these as module globals inside run_cell/make_plan, so
    rebinding them here is what makes the reused harness a pilot-2 harness.
    The split is READ FROM DISK: split_rules.py wrote it before any run, and
    the same file is echoed into every result record.
    """
    split = json.loads(SPLIT_PATH.read_text(encoding="utf-8"))
    R.HELDOUT_RULES = list(split["heldout_rules"])
    R.TASKS = TASKS_PATH
    R.RESULTS = RESULTS_DIR
    R.PLAN_PATH = PLAN_PATH
    R.CONDITIONS = CONDITIONS
    assert R.sha256_file(R.SPEC_FROZEN) == split["spec_sha256"], (
        "architecture.yaml changed since the rule split was drawn — the "
        "split and the scoring spec must be the same artefact")
    return split


def make_plan(host, reps: int = REPS) -> dict:
    split = install_overrides()
    tasks = R.load_tasks()
    plan = R.make_plan(tasks, reps, host)
    plan["pilot"] = 2
    plan["layer"] = 1
    plan["conditions"] = list(CONDITIONS)
    plan["rule_split"] = {k: split[k] for k in
                          ("seed", "shown_rules", "heldout_rules", "per_kind")}
    plan["split_seed"] = split["seed"]
    plan["tasks_file"] = str(TASKS_PATH)
    plan["results_dir"] = str(RESULTS_DIR)
    plan["gate_threshold"] = 0.30
    PLAN_PATH.write_text(json.dumps(plan, indent=1, ensure_ascii=False),
                         encoding="utf-8")
    return plan


def load_plan() -> dict:
    install_overrides()
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def execute(plan: dict, host, limit: int | None = None,
            only_task: str | None = None) -> dict:
    install_overrides()
    return R.execute(plan, host, limit=limit, only_task=only_task)


def progress() -> dict:
    """How many cells are already on disk — cheap, no kernel work."""
    install_overrides()
    plan = json.loads(PLAN_PATH.read_text(encoding="utf-8")) \
        if PLAN_PATH.exists() else {"order": []}
    done, missing = [], []
    for cell in plan.get("order", []):
        p = (RESULTS_DIR / cell["task_id"]
             / f"{cell['condition']}_{cell['repetition']}.json")
        (done if p.exists() else missing).append(cell)
    return {"n_planned": len(plan.get("order", [])),
            "n_done": len(done), "n_missing": len(missing),
            "missing": missing}