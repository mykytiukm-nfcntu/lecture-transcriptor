import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';

import { getMe, logout as apiLogout } from '@/api/auth';
import { AUTH_INVALID_EVENT, getAuthToken } from '@/api/client';
import type { UserResponse } from '@/types/api';

const ME_QUERY_KEY = ['auth', 'me'] as const;

export interface UseAuthResult {
  token: string | null;
  user: UserResponse | undefined;
  isLoading: boolean;
  isAuthenticated: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
}

/**
 * React hook exposing the current auth state.
 *
 * - Reads the bearer token from `localStorage` synchronously.
 * - When a token is present, fetches `/api/auth/me` via TanStack Query (cached
 *   per-token so a logout+login re-fetches).
 * - Listens for the `auth:invalid` window event dispatched by the client on
 *   401 responses; removes the me query and navigates to /login.
 */
export function useAuth(): UseAuthResult {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const token = getAuthToken();

  const query = useQuery({
    queryKey: [...ME_QUERY_KEY, token],
    queryFn: getMe,
    enabled: token !== null,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  useEffect(() => {
    const handler = (): void => {
      queryClient.removeQueries({ queryKey: ME_QUERY_KEY });
      navigate('/login', { replace: true });
    };
    window.addEventListener(AUTH_INVALID_EVENT, handler);
    return () => {
      window.removeEventListener(AUTH_INVALID_EVENT, handler);
    };
  }, [queryClient, navigate]);

  const refresh = useCallback(async (): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: ME_QUERY_KEY });
  }, [queryClient]);

  const logout = useCallback(async (): Promise<void> => {
    try {
      await apiLogout();
    } finally {
      queryClient.removeQueries({ queryKey: ME_QUERY_KEY });
      navigate('/login', { replace: true });
    }
  }, [queryClient, navigate]);

  return useMemo<UseAuthResult>(
    () => ({
      token,
      user: query.data,
      isLoading: token !== null && query.isLoading,
      isAuthenticated: token !== null && query.data !== undefined,
      refresh,
      logout,
    }),
    [token, query.data, query.isLoading, refresh, logout],
  );
}
