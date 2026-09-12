/**
 * TypeScript shapes for the backend API responses this frontend consumes.
 *
 * These mirror the Pydantic schemas under backend/app/schemas/ but are trimmed
 * to only the fields the UI actually reads. `datetime` fields arrive as ISO
 * strings after JSON serialisation, so they are typed as `string`.
 */

export type LectureStatus =
  | 'queued'
  | 'normalizing'
  | 'transcribing'
  | 'generating'
  | 'completed'
  | 'failed';

export interface UserResponse {
  id: number;
  username: string;
  created_at: string;
}

export interface TokenResponse {
  token: string;
  token_type: 'bearer';
}

export interface CourseResponse {
  id: number;
  title: string;
  created_at: string;
  lecture_count: number;
}

export interface LectureListItem {
  id: number;
  title: string;
  original_filename: string;
  duration_seconds: number | null;
  language: string | null;
  status: LectureStatus;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
}

export interface LectureDetail extends LectureListItem {
  course_id: number;
}

export interface LectureUploadAccepted extends LectureListItem {
  preliminary_eta_seconds: number | null;
}

export interface LectureStatusResponse {
  id: number;
  status: LectureStatus;
  started_at: string | null;
  finished_at: string | null;
  error_message: string | null;
  elapsed_seconds: number | null;
  preliminary_eta_seconds: number | null;
  progress_percent: number | null;
  progress_stage: string | null;
}

export interface TranscriptSegment {
  index: number;
  start_seconds: number;
  end_seconds: number;
  text: string;
  confidence: number | null;
}

export interface TranscriptResponse {
  language: string;
  full_text: string;
  segments: TranscriptSegment[];
}

export type SummaryHighlightKind = 'definition' | 'formula' | 'example' | 'key_point';

export interface SummaryHighlight {
  kind: SummaryHighlightKind;
  text: string;
  timestamp_seconds: number;
}

export interface SummarySection {
  heading: string;
  timestamp_seconds: number;
  bullets: string[];
  highlights: SummaryHighlight[];
  subsections: SummarySection[];
}

export interface SummaryDocument {
  title: string;
  language: string;
  sections: SummarySection[];
}

export interface SummaryResponse {
  content: SummaryDocument;
  prompt_version: string;
  generation_language: string;
  created_at: string;
}

export interface GlossaryTermResponse {
  term: string;
  definition: string;
  first_mention_seconds: number;
}

export interface GlossaryResponse {
  language: string;
  prompt_version: string;
  entries: GlossaryTermResponse[];
  created_at: string;
}

export interface ModelsResponse {
  default: string;
  installed: string[];
}

export interface ApiErrorBody {
  detail: string;
  code?: string | null;
}
