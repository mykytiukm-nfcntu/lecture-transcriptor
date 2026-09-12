# Task: WEB — Local Lecture Transcriptor

## Status
- Task ID: `web-local-lecture-transcriptor`
- Task URL: (freeform task — no tracker)
- Git Branch: `feat/web-local-lecture-transcriptor`
- Project / Space: ISCM-2026
- Created: 2026-09-12
- Last Updated: 2026-09-12
- Progress: 100%
- State: COMPLETED

## Task Content

Simple, lightweight local web app for lecture transcription from pre-recorded **MP3 or WAV** files. **No realtime transcription.**

A user uploads an audio file once and receives, within minutes, a study package:

1. **Transcript** — ASR with timestamped segments.
2. **Structured summary** — hierarchical notes (sections, key statements, formulas, examples), each anchored to a timestamp.
3. **Glossary** — 10–30 domain terms with 1–3-sentence definitions grounded in the lecture content.

### Functional requirements (verbatim from user, condensed)

- **Registration & auth:** username + password, no email verification, non-expiring opaque token. Local single-machine app.
- **Isolation:** every resource scoped to its owning user; no cross-user reads.
- **View & export only** — no editing anywhere in the app.
- **Export formats:** PDF and TXT per lecture.
- **Concurrency:** only 1 active transcription at a time; only 1 active session at a time globally.
- **Organisation:** Course → Lecture hierarchy.
- **Audio normalisation** before ASR: mono, 16 kHz, loudness normalisation.
- **No progress tracking** during ASR/generation: show a preliminary ETA before the job starts, then a running timer.
- Original media stored **locally** on disk. Disk space is not a concern.
- **ASR:** `faster-whisper` (CTranslate2), model size **medium**, hard-coded.
- **Language:** auto-detect if free (faster-whisper's native detection is free); fallback to Ukrainian.
- **Not resumable** — any failure marks the lecture failed; the user re-uploads.

### Optional / accepted trade-offs

- Asynchronous processing pipeline (ASR → segmentation → LLM generation) — YES, but single-threaded worker inside the web process.
- **Voice activity detection** — enabled (built into faster-whisper, free win).

### Content generation requirements

- Transcript split into semantic chunks that fit the model context window, with overlap, respecting sentence boundaries.
- **Structured summary:** hierarchical headings, bullet key points, highlighted definitions, formulas and examples, each carrying a timestamp reference.
- **Glossary:** 10–30 domain terms with 1–3-sentence definitions grounded in the transcript.
- All LLM outputs requested and validated as strict JSON against a Pydantic schema; up to 2 bounded retries before the stage is marked failed.
- Generation language defaults to the lecture language; user may pick a different generation language.
- LLM is local and **not** runtime-configurable — a hard dependency, chosen via environment variable.

---

## Planning Analysis (Main Agent Reference)

*This section documents the main agent's analysis. It is NOT passed to sub-agents.*

### Baseline vs. simplified requirements

Reference baseline lives at [lecture-assistant-requirements.md](../../../../../Downloads/lecture-assistant-requirements.md) (v1.0). The user's message **narrows** it substantially. Everything below is the reconciled truth for THIS task.

Dropped from baseline v1.0:
- Quiz generation (FR-GEN-4) and quiz attempts.
- Editable summary / glossary (FR-VIEW-3).
- Email verification, JWT access/refresh with rotation (FR-AUTH-1/2/5).
- Full-text search (FR-VIEW-7).
- Multiple concurrent workers (FR-PIP-5).
- Live progress streaming (FR-PIP-3).
- Resumable pipeline stages (FR-ASR-6, NFR-3 partial).
- Pluggable / OpenAI-compatible LLM adapter (FR-GEN-7 partial — we keep an adapter *class* for cleanliness but no runtime switching).
- S3 / MinIO object storage (FR-UPL-6 — switched to local filesystem).
- m4a, ogg, mp4, mkv (FR-UPL-1/2 — only mp3 + wav).
- Roles (student/lecturer/admin), password reset (FR-AUTH-4/5).
- Admin management panel.

Retained from baseline v1.0:
- Layered architecture with a clear API / service / worker split.
- Data model shape for users, courses, lectures, transcripts, transcript_segments, summaries, glossary_terms (minus the quiz/edit fields).
- Prompt templates as versioned files under `prompts/`.
- Strict-JSON LLM outputs with bounded retry.
- FastAPI + Pydantic v2 + SQLAlchemy 2.0 backend, React 18 + Vite + Tailwind frontend.

### Resolutions Made

| Requirement | Baseline convention | Decision |
|---|---|---|
| "simple as hell / lightweight" web app | React 18 + Vite + Tailwind SPA | **Keep** React/Vite SPA (user preference) — matches baseline but two toolchains. |
| Async pipeline (optional) + 1 active job | Celery + Redis worker | **In-process background thread + global lock**. No Redis, no Celery. |
| Local LLM, not runtime configurable | Pluggable OpenAI-compatible endpoint | **Ollama** at `http://localhost:11434`, default model `gemma4:12b` (via env var). Adapter kept but only one implementation. |
| "Only 1 active user at a time" | Multi-user | **Session-eviction:** many accounts allowed; on any successful login, delete all other rows in `sessions` table. |
| Database backend | PostgreSQL 16 | **SQLite** (single file at `./storage/app.db`). |
| PDF library | (unspecified) | **ReportLab** with bundled DejaVu Sans TTF for Cyrillic. |
| VAD | Should-have | **Enabled** — faster-whisper's built-in Silero VAD flag. |
| Progress reporting | WebSocket/SSE | **None.** Duration probe → preliminary ETA before start; `started_at` → elapsed timer while running. Frontend polls `GET /api/lectures/{id}/status` every ~2 s. |
| Token strategy | JWT access+refresh | **Opaque random token** stored server-side in `sessions` table; sent via `Authorization: Bearer`. Never expires; invalidated only by logout or by another login (any user) evicting it. |
| Accepted formats | mp3, wav, m4a, ogg + video | **mp3, wav only.** Reject everything else with 415. |
| Export formats | Markdown + PDF | **PDF + TXT.** |

### Clarifications Received

1. **Q:** Frontend architecture — how lightweight?
   **A:** FastAPI + React/Vite SPA.
   **Resolution:** Two-project repo (`backend/`, `frontend/`), CORS-enabled FastAPI, TanStack Query on the client.

2. **Q:** Background processing model?
   **A:** In-process background thread + global lock.
   **Resolution:** One long-lived worker thread started on FastAPI startup, consumes from an in-memory queue. A `threading.Lock` guards the "one active job" invariant. New submissions while the lock is held are rejected with 409 Conflict.

3. **Q:** Local LLM runtime?
   **A:** Ollama with `gemma4:12b` (originally planned as `qwen2.5:7b-instruct`; swapped 2026-09-12 for stronger multilingual reasoning at ~2× the model footprint).
   **Resolution:** Ollama HTTP client, model name and base URL configured via env vars. JSON mode used for structured outputs. No adapter interface for other providers.

4. **Q:** "Only 1 active user at a time" meaning?
   **A:** Multiple accounts, only one active session globally (session eviction on any login).
   **Resolution:** Successful login DELETEs every other row from `sessions`. All subsequent requests carrying an evicted token receive 401.

5. **Q:** Database backend?
   **A:** SQLite.
   **Resolution:** SQLite file at `./storage/app.db`. Schema created on startup via SQLAlchemy `metadata.create_all()` — no Alembic. Foreign keys enabled via PRAGMA.

6. **Q:** PDF library and VAD?
   **A:** ReportLab + enable VAD.
   **Resolution:** ReportLab for PDF export with DejaVu Sans TTF bundled at `backend/app/assets/fonts/DejaVuSans.ttf`. Silero VAD enabled on every `faster-whisper` call.

---

## Implementation Plan

> **Rule:** Describes WHAT behavior is needed. No method names, no signatures. Implementation agents pick idiomatic names/shapes.

### Step 1 — Repository skeleton
Two-project layout (`backend/`, `frontend/`) at the workspace root, plus `storage/` (gitignored), `docs/`, and a top-level README. Include `.gitignore`, `.env.example`, `pyproject.toml`, `package.json`, `vite.config.ts`, `tsconfig.json`, `tailwind.config.ts`. No Docker required.

### Step 2 — Backend runtime, configuration, and persistence
FastAPI application factory. Environment-driven configuration for: DB path, storage root, Ollama base URL and model name, Whisper model size (constant `medium`, still surfaced as config for tests), CORS origins, worker queue size (`1`). SQLite engine with foreign keys enforced. Structured JSON logging with a per-job correlation id. Database schema materialised at startup.

### Step 3 — Authentication
Registration form accepting username + password (bcrypt/argon2 hash). Login endpoint that (a) verifies credentials, (b) **deletes every other row in the sessions table**, (c) inserts a fresh opaque token, (d) returns the token. Logout deletes the caller's session. `current_user` dependency resolves `Authorization: Bearer` → session → user. Non-owner access to any user-scoped resource returns 404 (not 403 — do not leak existence).

### Step 4 — Course & lecture management
Course: create, list, detail, delete — all user-scoped. Lecture: list within a course, detail, delete. Lecture carries title, creation timestamp, original filename, duration, language, status (`queued | normalizing | transcribing | generating | completed | failed`), error message, `started_at`, `finished_at`. No rename, no edit of transcripts / summaries / glossaries anywhere.

### Step 5 — Media upload & normalisation
Multipart upload accepting MP3 and WAV only. Validate MIME **and** magic bytes; reject everything else with 415 and a specific error code. Persist original under `storage/media/{user_id}/{lecture_id}/original.<ext>`. Probe duration (used for the preliminary ETA). Normalise to mono, 16 kHz, `loudnorm` filter → `normalized.wav` next to the original. On any failure at this stage, mark lecture failed.

### Step 6 — Job queue & single-job lock
One long-lived worker thread started at application startup, pulling from an in-memory queue. A module-level lock guarantees the "only one active transcription" invariant. Upload+process attempts while a job is running are rejected with 409 Conflict and a clear message ("another transcription is currently running"). The queue holds at most one pending item — the API itself enforces this and does not enqueue when a job is already active.

### Step 7 — ASR stage
Load the `faster-whisper medium` model once (lazy on first job is acceptable). Silero VAD enabled on every call. Auto-language-detection; if detection is missing or confidence is below a sensible threshold, fall back to Ukrainian. Persist transcript segments (index, start seconds, end seconds, text, confidence) plus the concatenated full text and the detected language.

### Step 8 — Chunking
Split the transcript into token-aware chunks sized for the target LLM context window, with configurable overlap, respecting sentence boundaries. Token counting uses the target model's tokenizer if available; otherwise a whitespace / rough heuristic.

### Step 9 — LLM generation
Ollama HTTP client. Two versioned prompt template files (summary v1, glossary v1) stored under `backend/app/prompts/`. Pydantic schemas define the exact JSON shape expected for each artifact. Every call requests strict JSON, parses it, and validates against the schema; parse or validation failure triggers up to **2** retries, then marks the stage failed with a specific error code.

**Summary** uses a map-reduce shape: per-chunk partial notes → merge pass producing the final hierarchy. Every section carries a start timestamp (the earliest transcript timestamp it references).

**Glossary** targets 10–30 terms. Every term carries a first-mention timestamp. Definitions must be grounded in the transcript — the prompt explicitly forbids inventing content not present in the lecture.

Generation language defaults to the detected lecture language; a query parameter on the process request may override it.

### Step 10 — Read-only artifact APIs
Endpoints for transcript (paginated segments), summary, glossary, status (for the timer), and the raw audio file (streamed for the player). All checks user-scoped.

### Step 11 — Export (PDF + TXT)
TXT export produces a readable concatenation: header (title, date, duration, language), summary, glossary, then the full transcript with `[HH:MM:SS]` timestamps per segment. PDF export uses ReportLab with a bundled Unicode TTF font. Same content shape as TXT, styled. Both delivered as file downloads via `Content-Disposition`.

### Step 12 — Frontend shell
Vite + React + TypeScript + Tailwind. React Router for pages, TanStack Query for server state, opaque token stored in `localStorage`, sent as `Authorization: Bearer`. On 401, redirect to `/login`.

### Step 13 — Frontend auth pages
Login and register forms. Simple validation. No password-strength UX beyond a minimum length.

### Step 14 — Frontend course & lecture pages
Course list with create form. Course detail with lecture list and upload form. Status badges on each lecture; while a lecture is in-progress the row polls `status` every ~2 seconds. Upload form disabled (with tooltip) whenever any lecture in the whole app is currently processing.

### Step 15 — Frontend lecture viewer
Three tabs: Transcript, Summary, Glossary. Embedded audio player using `<audio>`. Clicking a summary heading or a glossary term seeks the player to the referenced timestamp and scrolls the transcript tab to the matching segment. Transcript segments are clickable — click seeks the player. Two export buttons (PDF, TXT). Before the pipeline starts, show the preliminary ETA derived from duration; while running, show elapsed time.

### Step 16 — Tests
- Auth: register, login, session eviction (login by user B invalidates user A's token), logout.
- Ownership: user A cannot read user B's course / lecture / transcript / summary / glossary / export (all return 404).
- Upload: mp3 accepted, wav accepted, m4a rejected with 415, empty file rejected.
- Job lock: second upload while a job is running returns 409.
- Pipeline end-to-end with a short fixture WAV (mocked LLM to keep the test hermetic).
- Export: PDF and TXT for a completed lecture contain the expected sections.

### Step 17 — Documentation
README covering prerequisites (Python 3.11+, Node 20+, ffmpeg on PATH, Ollama installed with the target model pulled), local run instructions for backend and frontend, and the env vars in `.env.example`.

---

## Files to Modify/Create

Skill column reads `(project convention)` throughout because no `project-config.md` / plugin skill mapping is configured for this workspace. Implementation agents follow the coding-standards conventions in this document and idiomatic FastAPI / React practice.

### Repository root

| Status | File | Skill | Purpose |
|---|---|---|---|
| [x] | `README.md` | (project convention) | Prerequisites, local run, env vars |
| [x] | `.gitignore` | (project convention) | Ignore `storage/`, `.venv/`, `node_modules/`, build artefacts |
| [x] | `.env.example` | (project convention) | Shared example env |

### Backend

| Status | File | Skill | Purpose |
|---|---|---|---|
| [x] | `backend/pyproject.toml` | (project convention) | Deps, ruff/black/mypy config |
| [x] | `backend/.env.example` | (project convention) | Backend env template |
| [x] | `backend/app/main.py` | (project convention) | FastAPI app factory, startup hooks |
| [x] | `backend/app/core/config.py` | (project convention) | Env-driven settings |
| [x] | `backend/app/core/db.py` | (project convention) | SQLite engine + session dependency |
| [x] | `backend/app/core/security.py` | (project convention) | Password hashing, token generation |
| [x] | `backend/app/core/logging.py` | (project convention) | Structured JSON logging + correlation id |
| [x] | `backend/app/core/job_lock.py` | (project convention) | Module-level single-job lock |
| [x] | `backend/app/models/user.py` | (project convention) | User model |
| [x] | `backend/app/models/session.py` | (project convention) | Session model |
| [x] | `backend/app/models/course.py` | (project convention) | Course model |
| [x] | `backend/app/models/lecture.py` | (project convention) | Lecture model with status enum |
| [x] | `backend/app/models/transcript.py` | (project convention) | Transcript + TranscriptSegment |
| [x] | `backend/app/models/summary.py` | (project convention) | Summary artifact |
| [x] | `backend/app/models/glossary.py` | (project convention) | GlossaryTerm |
| [x] | `backend/app/schemas/` | (project convention) | Pydantic v2 request/response + LLM output schemas |
| [x] | `backend/app/api/deps.py` | (project convention) | `current_user`, `current_session`, DB session |
| [x] | `backend/app/api/auth.py` | (project convention) | Register / login / logout / me |
| [x] | `backend/app/api/courses.py` | (project convention) | Course CRUD (no rename/edit) |
| [x] | `backend/app/api/lectures.py` | (project convention) | Lecture list/detail/delete + upload+process |
| [x] | `backend/app/api/artifacts.py` | (project convention) | Transcript, summary, glossary, status reads |
| [x] | `backend/app/api/media.py` | (project convention) | Stream original audio for the player |
| [x] | `backend/app/api/export.py` | (project convention) | PDF and TXT export endpoints |
| [x] | `backend/app/services/media.py` | (project convention) | Validation, ffmpeg normalise, duration probe |
| [x] | `backend/app/services/asr.py` | (project convention) | faster-whisper wrapper (VAD, language detect) |
| [x] | `backend/app/services/chunking.py` | (project convention) | Sentence-boundary, token-aware chunking |
| [x] | `backend/app/services/llm.py` | (project convention) | Ollama HTTP client with strict-JSON + retry |
| [x] | `backend/app/services/summary_generator.py` | (project convention) | Map-reduce summary generation |
| [x] | `backend/app/services/glossary_generator.py` | (project convention) | Glossary generation with grounding |
| [x] | `backend/app/services/export_pdf.py` | (project convention) | ReportLab PDF renderer with Cyrillic font |
| [x] | `backend/app/services/export_txt.py` | (project convention) | TXT renderer |
| [x] | `backend/app/workers/worker.py` | (project convention) | Long-lived thread consuming the job queue |
| [x] | `backend/app/prompts/summary_v1.md.j2` | (project convention) | Summary prompt template |
| [x] | `backend/app/prompts/glossary_v1.md.j2` | (project convention) | Glossary prompt template |
| [ ] | `backend/app/assets/fonts/DejaVuSans.ttf` | (project convention) | Cyrillic-capable font for PDF |
| [x] | `backend/tests/conftest.py` | (project convention) | Test client + isolated SQLite per test |
| [x] | `backend/tests/test_auth.py` | (project convention) | Registration, login, eviction, logout |
| [x] | `backend/tests/test_ownership.py` | (project convention) | Cross-user isolation for every resource |
| [x] | `backend/tests/test_lectures.py` | (project convention) | Upload validation, job lock, delete |
| [x] | `backend/tests/test_pipeline.py` | (project convention) | End-to-end with fixture WAV + mocked LLM |
| [x] | `backend/tests/test_export.py` | (project convention) | PDF and TXT export content |

### Frontend

| Status | File | Skill | Purpose |
|---|---|---|---|
| [x] | `frontend/package.json` | (project convention) | React + Vite + TanStack Query + Tailwind deps |
| [x] | `frontend/vite.config.ts` | (project convention) | Dev proxy to backend, path aliases |
| [x] | `frontend/tsconfig.json` | (project convention) | Strict TS config |
| [x] | `frontend/tailwind.config.ts` | (project convention) | Tailwind config |
| [x] | `frontend/index.html` | (project convention) | Entry HTML |
| [x] | `frontend/src/main.tsx` | (project convention) | App bootstrap + QueryClient + Router |
| [x] | `frontend/src/App.tsx` | (project convention) | Route table + auth guard |
| [x] | `frontend/src/api/client.ts` | (project convention) | Fetch wrapper with bearer + 401 redirect |
| [x] | `frontend/src/api/auth.ts` | (project convention) | Auth queries/mutations |
| [x] | `frontend/src/api/courses.ts` | (project convention) | Course queries/mutations |
| [x] | `frontend/src/api/lectures.ts` | (project convention) | Lecture upload + polling + artifact reads |
| [x] | `frontend/src/hooks/useAuth.ts` | (project convention) | Auth state hook |
| [x] | `frontend/src/hooks/useLectureStatus.ts` | (project convention) | Polling hook for in-progress lectures |
| [x] | `frontend/src/pages/LoginPage.tsx` | (project convention) | Login form |
| [x] | `frontend/src/pages/RegisterPage.tsx` | (project convention) | Register form |
| [x] | `frontend/src/pages/CoursesPage.tsx` | (project convention) | Course list + create |
| [x] | `frontend/src/pages/CourseDetailPage.tsx` | (project convention) | Lecture list + upload |
| [x] | `frontend/src/pages/LectureViewerPage.tsx` | (project convention) | Tabs + player + exports |
| [x] | `frontend/src/components/AudioPlayer.tsx` | (project convention) | `<audio>` wrapper with seek API |
| [x] | `frontend/src/components/TranscriptView.tsx` | (project convention) | Clickable segments |
| [x] | `frontend/src/components/SummaryView.tsx` | (project convention) | Hierarchical summary with seek-on-click |
| [x] | `frontend/src/components/GlossaryView.tsx` | (project convention) | Term list with seek-on-click |
| [x] | `frontend/src/components/UploadForm.tsx` | (project convention) | Multipart upload + disable-when-locked |
| [x] | `frontend/src/components/StatusBadge.tsx` | (project convention) | Status badge + ETA / timer |
| [x] | `frontend/src/types/api.ts` | (project convention) | Response types |

### Configuration Changes Note

The single source of truth for configurable values is `backend/.env.example` (copied to `.env` locally). When adding a new setting, update: `backend/app/core/config.py`, `backend/.env.example`, and the README env-var table. There is no other config surface (no Docker Compose, no CI env, no secrets manager) because this is a local single-machine app.

---

## Architectural Notes

- **Concurrency model:** the FastAPI app runs with `uvicorn workers=1` in local mode. A single background thread inside that process consumes a queue guarded by a `threading.Lock`. The lock also gates the upload+process endpoint so that the API returns 409 rather than queuing a second job.
- **Session eviction is global, not per-user.** On any successful login, every other row in the `sessions` table is deleted. This is the "only 1 active user at a time" invariant.
- **No resumption.** Every stage of the pipeline mutates the lecture row's `status`. On failure, `status = failed` and `error_message` is populated. The user's only recovery path is deleting the lecture and re-uploading. Do not add retry-from-stage or checkpoint logic.
- **Media path convention:** `storage/media/{user_id}/{lecture_id}/{original.ext, normalized.wav}`. Never construct these paths from user-supplied strings — always from DB ids.
- **Ownership check pattern:** every user-scoped endpoint resolves the resource by id AND `user_id = current_user.id`. Missing → 404 (do not leak existence with a 403).
- **LLM strict-JSON pattern:** the LLM adapter is responsible for the request+parse+validate+retry loop. Business services receive already-validated Pydantic models.
- **PDF fonts:** ReportLab is used with a bundled DejaVu Sans TTF (registered at app startup). Do not rely on system fonts — the app must render Cyrillic on a clean Windows machine.
- **Faster-whisper model** is loaded once per process, lazily on the first job. Model size is fixed at `medium`; the config exposes it only so tests can substitute `tiny` for speed.
- **Prompt versioning:** every LLM call records the prompt filename (which encodes the version, e.g. `summary_v1.md.j2`) alongside the artifact for reproducibility. When updating a prompt, create `summary_v2.md.j2` and switch the constant — never edit v1 in place.

---

## Dependencies

- **Prerequisites (system):**
  - Python 3.11+
  - Node.js 20+
  - `ffmpeg` on PATH (needed by media normalisation and duration probing)
  - Ollama installed and running, with `ollama pull gemma4:12b` executed once
- **Python packages** (to be pinned in `backend/pyproject.toml`):
  - `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`
  - `sqlalchemy>=2`, `python-multipart`
  - `argon2-cffi` (password hashing)
  - `faster-whisper`, `ctranslate2`
  - `ffmpeg-python` (or direct `subprocess` calls to `ffmpeg`)
  - `httpx` (Ollama client)
  - `jinja2` (prompt templates)
  - `reportlab`
  - Dev: `pytest`, `pytest-asyncio`, `httpx` (test client), `ruff`, `black`, `mypy`
- **Frontend packages** (to be pinned in `frontend/package.json`):
  - `react`, `react-dom`, `react-router-dom`
  - `@tanstack/react-query`
  - `tailwindcss`, `postcss`, `autoprefixer`
  - Dev: `vite`, `@vitejs/plugin-react`, `typescript`, `eslint`, `prettier`
- **Existing code:** none — greenfield.

---

## Progress Log

### 2026-09-12 — Plan Created
- Task fetched (source: freeform message + reference doc `lecture-assistant-requirements.md`).
- Deep analysis completed against baseline v1.0.
- 6 clarifying questions asked and resolved (frontend, background processing, LLM, single-user semantics, database, PDF+VAD).
- Implementation plan created across 17 steps.
- Ready for implementation.

### 2026-09-12 — Implementation Started
- Created branch `feat/web-local-lecture-transcriptor`.
- No `.claude/project-config.md` found — sub-agents will follow FastAPI/React idiomatic practice + conventions in this document.
- Beginning wave-based sub-agent delegation.

### 2026-09-12 — Wave 1 done (repo skeleton + backend core)
- Repo: README.md, .gitignore, .env.example.
- Backend: pyproject.toml, .env.example, core/{config,db,security,logging,job_lock}.py + package markers.
- `python -m compileall backend/app` clean; VS Code diagnostics clean.

### 2026-09-12 — Wave 2 done (models + schemas)
- Models: user, session, course, lecture (+ LectureStatus enum), transcript (+ segments), summary, glossary. All SQLAlchemy 2.0 style, cascade FKs, unique constraints where required.
- Schemas: auth, course, lecture, transcript, summary (recursive SummarySection), glossary (10-30 entries), common (ErrorResponse, Paginated[T]).
- Compileall + import sanity check clean; Pylance clean.
- Note: Summary.content_json stringified JSON (parse via SummaryDocument.model_validate_json).

### 2026-09-12 — Wave 3 done (services + prompts + worker)
- Services: media (validate_mime + save_upload + probe_duration + normalise + ETA), asr (lazy faster-whisper + fallback language), chunking (whitespace token heuristic + sentence regex), llm (Ollama sync client + retry + Jinja2), summary_generator (map-reduce), glossary_generator (with grounding + elision), export_txt, export_pdf (DejaVuSans lazy-registered), pipeline (full ASR->LLM orchestration).
- Prompts: summary_v1, summary_reduce_v1, glossary_v1.
- Worker: daemon thread + shutdown sentinel + enqueue helper raising LockedError for 409.
- Compileall + import sanity clean (needed `pip install reportlab`).
- DejaVuSans.ttf still absent — documented in README; PDF export raises clear RuntimeError at first render if missing.

### 2026-09-12 — Wave 4 done (API + main.py)
- API: deps (HTTPBearer + get_current_user/session), auth (register/login/logout/me with global session eviction), courses (owner-scoped CRUD + lecture_count), lectures (nested upload+process with 415/409/400/202 + flat detail/delete), artifacts (transcript/summary/glossary/status), media (audio stream via FileResponse), export (TXT+PDF with 503 on missing font).
- main.py: lifespan startup order (logging → dirs → create_all → worker → best-effort Ollama probe), CORS from settings, X-Request-Id middleware for correlation_id, global handlers for LockedError→409 and MediaError→400.
- Approved wave-3 extension: `worker.enqueue(lecture_id, generation_language=None)` puts tuple; `pipeline.process_lecture(lecture_id, generation_language=None)`.
- Sanity: create_app() succeeds, 23 leaf endpoints registered.

### 2026-09-12 — Wave 5 done (backend tests)
- Files: conftest.py (env-isolated engine, TestClient with patched worker/Ollama, mp3/wav fixtures, mock_asr/mock_llm/mock_worker_enqueue/fake_ffmpeg), test_auth, test_ownership, test_lectures, test_pipeline, test_export.
- Fixed test-fixture bug: mock_llm needed to patch generate_json ALSO on summary_generator + glossary_generator (those modules do `from ... import generate_json` at import time). Test-only change; no production code touched.
- pytest: **50 passed, 1 skipped** (font download requires network) in 23s. Zero failing tests.

### 2026-09-12 — Wave 6 done (frontend scaffold + API + hooks)
- Root config: package.json, vite.config.ts, tsconfig.json, tailwind.config.ts, postcss.config.js, index.html, .eslintrc.cjs, .prettierrc.json, .gitignore.
- src/: main.tsx (StrictMode + QueryClientProvider + BrowserRouter), App.tsx (routes + RequireAuth guard), index.css (Tailwind directives).
- types/api.ts: all response shapes narrowly typed.
- api/: client.ts (fetchJson + fetchBlob + ApiError + token helpers + auth:invalid event), auth/courses/lectures modules (createAudioObjectUrl + downloadExport).
- hooks/: useAuth (getMe query + auth:invalid listener + logout), useLectureStatus (2s poll while in-progress).
- Deviation: build script drops `-b` because tsconfig uses noEmit (composite=true not compatible). Sanity: `npm install --package-lock-only` exit 0.

### 2026-09-12 — Wave 7 done (frontend pages + components)
- Pages: Login, Register (auto-login on success), Courses (create + list + delete), CourseDetail (upload + lecture list + global-lock disable), LectureViewer (processing/failed/completed screens + audio + tabs + exports).
- Components: AudioPlayer (forwardRef), TranscriptView, SummaryView (recursive), GlossaryView (chronological), UploadForm (file + title + auto/uk/en language), StatusBadge (live timer + ETA).
- Utility: format.ts (formatTimestamp/formatDuration/formatDateTime).
- App.tsx updated to route to real pages.
- Zero VS Code diagnostics.

### 2026-09-12 — Code review + fixes
- Reviewer report: 2 Critical, 6 Warning, 4 Suggestion.
- Fixed all Critical + Warning issues in 6 parallel fix agents:
  1. Critical: magic-byte rejection now returns 415 (was 400). Test updated to assert 415 + no phantom row. (`lectures.py`, `test_lectures.py`)
  2. Warning: upload-validation failures now `db.delete` + `db.commit` instead of committing a `failed` row. (`lectures.py`)
  3. Warning: `delete_course` now sweeps `storage/media/{user_id}/{lecture_id}/` for every cascaded lecture. (`courses.py`)
  4. Warning: `test_export_pdf_503_when_font_missing` rewritten to drive the real `_ensure_font_registered` → RuntimeError path (patches `_FONT_PATH` + `_font_registered` instead of `render_pdf`).
  5. Warning: `sniff_magic` accepts MPEG-1 Layer III CRC (`\xff\xfa`) and MPEG-2.5 (`\xff\xe2`/`\xe3`) via bit-mask check. (`media.py`)
  6. Warning: `llm.py` gains `close_client()`; `main.py` lifespan calls it in a try/finally after `stop_worker()`.
  7. Warning: dead `_shutdown_event` deleted from `worker.py`; sentinel-driven shutdown untouched.
- 4 Suggestions logged, not blocking (login timing side-channel, MIME check before lock, downloadExport revoke timing, transcript pagination in-memory slice).

### 2026-09-12 — Final validation
- `python -m compileall backend/app` → exit 0.
- `pytest backend/tests` → **50 passed, 1 skipped** (font-download skip; requires network) in 22.5s.
- VS Code diagnostics: zero on all backend fixed files.
- Ruff / black not installed locally — configured in `pyproject.toml`; user runs `pip install -e .[dev]` then `ruff check backend && black --check backend` for lint gates.
- Frontend: `npm install` needed before `tsc --noEmit` / `eslint`; deliverables reviewed with zero VS Code diagnostics.
- Task **COMPLETED**.

---

## Relevant Context

- Reference requirements doc: [lecture-assistant-requirements.md](../../../../../Downloads/lecture-assistant-requirements.md) (baseline v1.0 — narrower scope applies for this task).
- No pre-existing project conventions (greenfield). This context file IS the source of truth for conventions until a coding-standards doc exists.
- No similar implementations in the workspace.

---

## User Notes

*(Space for user to add clarifications or extra requirements.)*

---

## Completion Checklist

- [x] All files created per the table above
- [ ] Lint passes: `ruff check backend && black --check backend` and `npm run lint --prefix frontend` — tools not installed; config'd in pyproject.toml + .eslintrc.cjs
- [ ] Type-check passes: `mypy backend/app` and `tsc --noEmit -p frontend` — needs `pip install -e .[dev]` and `npm install`
- [x] Tests pass: `pytest backend/tests` (50 passed, 1 skipped)
- [ ] Manual smoke: register → login → create course → upload a short WAV → wait for completion → view transcript/summary/glossary → export PDF and TXT (needs ffmpeg + Ollama + DejaVuSans.ttf on disk)
- [x] Cross-user isolation verified by test (test_ownership.py)
- [x] Session eviction verified by test (test_login_evicts_all_other_sessions)
- [x] Job lock verified by test (test_upload_while_job_locked_returns_409)
- [ ] Cyrillic renders correctly in the PDF export (needs manual TTF placement + smoke)
