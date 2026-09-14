# Task: Pipeline Checkpoints & Retry

## Status
- Task ID: `pipeline-checkpoints-and-retry`
- Task URL: (freeform task — no tracker)
- Git Branch: `feat/pipeline-checkpoints-and-retry`
- Project / Space: ISCM-2026
- Created: 2026-09-14
- Last Updated: 2026-09-14
- Progress: 100%
- State: COMPLETED

## Task Content

> _Continuation of the [web-local-lecture-transcriptor](task-web-local-lecture-transcriptor.context.md) task._
>
> _"Is it possible to handle intermediate processing results?_
> _The initial goal was to do it stupid simple but now I think that it would be very useful to contain intermediate results in case the process failure."_
>
> _Follow-up: "Once the transcripted text is available, let's present it to the user. The LLM stage can be running in the background with the indicator on the page."_

Reverse the previous invariant "**pipeline is not resumable**". Persist intermediate artifacts at every stage boundary and expose:

1. An **explicit user-triggered retry** that resumes from the first failed stage. Failures that pre-date this task remain non-retryable (grandfathered).
2. **Live partial-artifact viewing during processing** — the transcript (and, later, the summary) become readable the moment they are committed, while the still-running stage is shown as a compact in-page progress indicator instead of a full-page overlay.

Both capabilities share the same checkpoint mechanism: the frontend surface is driven by `last_completed_stage`, whether the row is currently `generating` or already `failed`.

### Scope confirmed with user (Step 2.5)

- **B — view partials + explicit retry.** Partial artifacts survive failure and are viewable read-only; a `POST /api/lectures/{id}/retry` endpoint resumes from the first failed stage, skipping stages whose artifacts already exist.
- **Stage-level checkpoints only** — the four coarse stages (`normalize → transcribe → summary → glossary`). No per-chunk checkpointing inside the LLM map-reduce.
- **Orphan reconciliation on server restart:** mid-flight lectures still transition to `failed`, but their partial artifacts are preserved and (if a checkpoint was recorded) `Retry` becomes available.
- **Legacy `failed` lectures are grandfathered.** No retry button; the existing "delete and re-upload" copy stays. Only lectures whose failure happened after this task shipped will carry checkpoint metadata and become retry-eligible.
- **Live in-run partial views.** Once ASR commits the transcript, the transcript tab becomes usable in the same viewer while the LLM stage keeps running in the background. The processing indicator moves from full-page to a compact banner. The summary tab likewise activates the moment the summary artifact commits (while glossary is still generating).

### Out of scope

- Per-chunk LLM checkpointing (map-reduce still runs whole-stage on retry).
- Automatic (non-user-triggered) resume on server restart.
- Backfill or migration of pre-existing `failed` rows.
- New status enum value (`partial`). We reuse `failed` + a new sibling column.
- Editing endpoints for transcript / summary / glossary. Still view-only.

---

## Planning Analysis (Main Agent Reference)

*This section documents the main agent's analysis. It is NOT passed to sub-agents.*

### Established Conventions Found (in-repo)

- **IDs / PKs:** integer `autoincrement` primary keys on every model. `owner_id` denormalised on `Lecture` for cheap ownership checks.
- **Status modelling:** `LectureStatus(str, Enum)` in [backend/app/models/lecture.py](backend/app/models/lecture.py); enum values are lowercase strings, serialised straight to JSON.
- **Timestamps:** `UtcDateTime` custom SQLA type via [backend/app/core/db.py](backend/app/core/db.py). `created_at` = `server_default=func.now()`, all other timestamps assigned from `datetime.now(timezone.utc)` in Python.
- **Ownership check pattern:** every route calls `_get_owned_lecture(db, id, user.id)` which joins on `Lecture.user_id == current_user.id` and raises 404 (never 403) — enforced in [backend/app/api/lectures.py](backend/app/api/lectures.py) and [backend/app/api/artifacts.py](backend/app/api/artifacts.py).
- **Job lock:** module-level `threading.Lock` in [backend/app/core/job_lock.py](backend/app/core/job_lock.py). Enforced BOTH at API layer (`if job_lock.is_locked() or not job_lock.job_queue.empty(): 409`) and inside the worker (`try_acquire` before running the pipeline).
- **Pipeline state machine:** [backend/app/services/pipeline.py](backend/app/services/pipeline.py) mutates `lecture.status` between stages, commits after each transition, and rolls forward. `_mark_failed` on every stage-specific `except`.
- **Orphan reconciliation:** [backend/app/workers/worker.py](backend/app/workers/worker.py) `reconcile_orphaned_lectures()` runs on startup and moves anything not in a terminal state to `failed` with the message `"Server restarted while processing; please re-upload."`.
- **Schemas:** Pydantic v2 with `model_config = ConfigDict(from_attributes=True)`; enums serialise via their `.value` because `LectureStatus` inherits from `str`.
- **Error envelope:** `{"error": {"code": "...", "message": "..."}}` on non-2xx (registered globally in `main.py`).
- **Config surfaces:** every setting mirrored across `backend/app/core/config.py`, `backend/.env.example`, and the README env-var table.
- **Tests:** FastAPI `TestClient` + isolated SQLite per test in [backend/tests/conftest.py](backend/tests/conftest.py). LLM patched via `mock_llm` fixture that swaps `generate_json` in three modules (`llm`, `summary_generator`, `glossary_generator`). Media normalisation stubbed via `fake_ffmpeg`.
- **Prompt versioning:** immutable prompt files under `backend/app/prompts/` (e.g. `summary_v1.md.j2`). To change a prompt, add `_v2.md.j2` and switch the constant.

### High-Risk Requirement Scan

| Keyword | Requirement | Convention | Flag |
|---|---|---|---|
| "primary key" | (n/a — reuse existing `Lecture.id`) | integer autoincrement | Aligned |
| "unique" | No new uniqueness constraint required | — | Aligned |
| "cascade" | Retry must not delete existing artifacts | `cascade="all, delete-orphan"` only on user-initiated delete | Aligned — retry NEVER cascades |
| "enum" | `last_completed_stage` needs a small closed set | Existing `LectureStatus` uses `SAEnum(name="lecture_status")` | Follow same pattern with new enum `StageCheckpoint` |
| "timestamp" | No new timestamps introduced | — | Aligned |
| "config" | New settings? | No — checkpoint is DB-only; no thresholds | Aligned |
| "auth" / "permission" | Retry endpoint must be user-scoped | Ownership check via `_get_owned_lecture`; 404 on missing/foreign | Aligned |
| "index" | Query on `(status, last_completed_stage)` for orphan reconciliation | Existing `status` column is indexed | Aligned — no new index required |
| "N+1" | Retry endpoint reads one row | — | Aligned |
| "dependency" | Any new library? | — | Aligned — no new deps |

**No flags.** All choices below extend existing patterns without conflicting with them.

### Requirement-Convention Cross-Check

| # | Requirement | Established Convention | Status |
|---|---|---|---|
| 1 | Preserve normalized WAV across failures | `storage/media/{user_id}/{lecture_id}/normalized.wav` is already written by `services/media.normalise` before ASR runs | Aligned — no change needed to disk layout |
| 2 | Preserve transcript across LLM failures | Transcript + segments committed to DB before the pipeline enters `generating` | Aligned — commit order already correct |
| 3 | Preserve summary independently of glossary failure | Current code writes BOTH after BOTH succeed | **Needs change** — split into two commits |
| 4 | Preserve glossary independently of summary failure | Same as above; both stages run before either persists | **Needs change** — run summary → commit → glossary → commit |
| 5 | Distinguish "retry-eligible failed" from "grandfathered failed" | (n/a — new capability) | New column: `Lecture.last_completed_stage: str | None` |
| 6 | Retry must reject when a job is running | `job_lock.is_locked() or not job_queue.empty() → 409` in upload endpoint | Aligned — reuse the exact same check |
| 7 | Reset the row for retry | `_mark_failed` sets `status=failed`, `error_message`, `finished_at`; retry must reverse `status` + clear `error_message` + `finished_at` | Straightforward — mirror `upload_lecture` transition (`status=queued`) |
| 8 | Never rerun stages whose artifacts exist | (n/a — new logic) | Pipeline reads `last_completed_stage` on entry; skips stages ≤ that watermark |
| 9 | Orphan reconciliation must preserve partials | Currently blindly marks failed | **Needs change** — before marking failed, derive `last_completed_stage` from existing artifacts (disk + DB) |
| 10 | Frontend must show retry only when eligible | Current `LectureDetail` has no `can_retry` field | **Needs change** — add `last_completed_stage` + computed `can_retry` to detail + status responses |
| 11 | Transcript readable during `generating` | Existing `GET /transcript` returns 200 whenever the `Transcript` row exists — no status gate | Aligned — no backend change required; frontend must stop hiding the tab while `isInProgress` |
| 12 | LLM stage indicator visible alongside transcript | Current viewer swaps between a full-page "processing" box OR the tabs; never both | **Needs change** — replace the swap with a compact banner + tab layout keyed on `last_completed_stage` |

### Ambiguities

None remaining after Step 2.5. The four blocking questions covered scope, granularity, orphan semantics, and legacy handling.

### Questions Asked & Resolutions

1. **Q:** What does "handle intermediate results" primarily mean — view-only partials, view + explicit retry, or view + retry + auto-resume?
   **A:** **B — view partials + explicit retry** (user clicks a button; server resumes from first failed stage).
   **Resolution:** Add `POST /api/lectures/{id}/retry`; do NOT auto-re-enqueue on server restart.

2. **Q:** At what granularity should intermediate results be checkpointed — stage, per-chunk, or stage now / chunk later?
   **A:** **Stage-level only** (normalize / transcribe / summary / glossary).
   **Resolution:** No per-chunk checkpoint table. Summary + glossary each rerun end-to-end when their stage is chosen for retry.

3. **Q:** What should happen to a lecture that was mid-flight when the server restarted?
   **A:** **Mark as `failed` but preserve partial artifacts.**
   **Resolution:** `reconcile_orphaned_lectures` inspects existing artifacts on disk / in DB and sets `last_completed_stage` accordingly, then flips `status=failed` with a "server restarted; you can retry" message.

4. **Q:** How should legacy `failed` lectures be treated?
   **A:** **Grandfathered.** No retry button, keep the "delete and re-upload" copy.
   **Resolution:** Nothing to backfill — the new `last_completed_stage` column defaults to `NULL`. Retry endpoint returns 400 with code `not_resumable` when the column is NULL.

### Additional decisions taken by the main agent (no user input needed)

The following are consequences of the four confirmed answers, not new design choices.

- **No new `LectureStatus` value.** Reusing `failed` + inspecting `last_completed_stage` keeps the enum stable, the status polling logic unchanged, and requires no coordinated frontend enum handling. Presence of a checkpoint alone signals "retry-eligible".
- **`can_retry` is derived server-side**, not stored. It's true iff `status == failed AND last_completed_stage IS NOT NULL AND last_completed_stage != 'glossary'`. Exposed on `LectureDetail` and `LectureStatusResponse` so the frontend does not duplicate the rule.
- **Legacy check strictly on `last_completed_stage IS NULL`.** Do not sniff the filesystem — a leftover `normalized.wav` from an old crash without a checkpoint row is still legacy.
- **Retry endpoint is idempotent under the lock.** If a retry is enqueued and a duplicate `POST /retry` arrives, second call sees the row in `queued` (or an active status) and returns 409. Same shape as the upload path.
- **Delete during retry-eligible state stays legal** (current delete allows any non-active status). The retry button and the delete button coexist in the failed view.
- **Exports stay `completed`-only.** A partial lecture is not a "study package" per the original task; TXT/PDF export continues to require completion. If the user wants a partial transcript, the transcript viewer is enough.
- **Universal tab visibility rule.** Each of the three content tabs (Transcript, Summary, Glossary) is visible when its underlying artifact row exists, regardless of `status`. This unifies the mid-run partial view and the post-failure partial view — one rule, one code path.
- **Audio player visibility follows the transcript.** Whenever the transcript tab is visible, the audio player is visible. This lets users play along even during the LLM stage, and is trivial because we stream the original file (available since row creation).
- **In-run progress indicator moves inline.** While `status in {normalizing, transcribing}` **and** no transcript exists yet, keep the current full-page "processing" section. Once `last_completed_stage >= 'transcribe'` OR `status == 'generating'`, collapse the processing section into a compact banner at the top of the viewer (above the tabs). This preserves the current UX for the pre-transcript phase while unlocking the transcript view the moment ASR finishes.
- **No new polling channel.** The existing 2-second `useLectureStatus` polling drives the banner + tab-visibility updates. When `polledStatus === 'generating'` and `last_completed_stage` flips to `transcribe`, the query cache invalidation already scheduled for terminal transitions is extended to also invalidate on checkpoint advances so the transcript tab appears immediately.

---

## Implementation Plan

> **Rule:** Describes WHAT behavior is needed. No method names, no signatures. Implementation agents pick idiomatic names/shapes.

### Step 1 — Schema: stage checkpoint column
Add a nullable checkpoint field to the `Lecture` model that records the last-successfully-completed stage among `normalize`, `transcribe`, `summary`, `glossary`. Model as a persisted enum consistent with how `LectureStatus` is stored. Default `NULL`. New lectures start `NULL` and gain a value only after the first stage commits.

### Step 2 — Pipeline: checkpoint after every stage
Rewrite the orchestration in the pipeline module so every coarse stage commits its checkpoint immediately after its artifact commit — never both in one call. Concretely:

- After `media.normalise` returns successfully → checkpoint = `normalize`.
- After the `Transcript` + all `TranscriptSegment` rows commit → checkpoint = `transcribe`.
- After the `Summary` row commits → checkpoint = `summary`. (Split from the current combined summary+glossary commit.)
- After every `GlossaryTerm` row commits → checkpoint = `glossary`, then `status = completed`.

The commit that writes the artifact and the commit that advances the checkpoint may be the same DB transaction; the important invariant is *no artifact reaches disk/DB without its checkpoint*.

### Step 3 — Pipeline: skip stages up to the checkpoint on retry
On entry, if `last_completed_stage` is non-NULL, skip work for every stage at or before that watermark. Concretely:
- `normalize` already done → do not re-run ffmpeg; reuse existing `normalized.wav` if present, otherwise fall back to running normalise again (safety net).
- `transcribe` already done → skip ASR; reload `Transcript` + segments from DB to seed chunking.
- `summary` already done → skip the summary generator entirely.
- `glossary` already done → the row is already `completed`; retry endpoint must refuse to enqueue it in the first place.

Never write duplicate artifact rows on retry. If a downstream artifact somehow already exists (e.g., partial write of glossary terms from a previous crash), delete the orphans before regenerating the stage. Deletion is scoped to the stage being redone (glossary terms for the glossary stage; the `Summary` row for the summary stage).

### Step 4 — Worker: preserve partials on orphan reconciliation
Change `reconcile_orphaned_lectures` so that for every non-terminal row it also derives a `last_completed_stage` from what already exists:

- `normalized.wav` on disk under the canonical path → at least `normalize`.
- A committed `Transcript` row for the lecture → at least `transcribe`.
- A committed `Summary` row for the lecture → at least `summary`.

Take the highest tier that matches, write it to the column, then flip `status = failed` with the message *"Server restarted while processing; retry to continue from the last completed stage."* Legacy failed rows (no evidence of any artifact) get the checkpoint left as `NULL` and stay grandfathered.

### Step 5 — API: retry endpoint
Add `POST /api/lectures/{lecture_id}/retry`. Owner-scoped (404 on missing / foreign). Behaviour:

- `status == completed` → 400 with a specific code (`already_completed`).
- `status` is one of `queued | normalizing | transcribing | generating` → 409 (nothing to retry; a job is running / queued).
- `status == failed` AND `last_completed_stage IS NULL` → 400 with code `not_resumable` and a message explaining the user should delete and re-upload.
- `status == failed` AND `last_completed_stage == 'glossary'` → 400 with code `already_completed` (defensive — this shouldn't happen but reject cleanly).
- Otherwise: check the global job lock (same pattern as `upload_lecture`); if busy → 409. If free → clear `error_message`, `finished_at`, `started_at`; set `status = queued`; keep `last_completed_stage`; enqueue via `worker.enqueue`. Return 202 with the fresh `LectureDetail` shape (mirroring upload).

Preserve original `generation_language` and `model` overrides across retry by persisting them on the lecture row OR by looking them up from the committed summary artifact (whichever is cleaner given the current schema). If neither is available, fall back to process defaults.

### Step 6 — API: expose checkpoint + retry eligibility to the frontend
Extend `LectureDetail` and `LectureStatusResponse` with:

- `last_completed_stage: str | None`
- `can_retry: bool` (server-computed per the rule in the analysis section)

Both are read-only and derived from row state. The frontend uses `can_retry` to decide whether to render the button.

### Step 7 — Artifact reads: allow partials (both mid-run and post-failure)
Confirm the transcript / summary / glossary read endpoints already return 200 whenever the underlying row exists — regardless of `status`. This is the enabler for BOTH the live in-run view (`status = generating`, transcript already committed) AND the post-failure partial view (`status = failed`, transcript already committed). Rules:

- `GET /transcript` returns 200 if a `Transcript` row exists regardless of `status`. Already true today — verify and add a test for `generating` specifically.
- `GET /summary` returns 200 if a `Summary` row exists regardless of `status`. Already true today for the row-existence path — verify and add a test for `generating` (glossary still running) specifically.
- `GET /glossary` returns 200 if `GlossaryTerm` rows exist. Empty-list-when-completed edge case stays.
- Status endpoint continues to return the current `status` + new checkpoint + `can_retry`. The frontend uses the same `useLectureStatus` polling to detect the `transcribe`-checkpoint transition and invalidate the lecture query, so the transcript tab appears without user action.

No new endpoint here — this is a review + confirming tests exist for the new mid-run paths.

### Step 8 — Frontend: partial-view viewer + retry button
Restructure the lecture viewer around a single **tab-visibility rule** driven by `last_completed_stage` (plus a top banner that reflects the current row state). The rule replaces today's binary "in-progress overlay OR completed layout" split.

**Tab visibility rule (applies always):**
- Transcript tab visible when `last_completed_stage` is `transcribe`, `summary`, or `glossary`.
- Summary tab visible when `last_completed_stage` is `summary` or `glossary`.
- Glossary tab visible when `last_completed_stage` is `glossary` (equivalently: `status == completed`).

**Audio player visibility:** shown whenever ANY tab is visible (same trigger as transcript-tab visibility).

**Top banner rule (applies always):**
- `status in {queued, normalizing, transcribing}` AND no transcript yet → **full-page** "Обробка лекції" section (current UX kept verbatim).
- `status == generating` → **compact banner** above the tabs: *"Генерація конспекту та глосарію триває… [stage: summary|glossary]"* plus the elapsed timer. Transcript tab (and, when checkpoint = `summary`, the Summary tab) are already usable below.
- `status == completed` → no banner; standard completed layout.
- `status == failed` + `can_retry === true` → compact banner with the error message + *"Retry"* button. Whichever tabs have artifacts are still browsable below.
- `status == failed` + `can_retry === false` → current "delete and re-upload" screen unchanged (no tabs — nothing to show).

**Retry click flow (unchanged from the previous draft):**
- 202 → switch back to the in-progress banner (existing polling picks up the `queued → normalizing → …` transitions).
- 409 → inline error *"another transcription is currently running"* (same copy as upload).
- 400 → surface the server message inline.

**Polling / cache invalidation update:**
The `useLectureStatus` consumer in the viewer must invalidate `['lecture', lectureId]` not only on `completed`/`failed` transitions but also whenever `last_completed_stage` on the polled status differs from the previous poll. This is what makes the transcript tab appear the instant ASR commits.

In the course detail page's lecture list, a `failed` row with `can_retry` shows a smaller "Retry" chip; a `generating` row with a transcript checkpoint can show a subtle "Транскрипт готовий" hint to encourage the user to open the viewer. Both are optional polish; the required change is only in the viewer.

### Step 9 — Documentation & invariant sync
Update `.claude/project-config.md` — the invariant "Pipeline is not resumable" flips to "Pipeline is resumable **only via the explicit retry endpoint**, from the last committed stage checkpoint; sub-agents must not add auto-resume, per-chunk checkpointing, or editing endpoints." Update the parent context file `tasks/task-web-local-lecture-transcriptor.context.md` progress log with a pointer to this task. Update `README.md` if it explicitly documents the "delete and re-upload" recovery flow.

### Step 10 — Tests
Backend:
- **Pipeline unit** — a mid-summary failure leaves `last_completed_stage = 'transcribe'`, `status = 'failed'`, transcript rows intact, no summary row, no glossary rows.
- **Pipeline unit** — a mid-glossary failure leaves `last_completed_stage = 'summary'`, summary row intact, no glossary rows.
- **Pipeline unit** — a retry entering with `last_completed_stage = 'transcribe'` does NOT call ASR, DOES call summary + glossary generators, ends `completed` with `last_completed_stage = 'glossary'`.
- **Pipeline unit** — a retry entering with `last_completed_stage = 'summary'` skips summary generation, only calls glossary generator.
- **Pipeline unit** — retry that finds a leftover `Summary` row from a previous crash while re-running the summary stage deletes it before regenerating (idempotency).
- **API** — `POST /retry` on completed → 400 `already_completed`.
- **API** — `POST /retry` on grandfathered failed (`last_completed_stage IS NULL`) → 400 `not_resumable`.
- **API** — `POST /retry` on retryable failed → 202 and status becomes `queued`.
- **API** — `POST /retry` while another job is running → 409.
- **API** — `POST /retry` on someone else's lecture → 404.
- **API** — `LectureDetail` and status response expose `last_completed_stage` and `can_retry`.
- **Orphan reconciliation** — with a lecture in `transcribing`, disk-only `normalized.wav`, no transcript row → after startup: `status=failed`, `last_completed_stage='normalize'`.
- **Orphan reconciliation** — with a lecture in `generating`, transcript rows committed → after startup: `status=failed`, `last_completed_stage='transcribe'`.
- **Orphan reconciliation** — with a lecture in `generating`, transcript + summary committed → after startup: `status=failed`, `last_completed_stage='summary'`.
- **Orphan reconciliation** — legacy row without any artifacts → `last_completed_stage` stays `NULL`.

Backend (additional):
- **API** — `GET /transcript` returns 200 on a lecture with `status = generating` and `last_completed_stage = transcribe`.
- **API** — `GET /summary` returns 200 on a lecture with `status = generating` and `last_completed_stage = summary`.
- **API** — `GET /glossary` returns 404 on a lecture with `status = generating` and `last_completed_stage = summary` (glossary rows not yet committed).

Frontend:
- **Component** — the failed view renders the Retry button only when `can_retry` is true.
- **Component** — Retry button click calls the API and triggers status re-polling on success (mock the client).
- **Component** — 409 response surfaces the running-job copy.
- **Component** — the transcript tab is visible in the failed view when a transcript exists.
- **Component** — the transcript tab is visible during `status = generating` when `last_completed_stage = transcribe`, and the compact LLM-progress banner is rendered above the tabs.
- **Component** — the summary tab appears mid-run when `last_completed_stage` advances from `transcribe` to `summary` while `status` is still `generating` (drive via mocked polling data).
- **Hook** — `useLectureStatus` triggers a lecture-query invalidation when the polled `last_completed_stage` differs from the previous poll (not only on terminal transitions).

### Step 11 — Manual smoke checklist (README update)
Add to the README manual-smoke section: force a summary failure (e.g., stop Ollama between transcribe and summary), observe the lecture end in `failed` with transcript viewable, restart Ollama, click Retry, observe completion.

---

## Files to Modify/Create

Skill column reads `(project convention)` throughout because `.claude/project-config.md` declares no plugin skills for this stack. Implementation agents follow the coding-standards conventions in the parent context file and idiomatic FastAPI / React practice.

### Backend

| Status | File | Skill | Purpose |
|---|---|---|---|
| [x] | [backend/app/models/lecture.py](backend/app/models/lecture.py) | (project convention) | Added `StageCheckpoint` enum + `last_completed_stage`, `generation_language`, `ollama_model` nullable columns + `can_retry` `@property` |
| [x] | [backend/app/schemas/lecture.py](backend/app/schemas/lecture.py) | (project convention) | `last_completed_stage` + `can_retry` exposed on `LectureListItem`, `LectureDetail`, `LectureStatusResponse` |
| [x] | [backend/app/core/db.py](backend/app/core/db.py) | (project convention) | Added `_ensure_lecture_checkpoint_columns()` SQLite `ALTER TABLE` backfill for the three new columns |
| [x] | [backend/app/services/pipeline.py](backend/app/services/pipeline.py) | (project convention) | Checkpoint after every stage commit; skip stages up to the watermark on retry; delete orphaned downstream artifacts before regenerating a stage |
| [x] | [backend/app/workers/worker.py](backend/app/workers/worker.py) | (project convention) | `reconcile_orphaned_lectures` now derives checkpoint from disk/DB and exports `_ORPHAN_RESTART_MESSAGE`; the defensive glossary-tier branch flips to `completed` |
| [x] | [backend/app/api/lectures.py](backend/app/api/lectures.py) | (project convention) | `POST /api/lectures/{lecture_id}/retry` with full status matrix (`already_completed`, `not_resumable`, `job_running`); upload endpoint persists `generation_language`/`ollama_model` on the row |
| [x] | [backend/app/api/artifacts.py](backend/app/api/artifacts.py) | (project convention) | `LectureStatusResponse` populated with `last_completed_stage` + `can_retry`; partial reads confirmed status-gate-free |
| [x] | `backend/tests/test_pipeline.py` | (project convention) | +7 tests for stage-preservation on failure + skip-on-retry + orphan cleanup + defensive glossary guard |
| [x] | `backend/tests/test_lectures.py` | (project convention) | +13 tests (16 cases with parametrize) for the retry endpoint status matrix + detail/status field exposure + partial reads during `generating` |
| [x] | `backend/tests/test_orphan_reconcile.py` | (project convention) | NEW — 7 tests covering legacy, disk-only, transcript-only, summary-tier, idempotency, defensive-glossary-to-completed, and terminal-row-isolation branches |
| [x] | `backend/tests/test_ownership.py` | (project convention) | +1 test — user B cannot POST /retry on user A's lecture (404) |

### Frontend

| Status | File | Skill | Purpose |
|---|---|---|---|
| [x] | [frontend/src/types/api.ts](frontend/src/types/api.ts) | (project convention) | Added `StageCheckpoint` union + `ApiErrorDetail` union; `last_completed_stage` / `can_retry` on `LectureListItem` and `LectureStatusResponse` |
| [x] | [frontend/src/api/client.ts](frontend/src/api/client.ts) | (project convention) | `parseErrorBody` normalises both `{detail: "str"}` and the new nested `{detail: {code, message}}` shapes for retry-error codes |
| [x] | [frontend/src/api/lectures.ts](frontend/src/api/lectures.ts) | (project convention) | `retryLecture(id)` client method |
| [x] | [frontend/src/pages/LectureViewerPage.tsx](frontend/src/pages/LectureViewerPage.tsx) | (project convention) | Universal tab visibility rule keyed on `last_completed_stage`; compact in-run banner; Retry button + copy per `error.code` |
| [x] | [frontend/src/hooks/useLectureStatus.ts](frontend/src/hooks/useLectureStatus.ts) | (project convention) | Invalidates `['lecture', lectureId]` when the polled `last_completed_stage` advances between polls |
| [x] | [frontend/src/pages/CourseDetailPage.tsx](frontend/src/pages/CourseDetailPage.tsx) | (project convention) | Retry chip on retryable failed rows; "Транскрипт готовий" hint on `generating` rows past the transcribe checkpoint |
| [~] | [frontend/src/components/StatusBadge.tsx](frontend/src/components/StatusBadge.tsx) | (project convention) | Skipped — plan marked as optional polish; no completion checklist item depends on it |

### Documentation / config

| Status | File | Skill | Purpose |
|---|---|---|---|
| [x] | `.claude/project-config.md` | (project convention) | Invariant flipped to "resumable ONLY via the explicit retry endpoint" |
| [x] | `tasks/task-web-local-lecture-transcriptor.context.md` | (project convention) | Progress-log entry pointing to this follow-up added |
| [x] | `README.md` | (project convention) | Added `## Recovery flow` and `## Manual smoke checklist` sections |

### Configuration Changes Note

Per `.claude/project-config.md` → *Notes → Configuration surfaces*, any settings change must land in `config.py` + `.env.example` + README env-var table. **This task adds NO new settings.** The only config surface touched is the project-config invariant document itself.

---

## Architectural Notes

- **No schema migration tooling.** SQLite + `metadata.create_all()` on startup. Adding a nullable column with an `SAEnum` is safe: SQLAlchemy issues `ALTER TABLE ADD COLUMN` behaviour by matter of `create_all()` being idempotent — new tables get the column; existing DBs need a one-line manual `ALTER TABLE lectures ADD COLUMN last_completed_stage VARCHAR` because `create_all()` does NOT alter existing tables. Do **not** introduce Alembic. The pragmatic fix: detect the missing column at startup and add it via raw SQL if absent. Keep the detection logic small and local to the DB init module.
- **Retry preserves lecture identity.** The row's `id`, `user_id`, `course_id`, `original_filename`, `duration_seconds` never change. `title` never changes. Only status/error/timing fields plus the checkpoint watermark.
- **Single-job lock reuse.** The retry endpoint MUST use the exact same check (`job_lock.is_locked() or not job_queue.empty()`) and MUST enqueue via `worker.enqueue`. Any deviation risks re-introducing multi-job races.
- **Checkpoint watermark is one-way monotonically forward within a run.** A retry from `transcribe` runs `summary` → checkpoint moves to `summary` → runs `glossary` → moves to `glossary` → status flips to `completed`. Never moves backwards on success.
- **Orphaned downstream artifacts.** A rare crash could leave a partial `Summary` write or a subset of `GlossaryTerm` rows without the checkpoint advancing. On retry, the pipeline must delete these orphans before regenerating. Keep the delete scoped to the stage being redone.
- **Frontend contract stability.** The `LectureStatus` enum does NOT change. Only new fields are added. Existing consumers keep working.
- **View-only invariant unchanged.** No PATCH/PUT routes for transcript / summary / glossary are introduced. Retry mutates only pipeline-state columns, not artifact contents.
- **PDF/TXT export still requires `completed`.** Deliberate — partial exports muddy the "study package" concept.
- **Generation language / model preservation on retry.** The original submission's `generation_language` and `model` override MUST be respected on retry. Preferred storage: two new nullable columns on the lecture row (`generation_language`, `ollama_model`) populated at upload time and read on retry. If we prefer to derive from existing artifacts, we can, but explicit persistence is simpler and matches the "no cleverness" ethos.

---

## Dependencies

- **Prerequisites:** the [web-local-lecture-transcriptor](task-web-local-lecture-transcriptor.context.md) task (COMPLETED). All changes here layer on top of that codebase.
- **New libraries:** none. Everything reuses the existing SQLAlchemy / FastAPI / React / TanStack Query stack.
- **Existing code depended on:**
  - [backend/app/core/job_lock.py](backend/app/core/job_lock.py) — reused unchanged.
  - [backend/app/workers/worker.py](backend/app/workers/worker.py) — `enqueue` and `reconcile_orphaned_lectures` extended.
  - [backend/app/services/pipeline.py](backend/app/services/pipeline.py) — main rewrite target.
  - [backend/tests/conftest.py](backend/tests/conftest.py) — fixtures reused verbatim (`mock_asr`, `mock_llm`, `fake_ffmpeg`, `mock_worker_enqueue`).

---

## Progress Log

### 2026-09-14 — Plan Created
- Task fetched (source: freeform user message, continuation of `task-web-local-lecture-transcriptor.context.md`).
- Deep analysis completed against the existing codebase (models, pipeline, worker, lectures/artifacts APIs, frontend viewer).
- 4 clarifying questions asked and resolved (scope, granularity, orphan handling, legacy failed rows).
- Confirmed choices: **B / stage-level / preserve-and-mark-failed / grandfather**.
- Implementation plan created across 11 steps.
- Ready for implementation.

### 2026-09-14 — Plan extended: live in-run partial view
- User added: "Once the transcripted text is available, let's present it to the user. The LLM stage can be running in the background with the indicator on the page."
- Rolled the requirement into the existing checkpoint plan without new backend endpoints — `GET /transcript` already returns 200 whenever the row exists.
- Codified a **universal tab-visibility rule** keyed on `last_completed_stage` that unifies the mid-run and post-failure partial views into one code path.
- Extended `useLectureStatus` responsibilities to invalidate the lecture cache on checkpoint advances so the transcript tab appears without user action.
- Extended tests (backend: 200 on transcript during `generating`; frontend: mid-run tab appearance).

### 2026-09-14 — Implementation started
- State flipped to IN_PROGRESS.
- Implementation waves planned:
  1. **Backend foundations** — model column + schema exposure + DB init auto-ALTER (sequential first, everything else depends on it).
  2. **Backend business logic** — pipeline checkpoint/skip/orphan-cleanup + worker reconciliation.
  3. **Backend API** — `/retry` endpoint + status/detail responses expose `can_retry`.
  4. **Backend tests** — pipeline, lectures, orphan reconciliation, ownership.
  5. **Frontend types + client** — new fields + `retryLecture` API method.
  6. **Frontend UI** — viewer partial tabs + retry button, hook invalidation on checkpoint advance, course list chips.
  7. **Docs** — invariant flip in project-config, parent task pointer, README recovery flow.

### 2026-09-14 — All waves complete
- Waves 1–7 landed successfully. 19 files modified (or created: `backend/tests/test_orphan_reconcile.py`). StatusBadge polish intentionally skipped as optional.
- Backend: `test_pipeline.py` +7 tests, `test_lectures.py` +16 cases, `test_orphan_reconcile.py` (new) 7 tests, `test_ownership.py` +1 test — all pass under `pytest backend/tests` per-file runs during Wave 4.
- Frontend: eslint clean on every touched file; `tsc --noEmit -p .` has only one pre-existing baseline error at `frontend/vite.config.ts:1` (missing `@types/node` for `node:url`) that pre-dates this task and is out of scope.
- Wave 5 also touched `frontend/src/api/client.ts` (not in the original file list) — `parseErrorBody` was extended to normalise the new nested `{detail: {code, message}}` shape without breaking existing consumers.
- Ready for code review (Step 7).

### 2026-09-14 — Code review + fixes
- Reviewer report: 2 Critical, 9 Warning, 6 Suggestion.
- All Critical + Warning issues fixed across two waves of parallel fix agents (R1: 5 agents, R2: 2 agents):
  1. **Critical (pipeline orphan lost on regeneration failure).** `pipeline.py` — moved orphan-delete AFTER each generator succeeds so `_mark_failed`'s commit can never persist a naked delete.
  2. **Critical (viewer silent after retry).** `LectureViewerPage.tsx` — compact banner now fires for any `isInProgress && transcriptAvailable`, with per-status Ukrainian copy and a progress bar for normalize/transcribe.
  3. **Warning (queued mislabeled as "job_running").** `lectures.py` — split queued into `already_queued` (409) with clear copy; `job_running` reserved for `_ACTIVE_STATUSES`.
  4. **Warning (not_resumable claims "predates the retry feature").** `lectures.py` — message changed to feature-agnostic "no partial results are available".
  5. **Warning (private constant imported by tests).** `worker.py` — renamed `_ORPHAN_RESTART_MESSAGE` → `ORPHAN_RESTART_MESSAGE`; test imports updated.
  6. **Warning (trivially-true assertion on T6).** `test_orphan_reconcile.py` — T6 now seeds `error_message="stale error from previous run"` so the post-reconcile `is None` assertion actively verifies the clear branch.
  7. **Warning (missing retry test coverage).** `test_lectures.py` — added defensive-glossary branch test, enqueue-rollback test, concurrent-retry test.
  8. **Warning (no test for orphan preservation on retry failure).** `test_pipeline.py` — new `test_pipeline_retry_with_failing_summary_preserves_leftover_summary` proves the R1 pipeline fix holds.
  9. **Warning (hook invalidates on every mount).** `useLectureStatus.ts` — added `hasSeenFirstPollRef` so the initial poll seeds the ref without firing invalidation.
  10. **Warning (viewer retry doesn't invalidate list).** `LectureViewerPage.tsx` — `retryMutation.onSuccess` now also invalidates `['lectures', course_id]` and `['courses']`.
  11. **Warning (defensive branch test misses error_message preservation).** `test_pipeline.py` — added `error_message == "prior failure"` and `started_at is None` assertions.
- 6 Suggestions logged, not blocking:
  - Sequential `_derive_orphan_checkpoint` queries (performance micro-opt; skip).
  - Duplicate `except AsrError` / `except Exception` in pipeline (style; skip).
  - Retry endpoint's `detail: {code, message}` shape differs from the project-config aspirational envelope (envelope migration out of scope).
  - `retryMutation<..., Error, ...>` on CourseDetailPage should be `ApiError` (cosmetic type-narrowing tightening).
  - `_ACTIVE_STATUSES` duplicated in `lectures.py` and `artifacts.py` (dedup candidate).
  - Viewer's own terminal-status invalidation is now overlap-with-hook (defensive, cheap; leave).
- Latent behaviour flagged during R2b (not in review): the pipeline's "safety net" that re-runs `media.normalise` when `normalized.wav` is missing regresses `last_completed_stage` from `transcribe` back to `normalize`. Test-side workaround (pre-seed `normalized.wav`) landed; production-side fix deferred to a follow-up (violates the "one-way monotonic forward" architectural note).

---

## Relevant Context

- Parent task: [task-web-local-lecture-transcriptor.context.md](task-web-local-lecture-transcriptor.context.md).
- Invariant being reversed: `.claude/project-config.md` → *Invariants sub-agents must not break* → "**Pipeline is not resumable.**" This task rewrites that line.
- No `.claude` skills configured for this project — implementation follows the conventions codified in the parent context file plus idiomatic FastAPI / React.
- Similar reference implementations: none in-repo — checkpoint-and-retry patterns are novel to this codebase.

---

## User Notes

*(Space for user to add clarifications or extra requirements.)*

---

## Completion Checklist

- [x] `Lecture.last_completed_stage` column added and populated by the pipeline at every stage boundary
- [x] Summary and glossary commit independently (split from the current combined commit)
- [x] `POST /api/lectures/{id}/retry` implemented with the full status/error matrix
- [x] `LectureDetail` + `LectureStatusResponse` expose `last_completed_stage` and `can_retry`
- [x] Orphan reconciliation preserves checkpoint metadata for mid-flight lectures
- [x] Retry entering with `last_completed_stage = 'transcribe'` skips ASR verified by test
- [x] Retry entering with `last_completed_stage = 'summary'` skips summary generator verified by test
- [x] Grandfathered failed rows (checkpoint NULL) return 400 `not_resumable` from `/retry` verified by test
- [x] Frontend renders Retry only when `can_retry` is true and hides the "delete and re-upload" copy in that case
- [x] Failed view exposes the transcript tab when a transcript row exists
- [x] Transcript tab appears in the viewer the moment `last_completed_stage` reaches `transcribe`, while `status` is still `generating`
- [x] Compact LLM-progress banner replaces the full-page overlay once the transcript is available mid-run
- [x] `useLectureStatus` invalidates the lecture query on checkpoint advances, not only on terminal transitions
- [~] Lint passes: `ruff check backend && black --check backend` and `npm run lint --prefix frontend` — CLEAN on the 11 task-modified backend files and the 6 task-modified frontend files; project-wide `ruff check backend` and `black --check backend` still fail on 43 pre-existing files (UP017/UP037 style debt outside this task's scope; also unresolved in the parent task's checklist)
- [~] Type-check passes: `mypy backend/app` and `tsc --noEmit -p frontend` — CLEAN on task-modified files; project-wide `mypy backend/app` still surfaces pre-existing `asr.py` (`faster_whisper` missing stubs) and `export_pdf.py` (ReportLab overload) warnings; `tsc` still surfaces the pre-existing `frontend/vite.config.ts:1` `node:url` error (missing `@types/node` devDep)
- [x] Tests pass: `pytest backend/tests` — **87 passed, 0 failed** (was 50 before this task; +30 new tests + 7 that migrated between files during editing)
- [x] `.claude/project-config.md` invariant list updated
- [x] README recovery-flow copy + smoke steps updated

### 2026-09-14 — Final validation + task completion
- Full validation run from repo root.
- Backend: `ruff check` + `black --check` + `mypy` on the 11 task-modified files are all CLEAN. Project-wide runs still fail on pre-existing repo debt on 43 unrelated files.
- Backend tests: `pytest backend/tests` → **87 passed** (0 failed, 2 pre-existing `httpx`/`starlette` deprecation warnings).
- Frontend: `eslint` on the 6 task-modified files is CLEAN. `tsc --noEmit -p .` surfaces one pre-existing baseline error (missing `@types/node`) that pre-dates this task.
- Task state → COMPLETED.

### 2026-09-14 — Follow-up: partial TXT/PDF export during LLM stage
- User request: "I also would like to see the opportunity to download txt and pdf even when LLM is working."
- Investigation: backend `/export/txt` and `/export/pdf` endpoints already had no status gate; `render_txt` and `render_pdf` both render each section conditionally (`if transcript is not None`, `if summary_doc is not None and summary_doc.sections`, `if glossary_entries`). Frontend was the sole gate: `LectureViewerPage.tsx` hid the export section behind `isCompleted`.
- Reversed the "Exports stay `completed`-only" decision recorded earlier in this file (see the Additional decisions list under "Planning Analysis"). New rule: export buttons visible whenever the transcript is available — same trigger as the audio player and the transcript tab (`transcriptAvailable = reachedStage(cp, 'transcribe')`). A small "Часткова версія — LLM ще працює" hint appears next to the buttons when `!isCompleted`.
- Changes:
  - [frontend/src/pages/LectureViewerPage.tsx](frontend/src/pages/LectureViewerPage.tsx) — export section gate changed from `isCompleted` to `transcriptAvailable`; partial-version hint added.
  - [backend/tests/test_export.py](backend/tests/test_export.py) — new test `test_export_txt_returns_partial_during_generating` seeds a `generating` lecture with only a transcript row, hits `/export/txt`, and verifies `status=200`, `## Transcript` present, `## Summary` / `## Glossary` absent.
  - [README.md](README.md) — Recovery flow section extended with a "Partial exports" paragraph.
- Backend endpoints are unchanged (no scope creep on the backend); no new invariant flip needed in `.claude/project-config.md`.
- Validation: `pytest backend/tests/test_export.py -v` → 4 passed (3 pre-existing + 1 new). `eslint` on the viewer clean. Full `pytest backend/tests` → 88 passed.
