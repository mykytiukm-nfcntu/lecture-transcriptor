import { clearAuthToken, fetchJson, setAuthToken } from './client';
import type { TokenResponse, UserResponse } from '@/types/api';

export interface AuthCredentials {
  username: string;
  password: string;
}

export async function registerUser(input: AuthCredentials): Promise<UserResponse> {
  return fetchJson<UserResponse>('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function login(input: AuthCredentials): Promise<TokenResponse> {
  const response = await fetchJson<TokenResponse>('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify(input),
  });
  setAuthToken(response.token);
  return response;
}

export async function logout(): Promise<void> {
  try {
    await fetchJson<void>('/api/auth/logout', { method: 'POST' });
  } finally {
    clearAuthToken();
  }
}

export async function getMe(): Promise<UserResponse> {
  return fetchJson<UserResponse>('/api/auth/me');
}
