import { createFormData, fetchBlob, fetchJson } from './client';
import type {
  GlossaryResponse,
  LectureDetail,
  LectureListItem,
  LectureStatusResponse,
  LectureUploadAccepted,
  SummaryResponse,
  TranscriptResponse,
} from '@/types/api';

export interface UploadLectureInput {
  courseId: number;
  file: File;
  title?: string;
  language?: string;
  model?: string;
}

export interface TranscriptQuery {
  offset?: number;
  limit?: number;
}

export type ExportFormat = 'pdf' | 'txt';

export async function listLectures(courseId: number): Promise<LectureListItem[]> {
  return fetchJson<LectureListItem[]>(`/api/courses/${courseId}/lectures`);
}

export async function getLecture(id: number): Promise<LectureDetail> {
  return fetchJson<LectureDetail>(`/api/lectures/${id}`);
}

export async function deleteLecture(id: number): Promise<void> {
  await fetchJson<void>(`/api/lectures/${id}`, { method: 'DELETE' });
}

export async function uploadLecture(input: UploadLectureInput): Promise<LectureUploadAccepted> {
  const params = new URLSearchParams();
  if (input.title !== undefined && input.title.length > 0) {
    params.set('title', input.title);
  }
  if (input.language !== undefined && input.language.length > 0) {
    params.set('language', input.language);
  }
  if (input.model !== undefined && input.model.length > 0) {
    params.set('model', input.model);
  }
  const query = params.toString();
  const path = `/api/courses/${input.courseId}/lectures${query ? `?${query}` : ''}`;
  const body = createFormData({ file: input.file });
  return fetchJson<LectureUploadAccepted>(path, {
    method: 'POST',
    body,
  });
}

export async function getLectureStatus(id: number): Promise<LectureStatusResponse> {
  return fetchJson<LectureStatusResponse>(`/api/lectures/${id}/status`);
}

export async function getTranscript(
  id: number,
  query: TranscriptQuery = {},
): Promise<TranscriptResponse> {
  const params = new URLSearchParams();
  if (query.offset !== undefined) {
    params.set('offset', String(query.offset));
  }
  if (query.limit !== undefined) {
    params.set('limit', String(query.limit));
  }
  const q = params.toString();
  const path = `/api/lectures/${id}/transcript${q ? `?${q}` : ''}`;
  return fetchJson<TranscriptResponse>(path);
}

export async function getSummary(id: number): Promise<SummaryResponse> {
  return fetchJson<SummaryResponse>(`/api/lectures/${id}/summary`);
}

export async function getGlossary(id: number): Promise<GlossaryResponse> {
  return fetchJson<GlossaryResponse>(`/api/lectures/${id}/glossary`);
}

/**
 * Fetch the lecture audio as a Blob and wrap it in an object URL so it can be
 * assigned to `<audio src>`. Callers MUST call `URL.revokeObjectURL(url)` on
 * unmount to avoid leaking the underlying blob.
 *
 * We do this instead of a plain `<audio src="/api/.../audio">` because the
 * `<audio>` element cannot attach the `Authorization: Bearer` header.
 */
export async function createAudioObjectUrl(id: number): Promise<string> {
  const blob = await fetchBlob(`/api/lectures/${id}/audio`);
  return URL.createObjectURL(blob);
}

/**
 * Fetch an export and trigger a browser download via a temporary `<a>` click.
 * Same rationale as `createAudioObjectUrl`: the endpoint requires a bearer.
 */
export async function downloadExport(id: number, format: ExportFormat): Promise<void> {
  const blob = await fetchBlob(`/api/lectures/${id}/export/${format}`);
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `lecture-${id}.${format}`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}
