import { forwardRef } from 'react';

interface AudioPlayerProps {
  src: string | null;
}

/**
 * Thin wrapper around a native `<audio>` element that forwards its DOM ref to
 * the parent so callers can seek programmatically (`audioRef.current.currentTime = ...`).
 *
 * When `src` is null (still loading / not available) renders a skeleton block.
 */
export const AudioPlayer = forwardRef<HTMLAudioElement, AudioPlayerProps>(
  function AudioPlayer({ src }, ref) {
    if (src === null) {
      return (
        <div
          className="h-14 w-full animate-pulse rounded bg-slate-200"
          role="status"
          aria-label="Завантаження аудіо"
        />
      );
    }
    return (
      <audio
        ref={ref}
        controls
        preload="metadata"
        src={src}
        className="w-full"
        aria-label="Аудіо лекції"
      />
    );
  },
);
