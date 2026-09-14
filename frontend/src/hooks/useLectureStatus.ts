import { useEffect, useRef } from 'react';
import { useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query';

import { getLectureStatus } from '@/api/lectures';
import type { LectureStatus, LectureStatusResponse, StageCheckpoint } from '@/types/api';

const IN_PROGRESS_STATUSES: readonly LectureStatus[] = [
  'queued',
  'normalizing',
  'transcribing',
  'generating',
];

export interface UseLectureStatusOptions {
  enabled?: boolean;
}

/**
 * Polls `/api/lectures/{id}/status` every 2s while the lecture is in a
 * non-terminal state; stops polling once status becomes `completed` or
 * `failed`.
 *
 * Additionally invalidates `['lecture', lectureId]` whenever the polled
 * `last_completed_stage` differs from the previous poll's value, so
 * consumers observe each freshly committed artifact (transcript on
 * `transcribe`, summary on `summary`, glossary on `glossary`) the instant
 * the checkpoint advances. Terminal-status invalidation remains the
 * responsibility of the caller.
 */
export function useLectureStatus(
  lectureId: number,
  options: UseLectureStatusOptions = {},
): UseQueryResult<LectureStatusResponse> {
  const enabled = options.enabled ?? true;
  const queryClient = useQueryClient();
  const previousCheckpointRef = useRef<StageCheckpoint | null>(null);
  const hasSeenFirstPollRef = useRef(false);

  const statusQuery = useQuery<LectureStatusResponse>({
    queryKey: ['lectures', lectureId, 'status'],
    queryFn: () => getLectureStatus(lectureId),
    enabled,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status !== undefined && IN_PROGRESS_STATUSES.includes(status)) {
        return 2000;
      }
      return false;
    },
  });

  // Reset the "first poll" tracking when the consumer swaps lecture ids so
  // the next id's initial observation is treated as a baseline, not a diff.
  useEffect(() => {
    hasSeenFirstPollRef.current = false;
    previousCheckpointRef.current = null;
  }, [lectureId]);

  // The first observed checkpoint is a baseline, not an advance: seeding the
  // ref lazily prevents a mount-time invalidation storm when revisiting a
  // lecture whose checkpoint already advanced past `null` (e.g. `transcribe`).
  // Only genuine poll-to-poll transitions trigger `['lecture', lectureId]`
  // invalidation. Terminal-status invalidation remains the caller's job.
  const polledCheckpoint = statusQuery.data?.last_completed_stage;
  useEffect(() => {
    if (polledCheckpoint === undefined) {
      return;
    }
    if (!hasSeenFirstPollRef.current) {
      previousCheckpointRef.current = polledCheckpoint;
      hasSeenFirstPollRef.current = true;
      return;
    }
    if (polledCheckpoint !== previousCheckpointRef.current) {
      previousCheckpointRef.current = polledCheckpoint;
      void queryClient.invalidateQueries({ queryKey: ['lecture', lectureId] });
    }
  }, [polledCheckpoint, lectureId, queryClient]);

  return statusQuery;
}
