import { useEffect, useMemo, useState } from 'react';
import type { ReactElement } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { useLectureStatus } from '@/hooks/useLectureStatus';
import type { LectureListItem, LectureStatus } from '@/types/api';
import { formatDuration } from '@/utils/format';

interface StatusBadgeProps {
  lecture: LectureListItem;
}

interface Style {
  label: string;
  icon: string;
  bgClass: string;
  textClass: string;
}

const IN_PROGRESS: readonly LectureStatus[] = [
  'queued',
  'normalizing',
  'transcribing',
  'generating',
];

function styleFor(status: LectureStatus): Style {
  switch (status) {
    case 'queued':
      return {
        label: 'Queued',
        icon: '🕒',
        bgClass: 'bg-status-queued/15',
        textClass: 'text-status-queued',
      };
    case 'normalizing':
      return {
        label: 'Normalising',
        icon: '🎚',
        bgClass: 'bg-status-running/10',
        textClass: 'text-status-running',
      };
    case 'transcribing':
      return {
        label: 'Transcribing',
        icon: '🎙',
        bgClass: 'bg-status-running/10',
        textClass: 'text-status-running',
      };
    case 'generating':
      return {
        label: 'Generating notes',
        icon: '🧠',
        bgClass: 'bg-status-running/10',
        textClass: 'text-status-running',
      };
    case 'completed':
      return {
        label: 'Completed',
        icon: '✅',
        bgClass: 'bg-status-completed/10',
        textClass: 'text-status-completed',
      };
    case 'failed':
      return {
        label: 'Failed',
        icon: '❌',
        bgClass: 'bg-status-failed/10',
        textClass: 'text-status-failed',
      };
  }
}

function computeElapsedSeconds(startedAt: string | null): number | null {
  if (startedAt === null) return null;
  // A timezone-less ISO string is parsed as local time by Chromium; treat it as UTC.
  const iso = /[Zz]|[+-]\d{2}:?\d{2}$/.test(startedAt) ? startedAt : `${startedAt}Z`;
  const started = new Date(iso).getTime();
  if (Number.isNaN(started)) return null;
  return Math.max(0, Math.floor((Date.now() - started) / 1000));
}

/**
 * Pill-shaped status indicator for a lecture.
 *
 * When the lecture is in a non-terminal state, the badge attaches to the
 * shared `useLectureStatus` polling query so it can display a live-updating
 * elapsed timer (or a preliminary ETA while queued). When the polled status
 * transitions to a terminal state we invalidate the parent list + detail
 * queries so the row and viewer re-render automatically.
 */
export function StatusBadge({ lecture }: StatusBadgeProps): ReactElement {
  const queryClient = useQueryClient();
  const rowIsInProgress = IN_PROGRESS.includes(lecture.status);
  const statusQuery = useLectureStatus(lecture.id, { enabled: rowIsInProgress });

  const status: LectureStatus = statusQuery.data?.status ?? lecture.status;
  const startedAt: string | null = statusQuery.data?.started_at ?? lecture.started_at;
  const etaSeconds: number | null = statusQuery.data?.preliminary_eta_seconds ?? null;

  const isInProgress = IN_PROGRESS.includes(status);

  const [elapsed, setElapsed] = useState<number | null>(() => computeElapsedSeconds(startedAt));
  useEffect(() => {
    if (!isInProgress || startedAt === null) {
      setElapsed(null);
      return;
    }
    setElapsed(computeElapsedSeconds(startedAt));
    const interval = window.setInterval(() => {
      setElapsed(computeElapsedSeconds(startedAt));
    }, 1000);
    return () => {
      window.clearInterval(interval);
    };
  }, [isInProgress, startedAt]);

  useEffect(() => {
    if (!rowIsInProgress) return;
    if (status === 'completed' || status === 'failed') {
      void queryClient.invalidateQueries({ queryKey: ['lectures'] });
      void queryClient.invalidateQueries({ queryKey: ['lecture', lecture.id] });
    }
  }, [status, rowIsInProgress, queryClient, lecture.id]);

  const style = styleFor(status);

  const detail = useMemo<string | null>(() => {
    if (status === 'queued') {
      return etaSeconds !== null ? `ETA ${formatDuration(etaSeconds)}` : null;
    }
    if (isInProgress) {
      return elapsed !== null ? `${formatDuration(elapsed)} elapsed` : null;
    }
    return null;
  }, [status, isInProgress, elapsed, etaSeconds]);

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${style.bgClass} ${style.textClass}`}
      role="status"
      aria-label={`Status: ${style.label}${detail !== null ? `, ${detail}` : ''}`}
    >
      <span aria-hidden="true">{style.icon}</span>
      <span>{style.label}</span>
      {detail !== null ? <span className="opacity-75">· {detail}</span> : null}
    </span>
  );
}
