import type { ReactElement } from 'react';
import { useMemo } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { ApiError } from '@/api/client';
import { getCourse } from '@/api/courses';
import { deleteLecture, listLectures } from '@/api/lectures';
import { StatusBadge } from '@/components/StatusBadge';
import { UploadForm } from '@/components/UploadForm';
import type { CourseResponse, LectureListItem, LectureStatus } from '@/types/api';
import { formatDateTime, formatDuration } from '@/utils/format';

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

  const anyInProgress = useMemo(
    () => (lecturesQuery.data ?? []).some((l) => IN_PROGRESS.includes(l.status)),
    [lecturesQuery.data],
  );

  if (!validId) {
    return (
      <main className="mx-auto max-w-4xl space-y-2 p-6">
        <p className="text-status-failed">Invalid course id.</p>
        <Link to="/courses" className="text-status-running hover:underline">
          ← Back to courses
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

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl flex-col gap-1 px-6 py-4">
          <Link to="/courses" className="text-sm text-status-running hover:underline">
            ← All courses
          </Link>
          <h1 className="text-xl font-bold text-slate-900">
            {courseQuery.data?.title ?? 'Course'}
          </h1>
          {courseQuery.data !== undefined ? (
            <p className="text-xs text-slate-500">
              {courseQuery.data.lecture_count}{' '}
              {courseQuery.data.lecture_count === 1 ? 'lecture' : 'lectures'} · created{' '}
              {formatDateTime(courseQuery.data.created_at)}
            </p>
          ) : null}
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        <section aria-labelledby="upload-heading" className="space-y-2">
          <h2 id="upload-heading" className="text-lg font-semibold text-slate-900">
            Upload a lecture
          </h2>
          <UploadForm
            courseId={courseId}
            disabled={anyInProgress}
            disabledReason={
              anyInProgress
                ? 'Another transcription is currently running. Please wait for it to finish.'
                : undefined
            }
          />
        </section>

        <section aria-labelledby="lectures-heading" className="space-y-3">
          <h2 id="lectures-heading" className="text-lg font-semibold text-slate-900">
            Lectures
          </h2>
          {deleteError !== null ? (
            <p className="text-sm text-status-failed" role="alert">
              {deleteError}
            </p>
          ) : null}
          {lecturesQuery.isLoading ? (
            <p className="text-slate-500">Loading lectures…</p>
          ) : lecturesQuery.isError ? (
            <p className="text-status-failed" role="alert">
              {lecturesQuery.error instanceof Error
                ? lecturesQuery.error.message
                : 'Failed to load lectures.'}
            </p>
          ) : lecturesQuery.data === undefined || lecturesQuery.data.length === 0 ? (
            <p className="rounded border border-dashed border-slate-300 bg-white p-6 text-center text-slate-500">
              No lectures yet. Upload an audio file above to create one.
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
                      {lecture.language ?? 'language pending'} · uploaded{' '}
                      {formatDateTime(lecture.created_at)}
                    </p>
                    {lecture.error_message !== null ? (
                      <p className="text-xs text-status-failed">
                        Error: {lecture.error_message}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Link
                      to={`/lectures/${lecture.id}`}
                      className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100"
                    >
                      View
                    </Link>
                    <button
                      type="button"
                      onClick={() => {
                        const ok = window.confirm(
                          `Delete lecture “${lecture.title}”? This cannot be undone.`,
                        );
                        if (ok) deleteMutation.mutate(lecture.id);
                      }}
                      disabled={deleteMutation.isPending}
                      className="rounded border border-status-failed/50 px-3 py-1 text-sm text-status-failed hover:bg-status-failed/10 disabled:opacity-50"
                    >
                      Delete
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
