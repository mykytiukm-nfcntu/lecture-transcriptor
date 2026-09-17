"""Dump the most recent lectures + surviving artifacts.

Run with:  python scripts/inspect_lectures.py [path\\to\\app.db]

If no path is given, the script tries `storage/app.db` first (repo-root default),
then `backend/storage/app.db` (if running from repo root), then `./app.db`.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def find_db(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    candidates = [
        Path("storage/app.db"),
        Path("backend/storage/app.db"),
        Path("app.db"),
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    print("Could not auto-locate app.db. Pass the path explicitly:")
    print("  python scripts/inspect_lectures.py C:\\path\\to\\storage\\app.db")
    sys.exit(2)


def main() -> int:
    db_path = find_db(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"DB: {db_path.resolve()}")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    print()
    print("== Last 5 lectures ==")
    rows = conn.execute(
        """
        SELECT id, user_id, course_id, title, status, last_completed_stage,
               error_message, started_at, finished_at, created_at
          FROM lectures
         ORDER BY id DESC
         LIMIT 5
        """
    ).fetchall()
    if not rows:
        print("  (no lectures yet)")
    for r in rows:
        print(f"  id={r['id']}  user={r['user_id']}  course={r['course_id']}  title={r['title']!r}")
        print(f"    status={r['status']}  checkpoint={r['last_completed_stage']}")
        print(f"    started={r['started_at']}  finished={r['finished_at']}  created={r['created_at']}")
        print(f"    error={r['error_message']!r}")

        # Which artifacts exist for this lecture?
        has_transcript = conn.execute(
            "SELECT 1 FROM transcripts WHERE lecture_id=? LIMIT 1", (r["id"],)
        ).fetchone() is not None
        has_summary = conn.execute(
            "SELECT 1 FROM summaries WHERE lecture_id=? LIMIT 1", (r["id"],)
        ).fetchone() is not None
        glossary_count = conn.execute(
            "SELECT COUNT(*) FROM glossary_terms WHERE lecture_id=?", (r["id"],)
        ).fetchone()[0]
        print(
            f"    transcript={has_transcript}  summary={has_summary}  "
            f"glossary_terms={glossary_count}"
        )
        print()

    print("== Distinct statuses across ALL lectures ==")
    for status, count in conn.execute(
        "SELECT status, COUNT(*) FROM lectures GROUP BY status"
    ):
        print(f"  {status:<12} {count}")

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
