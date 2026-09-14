# Project Config

Read by the `prodigy-core` `/plan-task` and `/start-task` commands.

Reference template: `~/.vscode/agent-plugins/gitlab.ciklum.net/prodigy/prodigy-claude/settings-snippets/project-config.example.md`.

---

## Task Tracker

- **Name:** `none`   <!-- freeform tasks; the plan command will ask for the task description inline -->
- **Issue-fetch tool:** _(n/a)_
- **ID format example:** _(n/a — kebab-case slug derived from the title, e.g. `web-local-lecture-transcriptor`)_
- **Branch-name field:** _(n/a — the plan command proposes `feat/<slug>`)_

---

## Project Layout

Two-project repo: a Python backend and a React frontend.

- **Source root:** `backend/app/` (backend) and `frontend/src/` (frontend)
- **Tests root:** `backend/tests/`
- **Coding-standards doc:** `tasks/task-web-local-lecture-transcriptor.context.md` — sections **Architectural Notes** and **Resolutions Made**. A dedicated `docs/coding-standards.md` is not yet written; the task context file is the source of truth until one exists.
- **Tasks directory:** `tasks/`

---

## Validation Commands

Full-project validation (run before merging a task):

```bash
# Lint
ruff check backend
black --check backend
npm run lint --prefix frontend

# Type-check
mypy backend/app
npx --prefix frontend tsc --noEmit -p frontend

# Tests
pytest backend/tests
```

### Per-file commands (used by sub-agents after editing a file)

```bash
# Python — lint + fix a single file
ruff check {file} --fix
black {file}

# Python — type-check a single file
mypy {file}

# TypeScript / TSX — lint + fix a single file
npx --prefix frontend eslint {file} --fix
```

Notes for sub-agents:
- `tsc` is project-scoped, not file-scoped. For per-file type-check confidence on TS/TSX, run the full-project `tsc --noEmit -p frontend` after finishing a feature slice.
- If the change is inside `backend/`, the Python commands are enough. If inside `frontend/`, the JS commands are enough.

---

## Code Review Agent

- **Name:** _(blank)_

No project-specific code-review agent is configured. The orchestrator falls back to `subagent_type="general-purpose"` (or the `Explore` agent for read-only review passes).

---

## Skill Mapping

No plugin skills are configured for this project's stack yet. Every layer follows project convention — implementation agents refer to the task context file, `.claude/project-config.md` (this file), and idiomatic FastAPI / React practice.

| Layer | Skill name |
|---|---|
| Database models (SQLAlchemy 2.0) | _(project convention)_ |
| Request/response models (Pydantic v2) | _(project convention)_ |
| DB migrations | _(project convention — schema created via `metadata.create_all()`; no Alembic)_ |
| Integration tests | _(project convention — pytest + FastAPI TestClient)_ |
| Unit tests | _(project convention — pytest)_ |
| Repository / data-access layer | _(project convention — thin SQLAlchemy sessions per request; no repository classes)_ |
| Service layer | _(project convention)_ |
| API endpoints (FastAPI routers) | _(project convention)_ |
| Background worker | _(project convention — single long-lived thread + `threading.Lock`)_ |
| LLM adapter | _(project convention — Ollama-only, strict-JSON with bounded retry)_ |
| Prompt templates | _(project convention — versioned files under `backend/app/prompts/`)_ |
| React pages / components | _(project convention — Vite + TS strict + Tailwind + TanStack Query)_ |
| Frontend API client | _(project convention — `fetch` wrapper with bearer + 401 redirect)_ |

If plugin skills are installed later (e.g. `python-sqlalchemy-models`, `python-pydantic-models`), replace the right-hand column and re-run `/plan-task` — it will honour the mapping.

---

## Notes

Project-specific conventions and gotchas that the orchestrator and sub-agents must respect:

### Stack

- **Backend:** FastAPI + Pydantic v2 + SQLAlchemy 2.0 (SQLite) + `faster-whisper` (CTranslate2) + Ollama (`gemma4:12b`) + ReportLab.
- **Frontend:** React 18 + Vite + TypeScript (strict) + Tailwind + React Router + TanStack Query.
- **Runtime prerequisites:** Python 3.11+, Node 20+, `ffmpeg` on PATH, Ollama running with the target model pulled.
- **No Docker, no Redis, no Celery, no Alembic, no PostgreSQL, no S3.** This is a deliberately local single-machine app.

### Configuration surfaces

When a task adds or changes a setting (env var, model name, path, threshold), update **all three** of:

1. `backend/app/core/config.py` — the Pydantic settings class
2. `backend/.env.example` — the copy-paste template
3. `README.md` — the env-var table under "Configuration"

There is no other config surface. Do **not** create a `docker-compose.yml`, a Helm chart, a CI env file, or a secrets manager.

### Invariants sub-agents must not break

- **Session eviction is global.** On any successful login, delete every other row in the `sessions` table. Never scope the eviction to `user_id`. This is the "only 1 active user at a time" requirement.
- **Single-job lock is process-wide.** One `threading.Lock` module-level in the worker package. The upload+process API endpoint checks the lock and returns 409 rather than enqueuing a second job.
- **Ownership check returns 404, never 403.** Missing OR not-owned → 404 with a generic "not found" message. Do not leak existence.
- **Pipeline is resumable ONLY via the explicit retry endpoint.** Failures still set `lecture.status = failed` and populate `error_message`, but the pipeline persists a stage-level checkpoint on `Lecture.last_completed_stage` at every stage boundary. `POST /api/lectures/{id}/retry` resumes from the first failed stage; server restart preserves partial artifacts. Do NOT add auto-resume on server restart, per-chunk (sub-stage) checkpointing, or editing endpoints for transcript / summary / glossary. Legacy failed rows (with `last_completed_stage IS NULL`) remain non-retryable — the "delete and re-upload" recovery flow still applies to them.
- **Media paths derive from DB ids only.** Never from user-supplied filenames. Canonical path: `storage/media/{user_id}/{lecture_id}/{original.ext, normalized.wav}`.
- **LLM strict-JSON with bounded retry (max 2).** The LLM adapter owns the request → parse → validate → retry loop. Business services receive already-validated Pydantic models.
- **Prompt files are immutable per version.** To change a prompt, add `summary_v2.md.j2` and switch the constant. Do not edit `summary_v1.md.j2` in place.
- **`faster-whisper` model size is fixed at `medium`.** The config exposes it only so tests can substitute `tiny` for speed.
- **View & export only.** There are no editing endpoints for transcripts, summaries, or glossaries. Do not add PATCH/PUT routes to `/api/lectures/*/summary` etc.

### Style

- **Python:** ruff + black + mypy (strict on `backend/app/`). Type annotations required on public functions.
- **TypeScript:** eslint + prettier + `tsc --strict`. No `any` in checked-in code.
- **Naming:** snake_case in Python, camelCase in TS, kebab-case for filenames on the frontend (`lecture-viewer-page.tsx` acceptable; existing plan uses PascalCase page names which is also fine — pick one and stay consistent per folder).
- **Error envelope:** `{"error": {"code": "...", "message": "..."}}` on every non-2xx response.
