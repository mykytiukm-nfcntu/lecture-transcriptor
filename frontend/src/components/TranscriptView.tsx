import type { ReactElement } from 'react';
import { useQuery } from '@tanstack/react-query';

import { getTranscript } from '@/api/lectures';
import type { TranscriptResponse } from '@/types/api';
import { formatTimestamp } from '@/utils/format';

interface TranscriptViewProps {
  lectureId: number;
  onSeek: (seconds: number) => void;
}

export function TranscriptView({ lectureId, onSeek }: TranscriptViewProps): ReactElement {
  const { data, isLoading, isError, error } = useQuery<TranscriptResponse>({
    queryKey: ['transcript', lectureId],
    queryFn: () => getTranscript(lectureId, {}),
  });

  if (isLoading) {
    return <p className="text-slate-500">Loading transcript…</p>;
  }
  if (isError) {
    return (
      <p className="text-status-failed" role="alert">
        {error instanceof Error ? error.message : 'Failed to load transcript.'}
      </p>
    );
  }
  if (data === undefined || data.segments.length === 0) {
    return <p className="text-slate-500">No transcript segments available.</p>;
  }

  return (
    <div className="rounded border border-slate-200 bg-white p-3">
      <p className="mb-2 text-xs text-slate-500">
        Language: {data.language} · {data.segments.length} segments
      </p>
      <ol className="max-h-[60vh] space-y-1 overflow-y-auto pr-1">
        {data.segments.map((segment) => (
          <li key={segment.index}>
            <button
              type="button"
              onClick={() => onSeek(segment.start_seconds)}
              className="block w-full rounded px-2 py-1 text-left text-sm hover:bg-slate-100 focus:bg-slate-100 focus:outline-none"
            >
              <span className="mr-2 font-mono text-xs text-slate-500">
                [{formatTimestamp(segment.start_seconds)}]
              </span>
              <span className="text-slate-800">{segment.text}</span>
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
