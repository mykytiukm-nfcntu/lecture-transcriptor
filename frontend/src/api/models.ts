import { fetchJson } from './client';
import type { ModelsResponse } from '@/types/api';

export async function listModels(): Promise<ModelsResponse> {
  return fetchJson<ModelsResponse>('/api/models');
}
