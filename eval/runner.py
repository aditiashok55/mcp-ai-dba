"""Eval harness CLI.

    python -m eval.runner --scenario lock_contention --runs 3
    python -m eval.runner --all --runs 3 --live   # use real LLM

Scores per scenario and overall. Chaos injectors need real Postgres
running — set ``AI_DBA_INTEGRATION=1`` (matches the pytest convention)
to acknowledge that.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from dataclasses import asdict

from agent.loop import run
from eval.chaos import SCENARIOS
from eval.rule_llm import RuleBasedLLM
from eval.scorer import Score, score_diagnosis


DEFAULT_QUESTION = "The database feels unhealthy. Investigate and diagnose."


def _run_once(scenario_key: str, question: str, *, live: bool) -> Score:
    spec = SCENARIOS[scenario_key]
    injector = spec.factory()
    injector.start()
    try:
        # Give background sessions a moment to register in pg_stat_activity.
        time.sleep(1.0)

        if live:
            from agent.llm import build_llm

            llm = build_llm()
        else:
            llm = RuleBasedLLM()

        result = run(question, llm=llm, max_steps=8)
        return score_diagnosis(scenario_key, spec.expected_findings, result.diagnosis)
    finally:
        injector.stop()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval")
    p.add_argument("--scenario", choices=sorted(SCENARIOS), help="single scenario")
    p.add_argument("--all", action="store_true", help="run every scenario")
    p.add_argument("--runs", type=int, default=1)
    p.add_argument("--live", action="store_true", help="use real LLM via build_llm()")
    p.add_argument("--question", default=DEFAULT_QUESTION)
    args = p.parse_args(argv)

    if not args.scenario and not args.all:
        p.error("pass --scenario NAME or --all")

    if not os.getenv("AI_DBA_INTEGRATION"):
        print(
            "refusing to run eval: chaos injectors need a real DB; "
            "set AI_DBA_INTEGRATION=1 to confirm.",
            file=sys.stderr,
        )
        return 2

    keys = [args.scenario] if args.scenario else list(SCENARIOS)
    all_scores: list[Score] = []
    per_scenario: dict[str, list[float]] = {k: [] for k in keys}

    for k in keys:
        for i in range(args.runs):
            score = _run_once(k, args.question, live=args.live)
            all_scores.append(score)
            per_scenario[k].append(score.score)
            print(
                f"[{k} run {i+1}/{args.runs}] score={score.score:.2f} "
                f"matched={score.matched} missing={score.missing}"
            )

    print("\n=== summary ===")
    for k, scores in per_scenario.items():
        print(
            f"{k}: mean={statistics.mean(scores):.2f} "
            f"pass_rate={sum(s >= 0.5 for s in scores)}/{len(scores)}"
        )
    overall = statistics.mean(s.score for s in all_scores)
    print(f"overall mean score: {overall:.2f}")

    # machine-readable dump on stderr for CI grep
    print(
        json.dumps(
            {"per_scenario": per_scenario, "overall": overall},
            indent=2,
        ),
        file=sys.stderr,
    )
    return 0 if overall >= 0.5 else 1


if __name__ == "__main__":
    raise SystemExit(main())


# quiet unused-import for asdict — kept for future JSON summary
_ = asdict
