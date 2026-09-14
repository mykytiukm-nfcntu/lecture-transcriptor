import type { ReactElement, ReactNode } from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { ApiError } from '@/api/client';
import { createAudioObjectUrl, downloadExport, getLecture, retryLecture } from '@/api/lectures';
import { AudioPlayer } from '@/components/AudioPlayer';
import { GlossaryView } from '@/components/GlossaryView';
import { StatusBadge } from '@/components/StatusBadge';
import { SummaryView } from '@/components/SummaryView';
import { TranscriptView } from '@/components/TranscriptView';
import { useLectureStatus } from '@/hooks/useLectureStatus';
import type { ExportFormat } from '@/api/lectures';
import type { LectureDetail, LectureStatus, StageCheckpoint } from '@/types/api';
import { formatDuration } from '@/utils/format';

type Tab = 'transcript' | 'summary' | 'glossary';

const IN_PROGRESS: readonly LectureStatus[] = [
  'queued',
  'normalizing',
  'transcribing',
  'generating',
];

const STAGE_ORDER: readonly StageCheckpoint[] = ['normalize', 'transcribe', 'summary', 'glossary'];

// True when the pipeline has committed `target`'s stage (or a later one).
// `current === null` means no checkpoint yet — nothing has committed.
function reachedStage(current: StageCheckpoint | null, target: StageCheckpoint): boolean {
  if (current === null) return false;
  return STAGE_ORDER.indexOf(current) >= STAGE_ORDER.indexOf(target);
}

function tabsVisibleFor(checkpoint: StageCheckpoint | null): readonly Tab[] {
  const tabs: Tab[] = [];
  if (reachedStage(checkpoint, 'transcribe')) tabs.push('transcript');
  if (reachedStage(checkpoint, 'summary')) tabs.push('summary');
  if (reachedStage(checkpoint, 'glossary')) tabs.push('glossary');
  return tabs;
}

// Maps a retry-mutation failure to user-facing Ukrainian copy per the
// documented error-code contract. Falls through to `error.message` for any
// other error the client library populated.
function retryErrorCopy(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'job_running') {
      return 'Наразі виконується інша транскрипція. Зачекайте, поки вона завершиться.';
    }
    if (error.code === 'not_resumable') {
      return 'Ця лекція не підлягає повторній обробці. Видаліть і завантажте знову.';
    }
    if (error.code === 'already_completed') {
      return 'Лекція вже оброблена.';
    }
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Не вдалося перезапустити обробку.';
}

function generatingStageCopy(stage: string | null | undefined): string {
  if (stage === 'summary') return 'Створюємо конспект…';
  if (stage === 'glossary') return 'Створюємо глосарій…';
  return 'Готуємо LLM-запити…';
}

function inProgressCopy(status: LectureStatus, stage: string | null | undefined): string {
  if (status === 'queued') return 'У черзі…';
  if (status === 'normalizing') return 'Готуємо аудіо…';
  if (status === 'transcribing') return 'Транскрибуємо…';
  if (status === 'generating') return generatingStageCopy(stage);
  return 'Обробка триває…';
}

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
  const isInProgress = lecture !== undefined && IN_PROGRESS.includes(lecture.status);
  const checkpoint: StageCheckpoint | null = lecture?.last_completed_stage ?? null;
  const transcriptAvailable = reachedStage(checkpoint, 'transcribe');
  const visibleTabs = useMemo(() => tabsVisibleFor(checkpoint), [checkpoint]);

  // Poll status while the lecture is in progress. Cache invalidation on
  // checkpoint advance is Wave 6b's responsibility (inside useLectureStatus);
  // here we only invalidate on the terminal transitions to be safe if the hook
  // is still on the old contract.
  const statusQuery = useLectureStatus(lectureId, { enabled: validId && isInProgress });
  const polledStatus = statusQuery.data?.status;
  useEffect(() => {
    if (polledStatus === 'completed' || polledStatus === 'failed') {
      void queryClient.invalidateQueries({ queryKey: ['lecture', lectureId] });
    }
  }, [polledStatus, queryClient, lectureId]);

  // Audio object URL — fetched as soon as the transcript tab becomes visible
  // (so the player is available mid-run alongside the partial transcript);
  // revoked on unmount, id change, or if the transcript checkpoint disappears.
  const [audioSrc, setAudioSrc] = useState<string | null>(null);
  const [audioError, setAudioError] = useState<string | null>(null);
  useEffect(() => {
    if (!transcriptAvailable) {
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
  }, [transcriptAvailable, lectureId]);

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
  // Keep the selected tab in the visible set: if a tab disappears (shouldn't
  // happen backwards) or if the current selection isn't visible yet on first
  // render (e.g. transcript still committing), snap to the first visible tab.
  useEffect(() => {
    const first = visibleTabs[0];
    if (first === undefined) return;
    if (!visibleTabs.includes(tab)) {
      setTab(first);
    }
  }, [visibleTabs, tab]);

  const retryMutation = useMutation<LectureDetail, ApiError, void>({
    mutationFn: () => retryLecture(lectureId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['lecture', lectureId] });
      if (lecture !== undefined) {
        void queryClient.invalidateQueries({ queryKey: ['lectures', lecture.course_id] });
      }
      void queryClient.invalidateQueries({ queryKey: ['courses'] });
    },
  });
  const retryErrorMessage: string | null =
    retryMutation.error !== null ? retryErrorCopy(retryMutation.error) : null;

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
  const isCompleted = lecture.status === 'completed';
  const isFailed = lecture.status === 'failed';
  const preTranscriptPhase = isInProgress && !transcriptAvailable;
  const inProgressBannerVisible = isInProgress && transcriptAvailable;
  const retryBannerVisible = isFailed && lecture.can_retry;
  const legacyFailedVisible = isFailed && !lecture.can_retry;
  // Polled status is fresher than the cached lecture query during a retry.
  const effectiveStatus: LectureStatus = statusQuery.data?.status ?? lecture.status;
  const effectiveStage: string | null = statusQuery.data?.progress_stage ?? null;
  const inProgressShowProgress =
    effectiveStatus === 'normalizing' || effectiveStatus === 'transcribing';

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
        {preTranscriptPhase ? (
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
        ) : null}

        {legacyFailedVisible ? (
          <section className="rounded border border-status-failed/40 bg-white p-6 shadow-sm">
            <h2 className="text-lg font-semibold text-status-failed">Помилка обробки</h2>
            {lecture.error_message !== null ? (
              <p className="mt-1 text-sm text-slate-700">{lecture.error_message}</p>
            ) : null}
            <p className="mt-2 text-sm text-slate-600">
              Видаліть цю лекцію та завантажте аудіо знову, щоб спробувати ще раз.
            </p>
          </section>
        ) : null}

        {inProgressBannerVisible ? (
          <section
            aria-label="Триває обробка"
            className="flex flex-wrap items-center gap-3 rounded border border-status-running/30 bg-status-running/5 p-3 shadow-sm"
          >
            <span
              aria-hidden="true"
              className="inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-status-running"
            />
            <div className="min-w-0 flex-1 text-sm">
              {effectiveStatus === 'generating' ? (
                <>
                  <p className="font-medium text-slate-900">
                    Триває генерація конспекту та глосарію…
                  </p>
                  <p className="text-slate-600">
                    {generatingStageCopy(effectiveStage)}
                  </p>
                </>
              ) : (
                <p className="font-medium text-slate-900">
                  {inProgressCopy(effectiveStatus, effectiveStage)}
                </p>
              )}
              {inProgressShowProgress ? (
                <div className="mt-2">
                  <ProgressBar percent={statusQuery.data?.progress_percent ?? null} />
                </div>
              ) : null}
            </div>
          </section>
        ) : null}

        {retryBannerVisible ? (
          <section
            aria-label="Помилка обробки — можна повторити"
            className="flex flex-wrap items-start gap-3 rounded border border-status-failed/40 bg-white p-4 shadow-sm"
          >
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-status-failed">Помилка обробки</p>
              {lecture.error_message !== null ? (
                <p className="mt-1 text-sm text-slate-700">{lecture.error_message}</p>
              ) : null}
              <p className="mt-1 text-xs text-slate-500">
                Часткові артефакти збережено — можна перезапустити з останнього успішного етапу.
              </p>
            </div>
            <div className="flex flex-col items-end gap-1">
              <button
                type="button"
                onClick={() => retryMutation.mutate()}
                disabled={retryMutation.isPending}
                className="inline-flex items-center gap-2 rounded bg-status-running px-3 py-1.5 text-sm font-medium text-white shadow-sm hover:bg-status-running/90 disabled:cursor-not-allowed disabled:opacity-60"
              >
                {retryMutation.isPending ? (
                  <>
                    <span
                      aria-hidden="true"
                      className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-white border-t-transparent"
                    />
                    Перезапуск…
                  </>
                ) : (
                  'Повторити'
                )}
              </button>
              {retryErrorMessage !== null ? (
                <p className="max-w-xs text-right text-xs text-status-failed" role="alert">
                  {retryErrorMessage}
                </p>
              ) : null}
            </div>
          </section>
        ) : null}

        {transcriptAvailable ? (
          <section aria-label="Аудіоплеєр" className="space-y-2">
            {audioError !== null ? (
              <p className="text-sm text-status-failed" role="alert">
                {audioError}
              </p>
            ) : null}
            <AudioPlayer ref={audioRef} src={audioSrc} />
          </section>
        ) : null}

        {transcriptAvailable ? (
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
            {!isCompleted ? (
              <span
                className="text-xs text-slate-500"
                title="Файл міститиме лише вже готові розділи (наприклад, транскрипт без глосарію)."
              >
                Часткова версія — LLM ще працює
              </span>
            ) : null}
            {exportError !== null ? (
              <p className="text-sm text-status-failed" role="alert">
                {exportError}
              </p>
            ) : null}
          </section>
        ) : null}

        {visibleTabs.length > 0 ? (
          <section aria-label="Вміст лекції" className="space-y-3">
            <div
              role="tablist"
              aria-label="Вкладки лекції"
              className="flex gap-1 border-b border-slate-200"
            >
              {visibleTabs.includes('transcript') ? (
                <TabButton current={tab} value="transcript" onSelect={setTab}>
                  Транскрипт
                </TabButton>
              ) : null}
              {visibleTabs.includes('summary') ? (
                <TabButton current={tab} value="summary" onSelect={setTab}>
                  Конспект
                </TabButton>
              ) : null}
              {visibleTabs.includes('glossary') ? (
                <TabButton current={tab} value="glossary" onSelect={setTab}>
                  Глосарій
                </TabButton>
              ) : null}
            </div>
            <div>
              {tab === 'transcript' && visibleTabs.includes('transcript') ? (
                <TranscriptView lectureId={lectureId} onSeek={seekTo} />
              ) : tab === 'summary' && visibleTabs.includes('summary') ? (
                <SummaryView lectureId={lectureId} onSeek={seekTo} />
              ) : tab === 'glossary' && visibleTabs.includes('glossary') ? (
                <GlossaryView lectureId={lectureId} onSeek={seekTo} />
              ) : null}
            </div>
          </section>
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
