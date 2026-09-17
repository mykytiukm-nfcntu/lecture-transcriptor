"""Reproduce the backend's ffmpeg/ffprobe invocations to isolate a silent normalize failure.

Run with:  python scripts/diagnose_ffmpeg.py

The script writes tiny 1-second silent WAV inputs, then calls ffmpeg exactly the way
`backend/app/services/media.py` does — `subprocess.run(...)` for the batch path and
`subprocess.Popen(... -progress pipe:1 -nostats ...)` for the streaming path — under both
ASCII and Cyrillic directory names. It prints exit codes, timings, and the first ~40
lines of stderr for each case. No dependencies beyond the stdlib.
"""

from __future__ import annotations

import io
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path


def hr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def head(title: str) -> None:
    print()
    print("-- " + title)


def make_silent_wav(dest: Path, seconds: float = 1.0, sample_rate: int = 16000) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    frames = int(seconds * sample_rate)
    with wave.open(str(dest), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * frames)


def run_batch(cmd: list[str], label: str) -> int:
    """Mirror `media.probe_duration` / `media.normalise` batch path."""
    head(label)
    print("cmd:", subprocess.list2cmdline(cmd))
    start = time.perf_counter()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        print("FileNotFoundError:", exc)
        return -1
    except Exception as exc:  # noqa: BLE001
        print(f"unexpected {type(exc).__name__}: {exc}")
        return -1
    elapsed = time.perf_counter() - start
    print(f"exit={result.returncode}  elapsed={elapsed:.2f}s")
    if result.stdout.strip():
        print("stdout (first 20 lines):")
        for ln in result.stdout.splitlines()[:20]:
            print("  " + ln)
    if result.stderr.strip():
        print("stderr (first 40 lines):")
        for ln in result.stderr.splitlines()[:40]:
            print("  " + ln)
    return result.returncode


def run_stream(cmd: list[str], label: str, timeout_seconds: float = 30.0) -> int:
    """Mirror `media.normalise`'s streaming Popen path (with a diagnostic timeout)."""
    head(label)
    print("cmd:", subprocess.list2cmdline(cmd))
    start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as exc:
        print("FileNotFoundError:", exc)
        return -1
    except Exception as exc:  # noqa: BLE001
        print(f"unexpected {type(exc).__name__}: {exc}")
        return -1

    progress_lines = 0
    deadline = start + timeout_seconds
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            if time.perf_counter() > deadline:
                print(f"TIMEOUT after {timeout_seconds:.0f}s waiting for -progress output")
                proc.kill()
                break
            if line.startswith("out_time_us="):
                progress_lines += 1
        rc = proc.wait(timeout=max(1.0, deadline - time.perf_counter()))
    except subprocess.TimeoutExpired:
        print("wait() timed out; killing process")
        proc.kill()
        rc = proc.wait()
    elapsed = time.perf_counter() - start
    stderr_text = ""
    if proc.stderr is not None:
        try:
            stderr_text = proc.stderr.read() or ""
        except Exception:  # noqa: BLE001
            pass
    print(f"exit={rc}  elapsed={elapsed:.2f}s  progress_updates={progress_lines}")
    if stderr_text.strip():
        print("stderr (first 40 lines):")
        for ln in stderr_text.splitlines()[:40]:
            print("  " + ln)
    return rc


def normalise_cmd(path_in: Path, path_out: Path, *, streaming: bool) -> list[str]:
    cmd: list[str] = [
        "ffmpeg", "-y",
        "-i", str(path_in),
        "-ac", "1",
        "-ar", "16000",
        "-af", "loudnorm",
    ]
    if streaming:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += [str(path_out)]
    return cmd


def probe_cmd(path: Path) -> list[str]:
    return [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]


def main() -> int:
    hr("ENVIRONMENT")
    print("Python:            ", sys.version.replace("\n", " "))
    print("Executable:        ", sys.executable)
    print("Platform:          ", platform.platform())
    print("OS release:        ", platform.release())
    print("Default encoding:  ", sys.getdefaultencoding())
    print("FS encoding:       ", sys.getfilesystemencoding())
    print("PYTHONIOENCODING:  ", os.environ.get("PYTHONIOENCODING", "<unset>"))
    print("CWD:               ", Path.cwd())
    print("ffmpeg on PATH:    ", shutil.which("ffmpeg") or "<NOT FOUND>")
    print("ffprobe on PATH:   ", shutil.which("ffprobe") or "<NOT FOUND>")
    print("PATH entries (first 12):")
    for i, p in enumerate((os.environ.get("PATH") or "").split(os.pathsep)[:12]):
        print(f"  {i:2d}: {p}")

    hr("BASIC INVOCATION FROM PYTHON")
    run_batch(["ffmpeg", "-version"], "ffmpeg -version (batch)")
    run_batch(["ffprobe", "-version"], "ffprobe -version (batch)")

    with tempfile.TemporaryDirectory(prefix="iscm_ffmpeg_diag_") as tmp_str:
        tmp = Path(tmp_str)
        cases: list[tuple[str, Path]] = [
            ("ASCII path", tmp / "ascii" / "1" / "1"),
            ("Cyrillic path (Коледж)", tmp / "Коледж" / "1" / "1"),
        ]

        for label, case_dir in cases:
            hr(f"CASE: {label}")
            src = case_dir / "original.wav"
            dst = case_dir / "normalized.wav"
            try:
                make_silent_wav(src)
            except Exception as exc:  # noqa: BLE001
                print(f"Could not create input WAV at {src}: {exc}")
                continue
            print("input:  ", src, "(exists:", src.exists(), ")")
            print("output: ", dst)

            # Same order as media.py: probe_duration → normalise (batch) → normalise (streaming)
            run_batch(probe_cmd(src), "probe_duration (subprocess.run)")

            rc = run_batch(normalise_cmd(src, dst, streaming=False), "normalise batch (subprocess.run)")
            if rc == 0:
                print(f"  → wrote {dst.stat().st_size} bytes")
                dst.unlink(missing_ok=True)

            rc = run_stream(normalise_cmd(src, dst, streaming=True), "normalise streaming (subprocess.Popen -progress pipe:1)")
            if rc == 0:
                print(f"  → wrote {dst.stat().st_size} bytes")

    hr("DONE")
    print("If every case exits 0, ffmpeg is fully reachable from a Python subprocess and")
    print("the backend's ffmpeg path is not the culprit. In that case, check:")
    print("  1. Is the worker thread actually processing? Look for 'Pipeline start' /")
    print("     'Pipeline completed' log lines in the backend output. If they never appear,")
    print("     the job never left the queue and the failure is upstream (auth, upload, DB).")
    print("  2. Is LOG_LEVEL <= INFO? A higher level hides both pipeline lifecycle and errors.")
    print("  3. Is the backend writing to a file you're not tailing (or to a different console)?")
    print("     Add `LOG_LEVEL=DEBUG` to backend/.env and restart to be sure.")
    print()
    print("If a case above exits non-zero or hangs, paste the section into chat and we")
    print("can narrow it further.")
    return 0


if __name__ == "__main__":
    # Ensure Unicode output survives the Windows console.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        # Fallback for very old Pythons or non-TextIO wrappers; not fatal.
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    raise SystemExit(main())
