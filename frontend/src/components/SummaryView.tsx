import type { ReactElement } from 'react';
import { useQuery } from '@tanstack/react-query';

import { getSummary } from '@/api/lectures';
import type { SummaryHighlightKind, SummaryResponse, SummarySection } from '@/types/api';
import { formatTimestamp } from '@/utils/format';

interface SummaryViewProps {
  lectureId: number;
  onSeek: (seconds: number) => void;
}

function highlightIcon(kind: SummaryHighlightKind): string {
  switch (kind) {
    case 'definition':
      return '📘';
    case 'formula':
      return '∑';
    case 'example':
      return '💡';
    case 'key_point':
      return '⭐';
  }
}

function highlightLabel(kind: SummaryHighlightKind): string {
  switch (kind) {
    case 'definition':
      return 'визначення';
    case 'formula':
      return 'формула';
    case 'example':
      return 'приклад';
    case 'key_point':
      return 'ключова думка';
  }
}

interface SectionProps {
  section: SummarySection;
  depth: number;
  onSeek: (seconds: number) => void;
}

function Section({ section, depth, onSeek }: SectionProps): ReactElement {
  const headingClass = depth === 0 ? 'text-lg font-semibold' : 'text-base font-medium';
  const HeadingTag: 'h2' | 'h3' | 'h4' | 'h5' =
    depth === 0 ? 'h2' : depth === 1 ? 'h3' : depth === 2 ? 'h4' : 'h5';

  return (
    <section
      className={depth === 0 ? 'space-y-2' : 'space-y-2 border-l-2 border-slate-200 pl-4'}
    >
      <HeadingTag className={headingClass}>
        <button
          type="button"
          onClick={() => onSeek(section.timestamp_seconds)}
          className="inline-flex items-baseline gap-2 rounded text-left text-slate-900 hover:text-status-running focus:text-status-running focus:outline-none"
        >
          <span className="font-mono text-xs text-slate-500">
            [{formatTimestamp(section.timestamp_seconds)}]
          </span>
          <span>{section.heading}</span>
        </button>
      </HeadingTag>

      {section.bullets.length > 0 ? (
        <ul className="ml-6 list-disc space-y-1 text-sm text-slate-800">
          {section.bullets.map((bullet, i) => (
            <li key={i}>{bullet}</li>
          ))}
        </ul>
      ) : null}

      {section.highlights.length > 0 ? (
        <ul className="space-y-1">
          {section.highlights.map((highlight, i) => (
            <li key={i}>
              <button
                type="button"
                onClick={() => onSeek(highlight.timestamp_seconds)}
                className="flex w-full items-baseline gap-2 rounded px-2 py-1 text-left text-sm hover:bg-slate-100 focus:bg-slate-100 focus:outline-none"
              >
                <span aria-hidden="true" className="w-4 shrink-0 text-base">
                  {highlightIcon(highlight.kind)}
                </span>
                <span className="font-mono text-xs text-slate-500">
                  [{formatTimestamp(highlight.timestamp_seconds)}]
                </span>
                <span className="text-slate-800">{highlight.text}</span>
                <span className="sr-only"> ({highlightLabel(highlight.kind)})</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      {section.subsections.length > 0 ? (
        <div className="space-y-3">
          {section.subsections.map((sub, i) => (
            <Section key={i} section={sub} depth={depth + 1} onSeek={onSeek} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

export function SummaryView({ lectureId, onSeek }: SummaryViewProps): ReactElement {
  const { data, isLoading, isError, error } = useQuery<SummaryResponse>({
    queryKey: ['summary', lectureId],
    queryFn: () => getSummary(lectureId),
  });

  if (isLoading) return <p className="text-slate-500">Завантаження конспекту…</p>;
  if (isError) {
    return (
      <p className="text-status-failed" role="alert">
        {error instanceof Error ? error.message : 'Не вдалося завантажити конспект.'}
      </p>
    );
  }
  if (data === undefined) {
    return <p className="text-slate-500">Конспект недоступний.</p>;
  }

  return (
    <article className="space-y-4 rounded border border-slate-200 bg-white p-4">
      <header className="space-y-1 border-b border-slate-200 pb-2">
        <h2 className="text-xl font-semibold text-slate-900">{data.content.title}</h2>
        <p className="text-xs text-slate-500">
          Мова: {data.generation_language} · Промпт: {data.prompt_version}
        </p>
      </header>
      {data.content.sections.length === 0 ? (
        <p className="text-slate-500">Розділи не згенеровано.</p>
      ) : (
        <div className="space-y-4">
          {data.content.sections.map((section, i) => (
            <Section key={i} section={section} depth={0} onSeek={onSeek} />
          ))}
        </div>
      )}
    </article>
  );
}
