/**
 * Low-level HTTP client for the backend API.
 *
 * - Reads the opaque bearer token from `localStorage['auth_token']`.
 * - Attaches `Authorization: Bearer <token>` to every request when present.
 * - On any 401 response, clears the token and dispatches an `auth:invalid`
 *   window event so app-level code (e.g. `useAuth`) can redirect to /login.
 * - Parses `{ detail, code }` from JSON error bodies and throws `ApiError`.
 */

import type { ApiErrorBody } from '@/types/api';

const AUTH_TOKEN_KEY = 'auth_token';
const BASE = import.meta.env.VITE_API_BASE_URL ?? '';

/** Window event name dispatched whenever the server rejects our token. */
export const AUTH_INVALID_EVENT = 'auth:invalid';

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string | null,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

export function getAuthToken(): string | null {
  try {
    return window.localStorage.getItem(AUTH_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setAuthToken(token: string | null): void {
  try {
    if (token === null) {
      window.localStorage.removeItem(AUTH_TOKEN_KEY);
    } else {
      window.localStorage.setItem(AUTH_TOKEN_KEY, token);
    }
  } catch {
    // localStorage can throw in private mode / disabled storage; swallow.
  }
}

export function clearAuthToken(): void {
  setAuthToken(null);
}

function buildHeaders(init: RequestInit | undefined, needsJsonContentType: boolean): Headers {
  const headers = new Headers(init?.headers ?? {});
  if (needsJsonContentType && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json');
  }
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json');
  }
  const token = getAuthToken();
  if (token && !headers.has('Authorization')) {
    headers.set('Authorization', `Bearer ${token}`);
  }
  return headers;
}

async function parseErrorBody(
  response: Response,
): Promise<{ detail: string; code: string | null }> {
  const fallbackDetail = `${response.status} ${response.statusText}`.trim() || 'Request failed';
  const contentType = response.headers.get('Content-Type') ?? '';
  if (!contentType.includes('application/json')) {
    return { detail: fallbackDetail, code: null };
  }
  try {
    const body = (await response.json()) as Partial<ApiErrorBody>;
    const detail =
      typeof body.detail === 'string' && body.detail.length > 0 ? body.detail : fallbackDetail;
    const code = typeof body.code === 'string' ? body.code : null;
    return { detail, code };
  } catch {
    return { detail: fallbackDetail, code: null };
  }
}

function handleUnauthorized(): void {
  clearAuthToken();
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(AUTH_INVALID_EVENT));
  }
}

async function requestRaw(path: string, init: RequestInit | undefined): Promise<Response> {
  const body = init?.body;
  const isFormData = typeof FormData !== 'undefined' && body instanceof FormData;
  const hasJsonBody = body !== undefined && body !== null && !isFormData;
  const headers = buildHeaders(init, hasJsonBody);
  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (response.status === 401) {
    handleUnauthorized();
  }
  return response;
}

/**
 * Fetch and parse a JSON response. Returns `undefined` for 204 or non-JSON
 * bodies — callers that expect a payload must type `T` to match the endpoint.
 */
export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await requestRaw(path, init);
  if (!response.ok) {
    const { detail, code } = await parseErrorBody(response);
    throw new ApiError(response.status, code, detail);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  const contentType = response.headers.get('Content-Type') ?? '';
  if (!contentType.includes('application/json')) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** Fetch a response body as a Blob (audio streams, exports). */
export async function fetchBlob(path: string, init?: RequestInit): Promise<Blob> {
  const response = await requestRaw(path, init);
  if (!response.ok) {
    const { detail, code } = await parseErrorBody(response);
    throw new ApiError(response.status, code, detail);
  }
  return await response.blob();
}

export function createFormData(fields: Record<string, string | Blob>): FormData {
  const fd = new FormData();
  for (const [key, value] of Object.entries(fields)) {
    fd.append(key, value);
  }
  return fd;
}
