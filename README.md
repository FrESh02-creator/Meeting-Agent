# Meeting Agent Monorepo

## Structure

```text
meeting-agent/
  frontend/   # React + Tailwind
  backend/    # FastAPI + LangGraph
```

## Frontend

```bash
cd frontend
npm install
npm run dev
```

Default: `http://localhost:5173`

## Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Default: `http://localhost:8000`

## API

- `POST /api/analyze/text`
- `POST /api/analyze/upload`
- `POST /api/report/weekly`

## LangGraph Workflow

Design doc: `backend/docs/langgraph_workflow.md`
