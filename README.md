# Local Lecture Transcriptor

Local, single-machine web app that turns a pre-recorded lecture (MP3, WAV, or M4A) into a
transcript, a structured summary, and a glossary. All processing is offline: ASR runs
via `faster-whisper` (CTranslate2) and generation runs against a local Ollama model.

## Prerequisites

- **Python** 3.11 or newer
- **Node.js** 20 or newer
- **ffmpeg** on `PATH` (used for audio duration probing and normalisation)
- **Ollama** installed and running, with the target model pulled once:
  ```
  ollama pull gemma4:12b
  ```
- **DejaVu Sans TTF** placed at `backend/app/assets/fonts/DejaVuSans.ttf`. This font
  is required by ReportLab to render Cyrillic in the PDF export. Download from
  <https://dejavu-fonts.github.io/> (Free/Other → DejaVu Sans package) and copy the
  `DejaVuSans.ttf` file into the path above. The application will not start until
  the file is present.

## Environment configuration

Backend and frontend each keep their own `.env.example`. Copy them locally before
running anything:

```
cp backend/.env.example backend/.env
```

The frontend gets its own `.env.example` in a later step; for now the Vite dev
server needs no environment variables. The repository root `.env.example` is only a
pointer — there is no shared root `.env`.

### Backend environment variables

| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `local` | Free-form environment label. |
| `DATABASE_URL` | `sqlite:///./storage/app.db` | SQLite file, created on first start. |
| `STORAGE_ROOT` | `./storage` | Root directory for the DB and media. |
| `MEDIA_SUBDIR` | `media` | Subdirectory of `STORAGE_ROOT` for uploads. |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama daemon URL. |
| `OLLAMA_MODEL` | `gemma4:12b` | Model name; must be pulled beforehand. |
| `WHISPER_MODEL_SIZE` | `medium` | `tiny \| base \| small \| medium \| large-v3`. Tests may use `tiny`. |
| `WHISPER_DEVICE` | `auto` | `cpu \| cuda \| auto`. |
| `WHISPER_COMPUTE_TYPE` | `int8` | Safe CPU default; `float16` is picked when `auto` selects a GPU. |
| `WHISPER_VAD` | `true` | Enable Silero VAD (drops silences). |
| `WHISPER_FALLBACK_LANGUAGE` | `uk` | Language used when auto-detect confidence is too low. |
| `WHISPER_LANGUAGE_DETECT_MIN_PROB` | `0.5` | Confidence threshold for language detection. |
| `LLM_TIMEOUT_SECONDS` | `120` | Per-call Ollama HTTP timeout. |
| `LLM_MAX_RETRIES` | `2` | JSON-parse/validation retries per LLM call. |
| `CHUNK_TOKENS` | `1800` | Approximate tokens per transcript chunk. |
| `CHUNK_OVERLAP_TOKENS` | `200` | Overlap between adjacent chunks. |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allow-list of origins. |
| `LOG_LEVEL` | `INFO` | Root log level (`DEBUG \| INFO \| WARNING \| ERROR`). |

## Local development

### Backend

```
cd backend
python -m venv .venv
.venv\Scripts\Activate.ps1     # PowerShell
# or: .venv\Scripts\activate.bat on cmd
# or: source .venv/bin/activate on macOS / Linux
pip install -e .[dev]
uvicorn app.main:app --reload --port 8000
```

### Frontend

```
cd frontend
npm install
npm run dev
```

The Vite dev server proxies API requests to the backend on port 8000. Open
<http://localhost:5173> in a browser and register a user to get started.

## Repository layout

```
backend/    FastAPI + faster-whisper + Ollama pipeline
frontend/   React + Vite + Tailwind SPA
storage/    Runtime data (DB and uploaded media) — gitignored
tasks/      Task planning documents
```

## Recovery flow

If the pipeline fails partway through (e.g. Ollama restarts between the transcribe
and summary stages, or the machine reboots mid-run), the lecture ends in `failed`
but its partial artifacts are preserved:

- The normalized audio, the transcript, and (if the failure happened after the summary
  stage) the summary all remain readable on the lecture viewer page.
- If the failure happened AFTER the transcript committed, a **Retry** button appears
  on the failed banner. Clicking it resumes the pipeline from the first uncommitted
  stage — the transcribe stage is not re-run, the summary is regenerated only if it
  was not yet committed, and the glossary is always regenerated last.
- Lectures that failed BEFORE the transcribe stage committed (or that failed under a
  version of the app older than this feature) do not show the Retry button. Delete and
  re-upload is the only recovery path for those.

A retry is subject to the same single-job lock as an upload — the endpoint returns
`409` when another transcription is already running.

**Partial exports.** The TXT and PDF download buttons appear the moment the transcript
is available — you do not have to wait for the LLM stage to finish. A file downloaded
mid-run contains whatever sections had already committed (always the transcript, plus
the summary once it commits); the glossary is only included after the pipeline reaches
`completed`. A small "Часткова версія" hint next to the export buttons flags when the
file is not the final study package.

## Manual smoke checklist

Cover the essential paths after any pipeline-related change:

1. **Happy path.** Register → login → create course → upload a short WAV → wait for
   completion → view transcript / summary / glossary → export PDF and TXT.
2. **Cross-user isolation.** Register a second user → confirm they cannot see the
   first user's course or lecture (URL guess returns 404, not 403).
3. **Single-job lock.** During a running transcription, attempting a second upload
   returns `409` at the API and shows a disabled upload form on the frontend.
4. **Retry after summary failure.** Start a transcription of a short WAV → stop
   Ollama once the transcript tab appears (i.e. `status=generating`) → wait for the
   lecture to move to `failed` → confirm the transcript is still readable → restart
   Ollama → click **Retry** on the failed banner → observe the lecture return to
   `queued`, then `generating`, then `completed`. The transcript row is NOT
   regenerated; only summary + glossary are.
5. **Server restart mid-run.** Start a transcription → kill the backend once the
   transcript has committed → restart the backend → confirm the lecture is now
   `failed` with the "Server restarted while processing" copy AND the Retry button
   is visible → click Retry → observe completion.
6. **Grandfathered failed row.** A lecture that failed BEFORE this feature shipped
   (with `last_completed_stage IS NULL`) shows the delete-and-re-upload copy and
   NOT the Retry button — the existing recovery UX is preserved.
