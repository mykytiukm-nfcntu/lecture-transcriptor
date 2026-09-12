import type { FormEvent, ReactElement } from 'react';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { login } from '@/api/auth';
import { ApiError } from '@/api/client';
import type { AuthCredentials } from '@/api/auth';
import type { TokenResponse } from '@/types/api';

export function LoginPage(): ReactElement {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const mutation = useMutation<TokenResponse, Error, AuthCredentials>({
    mutationFn: login,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['auth', 'me'] });
      navigate('/courses', { replace: true });
    },
  });

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    mutation.mutate({ username: username.trim(), password });
  };

  const errorMessage: string | null =
    mutation.error instanceof ApiError
      ? mutation.error.message
      : mutation.error instanceof Error
        ? mutation.error.message
        : null;

  return (
    <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center p-6">
      <div className="rounded-lg border border-slate-200 bg-white p-6 shadow">
        <h1 className="mb-4 text-2xl font-bold text-slate-900">Вхід до акаунту</h1>
        <form onSubmit={handleSubmit} className="space-y-4" noValidate>
          <div>
            <label htmlFor="login-username" className="block text-sm font-medium text-slate-700">
              Ім'я користувача
            </label>
            <input
              id="login-username"
              type="text"
              autoComplete="username"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="mt-1 block w-full rounded border border-slate-300 px-3 py-2 text-sm focus:border-status-running focus:outline-none"
            />
          </div>
          <div>
            <label htmlFor="login-password" className="block text-sm font-medium text-slate-700">
              Пароль
            </label>
            <input
              id="login-password"
              type="password"
              autoComplete="current-password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
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
            {mutation.isPending ? 'Вхід…' : 'Увійти'}
          </button>
        </form>
        <p className="mt-4 text-center text-sm text-slate-600">
          Немає акаунту?{' '}
          <Link to="/register" className="text-status-running hover:underline">
            Зареєструватися
          </Link>
        </p>
      </div>
    </main>
  );
}
