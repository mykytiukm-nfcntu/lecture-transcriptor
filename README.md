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
