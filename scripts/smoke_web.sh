#!/bin/bash
# Quick manual smoke test of the running UI.
set -e
curl -s -o /dev/null -w "GET /            -> %{http_code}\n" http://127.0.0.1:8000/
curl -s -o /dev/null -w "GET /api/tools    -> %{http_code}\n" http://127.0.0.1:8000/api/tools
curl -s -o /dev/null -w "POST /api/diagnose -> %{http_code}\n" \
  -H 'Content-Type: application/json' \
  -d '{"question":"is the DB up?","llm":"rule"}' \
  http://127.0.0.1:8000/api/diagnose
