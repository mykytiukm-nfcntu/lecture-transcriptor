const pad2 = (n: number): string => (n < 10 ? `0${n}` : String(n));

/** Format a number of seconds as `HH:MM:SS`. Negative/invalid → `00:00:00`. */
export function formatTimestamp(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds >= 0 ? Math.floor(seconds) : 0;
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = safe % 60;
  return `${pad2(hours)}:${pad2(minutes)}:${pad2(secs)}`;
}

/**
 * Format a duration as `mm:ss` for durations under an hour, `HH:MM:SS` above.
 * Nullish or invalid inputs render as a hyphen placeholder.
 */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds) || seconds < 0) {
    return '--:--';
  }
  const safe = Math.floor(seconds);
  if (safe >= 3600) {
    return formatTimestamp(safe);
  }
  const minutes = Math.floor(safe / 60);
  const secs = safe % 60;
  return `${pad2(minutes)}:${pad2(secs)}`;
}

/** Render an ISO-8601 date string using the browser's locale. */
export function formatDateTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return date.toLocaleString();
}
