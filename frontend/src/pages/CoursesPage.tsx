import type { FormEvent, ReactElement } from 'react';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { createCourse, deleteCourse, listCourses } from '@/api/courses';
import { ApiError } from '@/api/client';
import { useAuth } from '@/hooks/useAuth';
import type { CourseResponse } from '@/types/api';
import { formatDateTime, ukPlural } from '@/utils/format';

export function CoursesPage(): ReactElement {
  const { user, logout } = useAuth();
  const queryClient = useQueryClient();

  const coursesQuery = useQuery<CourseResponse[]>({
    queryKey: ['courses'],
    queryFn: listCourses,
  });

  const [newTitle, setNewTitle] = useState('');
  const [createError, setCreateError] = useState<string | null>(null);

  const createMutation = useMutation<CourseResponse, Error, { title: string }>({
    mutationFn: createCourse,
    onSuccess: () => {
      setNewTitle('');
      setCreateError(null);
      void queryClient.invalidateQueries({ queryKey: ['courses'] });
    },
    onError: (error) => {
      setCreateError(error instanceof Error ? error.message : 'Не вдалося створити курс.');
    },
  });

  const deleteMutation = useMutation<void, Error, number>({
    mutationFn: deleteCourse,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['courses'] });
    },
  });

  const handleCreate = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const trimmed = newTitle.trim();
    if (trimmed.length === 0) {
      setCreateError('Введіть назву курсу.');
      return;
    }
    createMutation.mutate({ title: trimmed });
  };

  const handleDelete = (course: CourseResponse): void => {
    const ok = window.confirm(
      `Видалити курс «${course.title}»? Усі лекції всередині будуть безповоротно видалені.`,
    );
    if (!ok) return;
    deleteMutation.mutate(course.id);
  };

  const deleteError: string | null =
    deleteMutation.error instanceof ApiError
      ? deleteMutation.error.message
      : deleteMutation.error instanceof Error
        ? deleteMutation.error.message
        : null;

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <h1 className="text-xl font-bold text-slate-900">Транскриптор лекцій</h1>
          <div className="flex items-center gap-4 text-sm text-slate-700">
            {user !== undefined ? (
              <span>
                Ви увійшли як <strong>{user.username}</strong>
              </span>
            ) : null}
            <button
              type="button"
              onClick={() => {
                void logout();
              }}
              className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100"
            >
              Вийти
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-8">
        <section
          aria-labelledby="create-course-heading"
          className="rounded border border-slate-200 bg-white p-4 shadow-sm"
        >
          <h2 id="create-course-heading" className="mb-3 text-lg font-semibold text-slate-900">
            Створити курс
          </h2>
          <form onSubmit={handleCreate} className="flex flex-col gap-3 md:flex-row md:items-end">
            <div className="flex-1">
              <label htmlFor="course-title" className="block text-sm font-medium text-slate-700">
                Назва курсу
              </label>
              <input
                id="course-title"
                type="text"
                required
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                className="mt-1 block w-full rounded border border-slate-300 px-3 py-2 text-sm"
              />
            </div>
            <button
              type="submit"
              disabled={createMutation.isPending}
              className="rounded bg-status-running px-4 py-2 text-sm font-medium text-white shadow hover:bg-blue-600 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {createMutation.isPending ? 'Створення…' : 'Створити'}
            </button>
          </form>
          {createError !== null ? (
            <p className="mt-2 text-sm text-status-failed" role="alert">
              {createError}
            </p>
          ) : null}
        </section>

        <section aria-labelledby="courses-heading" className="space-y-3">
          <h2 id="courses-heading" className="text-lg font-semibold text-slate-900">
            Ваші курси
          </h2>
          {deleteError !== null ? (
            <p className="text-sm text-status-failed" role="alert">
              {deleteError}
            </p>
          ) : null}
          {coursesQuery.isLoading ? (
            <p className="text-slate-500">Завантаження курсів…</p>
          ) : coursesQuery.isError ? (
            <p className="text-status-failed" role="alert">
              {coursesQuery.error instanceof Error
                ? coursesQuery.error.message
                : 'Не вдалося завантажити курси.'}
            </p>
          ) : coursesQuery.data === undefined || coursesQuery.data.length === 0 ? (
            <p className="rounded border border-dashed border-slate-300 bg-white p-6 text-center text-slate-500">
              Курсів ще немає. Створіть перший вище, щоб почати.
            </p>
          ) : (
            <ul className="grid gap-3 md:grid-cols-2">
              {coursesQuery.data.map((course) => (
                <li
                  key={course.id}
                  className="flex flex-col justify-between rounded border border-slate-200 bg-white p-4 shadow-sm"
                >
                  <div>
                    <h3 className="text-base font-semibold text-slate-900">
                      <Link to={`/courses/${course.id}`} className="hover:text-status-running">
                        {course.title}
                      </Link>
                    </h3>
                    <p className="mt-1 text-sm text-slate-600">
                      {course.lecture_count}{' '}
                      {ukPlural(course.lecture_count, 'лекція', 'лекції', 'лекцій')} · створено{' '}
                      {formatDateTime(course.created_at)}
                    </p>
                  </div>
                  <div className="mt-3 flex items-center justify-between">
                    <Link
                      to={`/courses/${course.id}`}
                      className="text-sm text-status-running hover:underline"
                    >
                      Відкрити →
                    </Link>
                    <button
                      type="button"
                      onClick={() => handleDelete(course)}
                      disabled={deleteMutation.isPending}
                      className="text-sm text-status-failed hover:underline disabled:opacity-50"
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
