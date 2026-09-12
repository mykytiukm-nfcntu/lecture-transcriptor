import { useQuery, type UseQueryResult } from '@tanstack/react-query';

import { getLectureStatus } from '@/api/lectures';
import type { LectureStatus, LectureStatusResponse } from '@/types/api';

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
 */
export function useLectureStatus(
  lectureId: number,
  options: UseLectureStatusOptions = {},
): UseQueryResult<LectureStatusResponse> {
  const enabled = options.enabled ?? true;
  return useQuery<LectureStatusResponse>({
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
}
