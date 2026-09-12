import { fetchJson } from './client';
import type { CourseResponse } from '@/types/api';

export interface CreateCourseInput {
  title: string;
}

export async function listCourses(): Promise<CourseResponse[]> {
  return fetchJson<CourseResponse[]>('/api/courses');
}

export async function getCourse(id: number): Promise<CourseResponse> {
  return fetchJson<CourseResponse>(`/api/courses/${id}`);
}

export async function createCourse(input: CreateCourseInput): Promise<CourseResponse> {
  return fetchJson<CourseResponse>('/api/courses', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export async function deleteCourse(id: number): Promise<void> {
  await fetchJson<void>(`/api/courses/${id}`, { method: 'DELETE' });
}
