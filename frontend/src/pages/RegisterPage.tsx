import type { FormEvent, ReactElement } from 'react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { login, registerUser } from '@/api/auth';
import type { AuthCredentials } from '@/api/auth';
import { ApiError } from '@/api/client';

export function RegisterPage(): ReactElement {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [clientError, setClientError] = useState<string | null>(null);

  const mutation = useMutation<void, Error, AuthCredentials>({
    mutationFn: async (input) => {
      await registerUser(input);
      // Auto-login for the smoother UX called out in the spec.
      await login(input);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
      navigate('/courses', { replace: true });
    },
  });

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setClientError(null);
    const trimmed = username.trim();
    if (trimmed.length === 0) {
      setClientError('Введіть ім\'я користувача.');
      return;
    }
    if (password.length < 8) {
      setClientError('Пароль має бути не менше 8 символів.');
      return;
    }
    if (password !== confirm) {
      setClientError('Паролі не збігаються.');
      return;
    }
    mutation.mutate({ username: trimmed, password });
  };

  const serverError: string | null = (() => {
    const err = mutation.error;
    if (err === null || err === undefined) return null;
    if (err instanceof ApiError) {
      if (err.status === 409) {
        return 'Це ім\'я користувача вже зайняте.';
      }
      if (err.status >= 500) {
        return 'Сталася помилка на сервері. Спробуйте пізніше.';
      }
      return err.message;
    }
    return err.message;
  })();

  const errorMessage = clientError ?? serverError;

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center p-6">
      <div className="rounded-lg border border-slate-200 bg-white p-6 shadow">
        <h1 className="mb-4 text-2xl font-bold text-slate-900">Створити акаунт</h1>
        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
          <div>
            <label htmlFor="register-username" className="block text-sm font-medium text-slate-700">
              Ім'я користувача
            </label>
            <input
              id="register-username"
              type="text"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="mt-1 block w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-status-running focus:outline-none"
            />
          </div>
          <div>
            <label htmlFor="register-password" className="block text-sm font-medium text-slate-700">
              Пароль
            </label>
            <input
              id="register-password"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="mt-1 block w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-status-running focus:outline-none"
            />
            <p className="mt-1 text-xs text-slate-500">Мінімум 8 символів.</p>
          </div>
          <div>
            <label htmlFor="register-confirm" className="block text-sm font-medium text-slate-700">
              Підтвердіть пароль
            </label>
            <input
              id="register-confirm"
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="mt-1 block w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-status-running focus:outline-none"
            />
          </div>
          {errorMessage !== null ? (
            <p className="text-sm text-status-failed" role="alert">
              {errorMessage}
            </p>
          ) : null}
          <button
            type="submit"
            disabled={mutation.isPending}
            className="w-full rounded bg-status-running px-4 py-2 font-medium text-white shadow hover:bg-blue-600 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {mutation.isPending ? 'Створення акаунту…' : 'Створити акаунт'}
          </button>
        </form>
        <p className="mt-4 text-center text-sm text-slate-600">
          Вже маєте акаунт?{' '}
          <Link to="/login" className="text-status-running hover:underline">
            Увійти
          </Link>
        </p>
      </div>
    </main>
  );
}
