# AI Restaurant Review Reply Agent

Drafts replies to restaurant reviews in the owner's tone with Claude, and posts **only after explicit human approval**.
Every posted reply is re-fetched from the dashboard and verified before it is shown as "Replied".

## Architecture
```
backend/app/
  main.py        FastAPI routes + input validation (thin; no business rules)
  services.py    Workflow: sync, drafts, approvals, idempotent posting, verification, results
  dashboard.py   DashboardAdapter interface, MockDashboard (SQLite), OrganizerDashboard (placeholder)
  generator.py   ClaudeGenerator (real) and MockGenerator (deterministic, offline)
  safety.py      Redaction of emails/phones, injection + sensitive-topic flags, reply checks
  db.py          SQLite schema + transactions     config.py  .env loading     sample_data.py  demo reviews
backend/tests/   test_workflow.py (28 tests), test_api.py (HTTP smoke tests)
backend/demo.py  Command-line walkthrough
frontend/src/    React (Vite): Home, Reviews, Reply editor, Tone settings, Results
```
Database tables: `reviews`, `owner_settings`, `reply_drafts` (versioned), `approval_decisions` (history, invalidation),
`posting_attempts` (unique idempotency key, one `posted` row per review), `verifications`, `status_history`.

## Setup
Backend (Python 3.10+):
```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then edit .env
uvicorn app.main:app --reload --port 8000
```
Claude key: put `ANTHROPIC_API_KEY=...` in `backend/.env` (never in React). For an offline first run use `AI_PROVIDER=mock`.

Frontend (Node 18+), in a second terminal:
```bash
cd frontend
npm install
npm run dev          # open http://localhost:5173
```

## Tests
```bash
cd backend
python -m unittest discover -s tests -t . -v     # no extra packages needed
pytest                                            # after pip install -r requirements.txt
python demo.py                                    # scripted drafted -> approved -> posted -> reloaded -> verified run
```

## Demo in the UI
1. Reviews -> Sync from dashboard (8 sample reviews; `rev_1002` and `rev_1003` are both "Alex Kim").
2. Tone settings -> choose "friendly" -> Save.
3. Open `rev_1003` -> Generate AI draft (status: Drafted). Edit the text -> Save edit (new version, approval invalidated).
4. Approve and Post -> confirm. Status goes Approved -> Needs verification -> Replied & verified.
5. Click Approve and Post again: no second reply is created.
6. `rev_1004` (contains phone/email): the draft is flagged and contact details never reach the AI or the reply.
7. Skip one review, "Save as draft only" on another, then open Results to see Replied / Drafted only / Skipped.

## Known limitations
- The real organizer dashboard is **not integrated**: its endpoints/credentials are unknown. `OrganizerDashboard` in
  `dashboard.py` raises a clear error instead of guessing. To finish: implement `list_reviews`, `get_review`,
  `post_reply` there (map its fields to the review shape documented in `DashboardAdapter`) and set `DASHBOARD_MODE=organizer`.
- Single-process design: one lock serializes workflow calls (fine for a demo; use Postgres + row locks to scale out).
- No login/authentication: add it before exposing this beyond localhost.
- MockGenerator replies are simple templates (they can e.g. thank someone for "the parking" if mentioned); real quality comes from Claude.
