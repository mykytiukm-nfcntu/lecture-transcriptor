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
  return date.toLocaleString('uk-UA');
}

/**
 * Ukrainian plural picker: `one` for 1, 21, 31, …; `few` for 2–4, 22–24, …;
 * `many` for 0, 5–20, 25–30, …
 */
export function ukPlural(n: number, one: string, few: string, many: string): string {
  const abs = Math.abs(n);
  const mod10 = abs % 10;
  const mod100 = abs % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}
