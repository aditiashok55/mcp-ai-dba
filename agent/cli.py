"""Command-line entrypoint.

Usage:
    python -m agent.cli "why is the database slow?"
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from agent.llm import build_llm
from agent.loop import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ai-dba")
    parser.add_argument("question", help="What should the agent investigate?")
    parser.add_argument(
        "--llm",
        choices=("auto", "anthropic", "openai", "ollama"),
        default="auto",
        help="LLM provider (default: auto — reads AI_DBA_LLM or picks by env).",
    )
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    llm = build_llm(args.llm)
    result = run(args.question, llm=llm, max_steps=args.max_steps)

    if result.diagnosis is not None:
        print(json.dumps(result.diagnosis.model_dump(), indent=2))
        return 0

    print(
        json.dumps(
            {
                "error": result.error,
                "raw_final": result.raw_final,
                "tool_call_ids": result.tool_call_ids,
            },
            indent=2,
        ),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
