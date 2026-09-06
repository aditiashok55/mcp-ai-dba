"""FastAPI backend for the AI-DBA web UI.

Endpoints:
    GET  /                — the single-page app
    GET  /api/tools       — list the tools the agent is allowed to call
    POST /api/diagnose    — run the agent loop, return a Diagnosis + trace

The backend is a thin wrapper: it re-uses :func:`agent.loop.run`
unchanged, so the security and observability guarantees of the CLI
also hold for the web UI.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.loop import run
from agent.mcp_client import list_tools


STATIC_DIR = Path(__file__).parent / "static"


class DiagnoseRequest(BaseModel):
    model_config = {"extra": "forbid"}

    question: str = Field(..., min_length=1, max_length=1000)
    llm: Literal["rule", "anthropic", "openai", "ollama"] = "rule"
    max_steps: int = Field(default=8, ge=1, le=20)


class DemoRequest(BaseModel):
    model_config = {"extra": "forbid"}

    scenario: Literal["slow_query", "lock_contention", "idle_in_transaction"]
    llm: Literal["rule", "anthropic", "openai", "ollama"] = "rule"
    max_steps: int = Field(default=8, ge=1, le=20)


class DiagnoseResponse(BaseModel):
    ok: bool
    diagnosis: dict[str, Any] | None = None
    tool_call_ids: list[str] = []
    raw_final: str | None = None
    error: str | None = None


def _build_llm(kind: str):
    if kind == "rule":
        from eval.rule_llm import RuleBasedLLM
        return RuleBasedLLM()

    from agent.llm import AnthropicLLM, OllamaLLM, OpenAILLM

    if kind == "anthropic":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise HTTPException(
                status_code=400,
                detail="ANTHROPIC_API_KEY not set on the server.",
            )
        return AnthropicLLM()

    if kind == "openai":
        if not os.getenv("OPENAI_API_KEY"):
            raise HTTPException(
                status_code=400,
                detail="OPENAI_API_KEY not set on the server.",
            )
        return OpenAILLM()

    if kind == "ollama":
        return OllamaLLM()

    raise HTTPException(status_code=400, detail=f"unknown llm {kind!r}")


def create_app() -> FastAPI:
    app = FastAPI(title="AI-DBA", version="0.1.0")

    # Start the Prometheus /metrics HTTP server (idempotent, respects
    # METRICS_ENABLED). Same guarantees the stdio MCP server gets.
    from mcp_server.metrics import ensure_started as _start_metrics
    _start_metrics()

    @app.get("/api/tools")
    def tools() -> list[dict[str, Any]]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in list_tools()
        ]

    @app.get("/api/providers")
    def providers() -> dict[str, Any]:
        """Which LLM providers are usable in the current environment."""
        from agent.llm import _ollama_reachable

        return {
            "rule": {"available": True, "reason": "offline, deterministic"},
            "anthropic": {
                "available": bool(os.getenv("ANTHROPIC_API_KEY")),
                "reason": "ANTHROPIC_API_KEY " + (
                    "set" if os.getenv("ANTHROPIC_API_KEY") else "not set"
                ),
            },
            "openai": {
                "available": bool(os.getenv("OPENAI_API_KEY")),
                "reason": "OPENAI_API_KEY " + (
                    "set" if os.getenv("OPENAI_API_KEY") else "not set"
                ),
            },
            "ollama": {
                "available": _ollama_reachable(),
                "reason": (
                    "reachable at " + os.getenv("OLLAMA_HOST", "http://localhost:11434")
                    if _ollama_reachable()
                    else "no Ollama server reachable"
                ),
            },
        }

    @app.post("/api/diagnose", response_model=DiagnoseResponse)
    def diagnose(req: DiagnoseRequest) -> DiagnoseResponse:
        llm = _build_llm(req.llm)
        result = run(req.question, llm=llm, max_steps=req.max_steps)
        return DiagnoseResponse(
            ok=result.diagnosis is not None,
            diagnosis=result.diagnosis.model_dump() if result.diagnosis else None,
            tool_call_ids=result.tool_call_ids,
            raw_final=result.raw_final,
            error=result.error,
        )

    # --- demo mode -----------------------------------------------------
    #
    # One-click "here's a broken database, watch the agent find the
    # problem". Starts a chaos injector, runs the loop, stops the
    # injector. Never leaves background sessions dangling.

    @app.post("/api/demo", response_model=DiagnoseResponse)
    def demo(req: DemoRequest) -> DiagnoseResponse:
        import time
        from eval.chaos import SCENARIOS

        spec = SCENARIOS[req.scenario]
        injector = spec.factory()
        injector.start()
        try:
            # Let the background sessions register in pg_stat_activity.
            time.sleep(1.0)
            llm = _build_llm(req.llm)
            question = (
                f"[demo:{req.scenario}] The database feels unhealthy. "
                "Investigate and diagnose the root cause."
            )
            result = run(question, llm=llm, max_steps=req.max_steps)
            return DiagnoseResponse(
                ok=result.diagnosis is not None,
                diagnosis=result.diagnosis.model_dump() if result.diagnosis else None,
                tool_call_ids=result.tool_call_ids,
                raw_final=result.raw_final,
                error=result.error,
            )
        finally:
            injector.stop()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # Serve /static/* explicitly so /api/* isn't shadowed.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app


app = create_app()


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    port = int(os.getenv("WEB_PORT", "8000"))
    uvicorn.run("web.backend:app", host="127.0.0.1", port=port, reload=False)
