import type { ReactElement } from 'react';
import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { ApiError } from '@/api/client';
import { getCourse } from '@/api/courses';
import { deleteLecture, listLectures, retryLecture } from '@/api/lectures';
import { StatusBadge } from '@/components/StatusBadge';
import { UploadForm } from '@/components/UploadForm';
import type {
  CourseResponse,
  LectureDetail,
  LectureListItem,
  LectureStatus,
} from '@/types/api';
import { formatDateTime, formatDuration, ukPlural } from '@/utils/format';

const IN_PROGRESS: readonly LectureStatus[] = [
  'queued',
  'normalizing',
  'transcribing',
  'generating',
];

export function CourseDetailPage(): ReactElement {
  const params = useParams<{ courseId: string }>();
  const courseId = params.courseId !== undefined ? Number(params.courseId) : Number.NaN;
  const validId = Number.isFinite(courseId) && courseId > 0;
  const queryClient = useQueryClient();

  const courseQuery = useQuery<CourseResponse>({
    queryKey: ['course', courseId],
    queryFn: () => getCourse(courseId),
    enabled: validId,
  });

  const lecturesQuery = useQuery<LectureListItem[]>({
    queryKey: ['lectures', courseId],
    queryFn: () => listLectures(courseId),
    enabled: validId,
  });

  const deleteMutation = useMutation<void, Error, number>({
    mutationFn: deleteLecture,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['lectures', courseId] });
      void queryClient.invalidateQueries({ queryKey: ['course', courseId] });
      void queryClient.invalidateQueries({ queryKey: ['courses'] });
    },
  });

  const retryMutation = useMutation<LectureDetail, Error, number>({
    mutationFn: retryLecture,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['lectures', courseId] });
      void queryClient.invalidateQueries({ queryKey: ['courses'] });
    },
  });

  const anyInProgress = useMemo(
    () => (lecturesQuery.data ?? []).some((l) => IN_PROGRESS.includes(l.status)),
    [lecturesQuery.data],
  );

  if (!validId) {
    return (
      <main className="mx-auto max-w-4xl space-y-2 p-6">
        <p className="text-status-failed">Некоректний ідентифікатор курсу.</p>
        <Link to="/courses" className="text-status-running hover:underline">
          ← Назад до курсів
        </Link>
      </main>
    );
  }

  const deleteError: string | null =
    deleteMutation.error instanceof ApiError
      ? deleteMutation.error.message
      : deleteMutation.error instanceof Error
        ? deleteMutation.error.message
        : null;

  const retryError: string | null = ((): string | null => {
    const err = retryMutation.error;
    if (err instanceof ApiError) {
      if (err.code === 'job_running') {
        return 'Наразі виконується інша транскрипція. Зачекайте, поки вона завершиться.';
      }
      return err.message;
    }
    return err instanceof Error ? err.message : null;
  })();

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl flex-col gap-1 px-6 py-4">
          <Link to="/courses" className="text-sm text-status-running hover:underline">
            ← Усі курси
          </Link>
          <h1 className="text-xl font-bold text-slate-900">
            {courseQuery.data?.title ?? 'Курс'}
          </h1>
          {courseQuery.data !== undefined ? (
            <p className="text-xs text-slate-500">
              {courseQuery.data.lecture_count}{' '}
              {ukPlural(courseQuery.data.lecture_count, 'лекція', 'лекції', 'лекцій')} · створено{' '}
              {formatDateTime(courseQuery.data.created_at)}
            </p>
          ) : null}
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        <section aria-labelledby="upload-heading" className="space-y-2">
          <h2 id="upload-heading" className="text-lg font-semibold text-slate-900">
            Завантажити лекцію
          </h2>
          <UploadForm
            courseId={courseId}
            disabled={anyInProgress}
            disabledReason={
              anyInProgress
                ? 'Наразі виконується інша транскрипція. Зачекайте, поки вона завершиться.'
                : undefined
            }
          />
        </section>

        <section aria-labelledby="lectures-heading" className="space-y-3">
          <h2 id="lectures-heading" className="text-lg font-semibold text-slate-900">
            Лекції
          </h2>
          {deleteError !== null ? (
            <p className="text-sm text-status-failed" role="alert">
              {deleteError}
            </p>
          ) : null}
          {retryError !== null ? (
            <p className="text-sm text-status-failed" role="alert">
              {retryError}
            </p>
          ) : null}
          {lecturesQuery.isLoading ? (
            <p className="text-slate-500">Завантаження лекцій…</p>
          ) : lecturesQuery.isError ? (
            <p className="text-status-failed" role="alert">
              {lecturesQuery.error instanceof Error
                ? lecturesQuery.error.message
                : 'Не вдалося завантажити лекції.'}
            </p>
          ) : lecturesQuery.data === undefined || lecturesQuery.data.length === 0 ? (
            <p className="rounded border border-dashed border-slate-300 bg-white p-6 text-center text-slate-500">
              Лекцій ще немає. Завантажте аудіофайл вище.
            </p>
          ) : (
            <ul className="space-y-2">
              {lecturesQuery.data.map((lecture) => (
                <li
                  key={lecture.id}
                  className="flex flex-col gap-3 rounded border border-slate-200 bg-white p-4 shadow-sm md:flex-row md:items-center md:justify-between"
                >
                  <div className="min-w-0 flex-1 space-y-1">
                    <div className="flex flex-wrap items-center gap-3">
                      <Link
                        to={`/lectures/${lecture.id}`}
                        className="truncate text-base font-semibold text-slate-900 hover:text-status-running"
                      >
                        {lecture.title}
                      </Link>
                      <StatusBadge lecture={lecture} />
                    </div>
                    <p className="text-xs text-slate-500">
                      {formatDuration(lecture.duration_seconds)} ·{' '}
                      {lecture.language ?? 'визначення мови…'} · завантажено{' '}
                      {formatDateTime(lecture.created_at)}
                    </p>
                    {lecture.status === 'generating' &&
                    (lecture.last_completed_stage === 'transcribe' ||
                      lecture.last_completed_stage === 'summary') ? (
                      <p className="text-xs font-semibold text-status-completed">
                        Транскрипт готовий — можна відкрити
                      </p>
                    ) : null}
                    {lecture.error_message !== null ? (
                      <p className="text-xs text-status-failed">
                        Помилка: {lecture.error_message}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Link
                      to={`/lectures/${lecture.id}`}
                      className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100"
                    >
                      Переглянути
                    </Link>
                    {lecture.status === 'failed' && lecture.can_retry ? (
                      <button
                        type="button"
                        onClick={() => retryMutation.mutate(lecture.id)}
                        disabled={retryMutation.isPending || anyInProgress}
                        className="rounded border border-status-running px-3 py-1 text-sm text-status-running hover:bg-status-running/10 disabled:opacity-50"
                      >
                        Спробувати знову
                      </button>
                    ) : null}
                    <button
                      type="button"
                      onClick={() => {
                        const ok = window.confirm(
                          `Видалити лекцію «${lecture.title}»? Це неворотно.`,
                        );
                        if (ok) deleteMutation.mutate(lecture.id);
                      }}
                      disabled={deleteMutation.isPending}
                      className="rounded border border-status-failed/50 px-3 py-1 text-sm text-status-failed hover:bg-status-failed/10 disabled:opacity-50"
                    >
                      Видалити
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      </main>
    </div>
  );
}
