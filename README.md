# Nova Agent Backend

Production-ready agentic backend built with **FastAPI + LangGraph + LangChain + Pydantic + Pinecone**.

An LLM agent reasons in a loop, calling a Pinecone-backed retrieval tool (RAG) when it needs
domain knowledge, with per-conversation memory, API-key auth, structured logging, and Docker.

## Architecture

```
app/
├── main.py                 # FastAPI app factory, middleware, lifespan
├── api/
│   ├── router.py           # Aggregate router
│   └── routes/
│       ├── health.py       # /health, /ready
│       └── chat.py         # /chat (agent), /ingest (RAG documents)
├── agent/
│   ├── graph.py            # LangGraph: agent <-> tools loop
│   ├── runner.py           # async entrypoint used by the API
│   ├── state.py            # graph state (TypedDict + add_messages reducer)
│   ├── nodes/agent_node.py # LLM call with bound tools
│   ├── prompts.py          # system prompt
│   └── tools/retrieval.py  # Pinecone knowledge-base search tool
├── services/
│   ├── llm.py              # ChatOpenAI + embeddings factories
│   └── vector_store.py     # Pinecone index mgmt + search/upsert (retried)
├── schemas/chat.py         # Pydantic request/response models
└── core/
    ├── config.py           # pydantic-settings config
    ├── logging.py          # structlog setup
    ├── security.py         # X-API-Key auth dependency
    └── exceptions.py       # domain errors + handlers
```

## Quick start

```bash
cd backend
python -m venv .venv && . .venv/Scripts/activate   # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env        # fill in OPENAI_API_KEY and PINECONE_API_KEY
uvicorn app.main:app --reload
```

Open the interactive docs at http://localhost:8000/docs

## Usage

Ingest documents into the knowledge base:

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "X-API-Key: dev-local-key" -H "Content-Type: application/json" \
  -d '{"documents":[{"content":"API keys are issued via the admin panel.","source":"onboarding"}]}'
```

Chat with the agent (reuse `thread_id` for multi-turn memory):

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "X-API-Key: dev-local-key" -H "Content-Type: application/json" \
  -d '{"message":"How are API keys issued?","thread_id":"demo-1"}'
```

## Configuration

All settings come from environment variables / `.env` — see `.env.example`.
Auth is disabled automatically when `API_KEYS` is empty (local dev convenience).

## Testing

```bash
pip install -e ".[dev]"
pytest          # external services are mocked; no real API keys needed
ruff check .
mypy app
```

## Production notes

- Swap `MemorySaver` in `agent/graph.py` for a persistent checkpointer (e.g. Postgres)
  so conversation memory survives restarts and scales across replicas.
- Run behind a reverse proxy; set `ENVIRONMENT=production` to disable `/docs` and emit JSON logs.
- Set real `API_KEYS` and tighten `CORS_ORIGINS` before deploying.
```
