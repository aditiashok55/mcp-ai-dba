# Local, no keys:
OLLAMA_MODEL=llama3.1 python -m agent.cli --llm ollama "why is the DB slow?"

# OpenAI:
OPENAI_API_KEY=sk-... python -m agent.cli --llm openai "..."

# Behind an OpenAI-compatible endpoint (e.g. LM Studio):
OPENAI_API_KEY=lm-studio OPENAI_BASE_URL=http://localhost:1234/v1 \
  python -m agent.cli --llm openai "..."

# UI shows all four in the dropdown; unavailable ones are greyed out.
python -m web.backend