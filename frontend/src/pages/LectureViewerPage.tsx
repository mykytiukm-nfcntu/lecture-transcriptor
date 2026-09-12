import type { ReactElement, ReactNode } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { ApiError } from '@/api/client';
import { createAudioObjectUrl, downloadExport, getLecture } from '@/api/lectures';
import { AudioPlayer } from '@/components/AudioPlayer';
import { GlossaryView } from '@/components/GlossaryView';
import { StatusBadge } from '@/components/StatusBadge';
import { SummaryView } from '@/components/SummaryView';
import { TranscriptView } from '@/components/TranscriptView';
import { useLectureStatus } from '@/hooks/useLectureStatus';
import type { ExportFormat } from '@/api/lectures';
import type { LectureDetail, LectureStatus } from '@/types/api';
import { formatDuration } from '@/utils/format';

type Tab = 'transcript' | 'summary' | 'glossary';

const IN_PROGRESS: readonly LectureStatus[] = [
  'queued',
  'normalizing',
  'transcribing',
  'generating',
];

export function LectureViewerPage(): ReactElement {
  const params = useParams<{ lectureId: string }>();
  const lectureId = params.lectureId !== undefined ? Number(params.lectureId) : Number.NaN;
  const validId = Number.isFinite(lectureId) && lectureId > 0;
  const queryClient = useQueryClient();

  const lectureQuery = useQuery<LectureDetail>({
    queryKey: ['lecture', lectureId],
    queryFn: () => getLecture(lectureId),
    enabled: validId,
  });

  const lecture = lectureQuery.data;
  const isCompleted = lecture?.status === 'completed';
  const isInProgress = lecture !== undefined && IN_PROGRESS.includes(lecture.status);
  const isFailed = lecture?.status === 'failed';

  // Poll status while the lecture is in progress and, on transition to a
  // terminal state, refresh the detail query so the tabs unlock.
  const statusQuery = useLectureStatus(lectureId, { enabled: validId && isInProgress });
  const polledStatus = statusQuery.data?.status;
  useEffect(() => {
    if (polledStatus === 'completed' || polledStatus === 'failed') {
      void queryClient.invalidateQueries({ queryKey: ['lecture', lectureId] });
    }
  }, [polledStatus, queryClient, lectureId]);

  // Audio object URL — fetched once when the lecture is completed, revoked on
  // unmount (or if the lecture id / completed flag changes).
  const [audioSrc, setAudioSrc] = useState<string | null>(null);
  const [audioError, setAudioError] = useState<string | null>(null);
  useEffect(() => {
    if (!isCompleted) {
      setAudioSrc(null);
      return;
    }
    let acquired: string | null = null;
    let cancelled = false;
    setAudioError(null);
    void createAudioObjectUrl(lectureId)
      .then((url) => {
        if (cancelled) {
          URL.revokeObjectURL(url);
          return;
        }
        acquired = url;
        setAudioSrc(url);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setAudioError(err instanceof Error ? err.message : 'Не вдалося завантажити аудіо.');
      });
    return () => {
      cancelled = true;
      if (acquired !== null) {
        URL.revokeObjectURL(acquired);
      }
    };
  }, [isCompleted, lectureId]);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const seekTo = useCallback((seconds: number): void => {
    const audio = audioRef.current;
    if (audio === null) return;
    audio.currentTime = Math.max(0, seconds);
    void audio.play().catch(() => {
      // Autoplay policies may reject a programmatic play; the user can press
      // the play control manually.
    });
  }, []);

  const [tab, setTab] = useState<Tab>('transcript');

  const exportMutation = useMutation<void, Error, ExportFormat>({
    mutationFn: (format) => downloadExport(lectureId, format),
  });
  const exportError: string | null =
    exportMutation.error instanceof ApiError
      ? exportMutation.error.message
      : exportMutation.error instanceof Error
        ? exportMutation.error.message
        : null;

  if (!validId) {
    return (
      <main className="mx-auto max-w-4xl space-y-2 p-6">
        <p className="text-status-failed">Некоректний ідентифікатор лекції.</p>
        <Link to="/courses" className="text-status-running hover:underline">
          ← Назад до курсів
        </Link>
      </main>
    );
  }

  if (lectureQuery.isLoading) {
    return <main className="mx-auto max-w-4xl p-6 text-slate-500">Завантаження лекції…</main>;
  }
  if (lectureQuery.isError || lecture === undefined) {
    return (
      <main className="mx-auto max-w-4xl space-y-2 p-6">
        <p className="text-status-failed" role="alert">
          {lectureQuery.error instanceof Error
            ? lectureQuery.error.message
            : 'Не вдалося завантажити лекцію.'}
        </p>
        <Link to="/courses" className="text-status-running hover:underline">
          ← Назад до курсів
        </Link>
      </main>
    );
  }

  const backLink = `/courses/${lecture.course_id}`;

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl flex-col gap-2 px-6 py-4">
          <Link to={backLink} className="text-sm text-status-running hover:underline">
            ← Назад до курсу
          </Link>
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-xl font-bold text-slate-900">{lecture.title}</h1>
            <StatusBadge lecture={lecture} />
          </div>
          <p className="text-sm text-slate-500">
            {formatDuration(lecture.duration_seconds)} ·{' '}
            {lecture.language ?? 'визначення мови…'} · файл {lecture.original_filename}
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        {isInProgress ? (
          <section className="space-y-3 rounded border border-slate-200 bg-white p-6 text-center shadow-sm">
            <h2 className="text-lg font-semibold text-slate-900">Обробка лекції</h2>
            <p className="text-sm text-slate-600">
              Ця сторінка оновиться автоматично, коли транскрипт буде готовий. Можна закрити вкладку — обробка триває у фоні.
            </p>
            {statusQuery.data?.status === 'generating' ? (
              <GenerationStageIndicator stage={statusQuery.data.progress_stage} />
            ) : (
              <ProgressBar percent={statusQuery.data?.progress_percent ?? null} />
            )}
            <div className="inline-flex">
              <StatusBadge lecture={lecture} />
            </div>
          </section>
        ) : isFailed ? (
          <section className="rounded border border-status-failed/40 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-status-failed">Помилка обробки</h2>
            {lecture.error_message !== null ? (
              <p className="mt-1 text-sm text-slate-700">{lecture.error_message}</p>
            ) : null}
            <p className="mt-2 text-sm text-slate-600">
              Видаліть цю лекцію та завантажте аудіо знову, щоб спробувати ще раз.
            </p>
          </section>
        ) : isCompleted ? (
          <>
            <section aria-label="Аудіоплеєр" className="space-y-2">
              {audioError !== null ? (
                <p className="text-sm text-status-failed" role="alert">
                  {audioError}
                </p>
              ) : null}
              <AudioPlayer ref={audioRef} src={audioSrc} />
            </section>

            <section aria-label="Експорт" className="flex flex-wrap items-center gap-2">
              <span className="text-sm text-slate-600">Експорт:</span>
              <button
                type="button"
                onClick={() => exportMutation.mutate('txt')}
                disabled={exportMutation.isPending}
                className="rounded border border-slate-300 bg-white px-3 py-1 text-sm hover:bg-slate-100 disabled:opacity-50"
              >
                Завантажити TXT
              </button>
              <button
                type="button"
                onClick={() => exportMutation.mutate('pdf')}
                disabled={exportMutation.isPending}
                className="rounded border border-slate-300 bg-white px-3 py-1 text-sm hover:bg-slate-100 disabled:opacity-50"
              >
                Завантажити PDF
              </button>
              {exportError !== null ? (
                <p className="text-sm text-status-failed" role="alert">
                  {exportError}
                </p>
              ) : null}
            </section>

            <section aria-label="Вміст лекції" className="space-y-3">
              <div
                role="tablist"
                aria-label="Вкладки лекції"
                className="flex gap-1 border-b border-slate-200"
              >
                <TabButton current={tab} value="transcript" onSelect={setTab}>
                  Транскрипт
                </TabButton>
                <TabButton current={tab} value="summary" onSelect={setTab}>
                  Конспект
                </TabButton>
                <TabButton current={tab} value="glossary" onSelect={setTab}>
                  Глосарій
                </TabButton>
              </div>
              <div>
                {tab === 'transcript' ? (
                  <TranscriptView lectureId={lectureId} onSeek={seekTo} />
                ) : tab === 'summary' ? (
                  <SummaryView lectureId={lectureId} onSeek={seekTo} />
                ) : (
                  <GlossaryView lectureId={lectureId} onSeek={seekTo} />
                )}
              </div>
            </section>
          </>
        ) : null}
      </main>
    </div>
  );
}

interface TabButtonProps {
  current: Tab;
  value: Tab;
  onSelect: (tab: Tab) => void;
  children: ReactNode;
}

function TabButton({ current, value, onSelect, children }: TabButtonProps): ReactElement {
  const active = current === value;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={() => onSelect(value)}
      className={`px-4 py-2 text-sm font-medium ${
        active
          ? 'border-b-2 border-status-running text-status-running'
          : 'text-slate-600 hover:text-slate-900'
      }`}
    >
      {children}
    </button>
  );
}

interface ProgressBarProps {
  percent: number | null;
}

function ProgressBar({ percent }: ProgressBarProps): ReactElement {
  const clamped = percent === null ? null : Math.max(0, Math.min(100, percent));
  const width = clamped ?? 0;
  return (
    <div className="mx-auto max-w-md space-y-1">
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-slate-200"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={clamped ?? undefined}
      >
        <div
          className={`h-full bg-status-running transition-[width] duration-500 ${
            clamped === null ? 'animate-pulse' : ''
          }`}
          style={{ width: `${clamped === null ? 100 : width}%` }}
        />
      </div>
      <p className="text-xs text-slate-500">
        {clamped === null ? 'Підготовка…' : `${clamped.toFixed(0)}% поточного етапу`}
      </p>
    </div>
  );
}

interface GenerationStageIndicatorProps {
  stage: string | null;
}

function GenerationStageIndicator({ stage }: GenerationStageIndicatorProps): ReactElement {
  const stepIndex = stage === 'summary' ? 0 : stage === 'glossary' ? 1 : -1;
  const steps: { key: 'summary' | 'glossary'; title: string; hint: string }[] = [
    {
      key: 'summary',
      title: 'Створення конспекту',
      hint: 'LLM формує ієрархічний конспект лекції з розділами, ключовими тезами та формулами.',
    },
    {
      key: 'glossary',
      title: 'Створення глосарію',
      hint: 'LLM виділяє 10–30 ключових термінів і формулює короткі визначення на основі лекції.',
    },
  ];
  const active = steps[stepIndex] ?? null;

  return (
    <div className="mx-auto max-w-md space-y-3">
      <div className="flex items-center justify-center gap-3">
        {steps.map((step, i) => {
          const isDone = i < stepIndex;
          const isActive = i === stepIndex;
          const circleClass = isDone
            ? 'bg-status-completed text-white'
            : isActive
              ? 'bg-status-running text-white animate-pulse'
              : 'bg-slate-200 text-slate-500';
          const labelClass = isActive
            ? 'text-status-running font-medium'
            : isDone
              ? 'text-status-completed'
              : 'text-slate-500';
          return (
            <div key={step.key} className="flex items-center gap-3">
              <div className={`flex items-center gap-2`}>
                <span
                  className={`inline-flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold ${circleClass}`}
                  aria-hidden="true"
                >
                  {isDone ? '✓' : i + 1}
                </span>
                <span className={`text-sm ${labelClass}`}>{step.title}</span>
              </div>
              {i < steps.length - 1 ? (
                <span aria-hidden="true" className="text-slate-300">
                  →
                </span>
              ) : null}
            </div>
          );
        })}
      </div>
      {active !== null ? (
        <p className="text-xs text-slate-500">
          Крок {stepIndex + 1} з {steps.length}: {active.hint}
        </p>
      ) : (
        <p className="text-xs text-slate-500">Готуємо LLM-запити…</p>
      )}
    </div>
  );
}
