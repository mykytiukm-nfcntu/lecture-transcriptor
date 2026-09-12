import type { ReactElement } from 'react';
import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import { getGlossary } from '@/api/lectures';
import type { GlossaryResponse } from '@/types/api';
import { formatTimestamp, ukPlural } from '@/utils/format';

interface GlossaryViewProps {
  lectureId: number;
  onSeek: (seconds: number) => void;
}

export function GlossaryView({ lectureId, onSeek }: GlossaryViewProps): ReactElement {
  const { data, isLoading, isError, error } = useQuery<GlossaryResponse>({
    queryKey: ['glossary', lectureId],
    queryFn: () => getGlossary(lectureId),
  });

  // Sort by first-mention timestamp so entries follow the lecture's own
  // chronology rather than the alphabet — helpful when reviewing along with
  // the audio.
  const sorted = useMemo(() => {
    if (data === undefined) return [];
    return [...data.entries].sort((a, b) => a.first_mention_seconds - b.first_mention_seconds);
  }, [data]);

  if (isLoading) return <p className="text-slate-500">Завантаження глосарію…</p>;
  if (isError) {
    return (
      <p className="text-status-failed" role="alert">
        {error instanceof Error ? error.message : 'Не вдалося завантажити глосарій.'}
      </p>
    );
  }
  if (data === undefined || sorted.length === 0) {
    return <p className="text-slate-500">Глосарій недоступний.</p>;
  }

  const termsWord = ukPlural(sorted.length, 'термін', 'терміни', 'термінів');

  return (
    <div className="rounded border border-slate-200 bg-white p-4">
      <p className="mb-3 text-xs text-slate-500">
        Мова: {data.language} · {sorted.length} {termsWord} · Промпт: {data.prompt_version}
      </p>
      <dl className="space-y-4">
        {sorted.map((entry) => (
          <div key={entry.term} className="space-y-1">
            <dt className="flex items-baseline gap-2">
              <span className="text-base font-semibold text-slate-900">{entry.term}</span>
              <button
                type="button"
                onClick={() => onSeek(entry.first_mention_seconds)}
                className="font-mono text-xs text-status-running hover:underline focus:underline focus:outline-none"
                aria-label={`Перейти до першої згадки терміна ${entry.term}`}
              >
                [{formatTimestamp(entry.first_mention_seconds)}]
              </button>
            </dt>
            <dd className="text-sm text-slate-700">{entry.definition}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
