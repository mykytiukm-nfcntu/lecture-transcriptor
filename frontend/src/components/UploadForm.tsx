import type { ChangeEvent, FormEvent, ReactElement } from 'react';
import { useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { ApiError } from '@/api/client';
import { uploadLecture } from '@/api/lectures';
import { listModels } from '@/api/models';
import type { LectureUploadAccepted, ModelsResponse } from '@/types/api';

interface UploadFormProps {
  courseId: number;
  disabled: boolean;
  disabledReason?: string;
}

type Language = 'auto' | 'uk' | 'en';

// Sentinel for the "use server default" option in the model dropdown.
const DEFAULT_MODEL_KEY = '__default__';

interface FormValues {
  file: File;
  title: string;
  language: Language;
  model: string;
}

const ACCEPTED_EXTS = ['.mp3', '.wav'] as const;

function validateFile(file: File | null): string | null {
  if (file === null) {
    return 'Please choose an .mp3 or .wav file.';
  }
  if (file.size === 0) {
    return 'The selected file is empty.';
  }
  const lowered = file.name.toLowerCase();
  if (!ACCEPTED_EXTS.some((ext) => lowered.endsWith(ext))) {
    return 'Only .mp3 and .wav files are supported.';
  }
  return null;
}

export function UploadForm({ courseId, disabled, disabledReason }: UploadFormProps): ReactElement {
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [language, setLanguage] = useState<Language>('auto');
  const [model, setModel] = useState<string>(DEFAULT_MODEL_KEY);
  const [validationError, setValidationError] = useState<string | null>(null);

  const modelsQuery = useQuery<ModelsResponse>({
    queryKey: ['models'],
    queryFn: listModels,
    staleTime: 30_000,
  });

  const mutation = useMutation<LectureUploadAccepted, Error, FormValues>({
    mutationFn: (input) =>
      uploadLecture({
        courseId,
        file: input.file,
        title: input.title.trim().length > 0 ? input.title.trim() : undefined,
        language: input.language === 'auto' ? undefined : input.language,
        model: input.model === DEFAULT_MODEL_KEY ? undefined : input.model,
      }),
    onSuccess: () => {
      setFile(null);
      setTitle('');
      setLanguage('auto');
      setModel(DEFAULT_MODEL_KEY);
      setValidationError(null);
      if (fileInputRef.current !== null) {
        fileInputRef.current.value = '';
      }
      void queryClient.invalidateQueries({ queryKey: ['lectures', courseId] });
      void queryClient.invalidateQueries({ queryKey: ['course', courseId] });
      void queryClient.invalidateQueries({ queryKey: ['courses'] });
    },
  });

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const next = event.target.files?.[0] ?? null;
    setFile(next);
    setValidationError(null);
  };

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const err = validateFile(file);
    if (err !== null) {
      setValidationError(err);
      return;
    }
    setValidationError(null);
    if (file === null) return;
    mutation.mutate({ file, title, language, model });
  };

  const submitDisabled = disabled || mutation.isPending;
  const serverError: string | null =
    mutation.error instanceof ApiError
      ? mutation.error.message
      : mutation.error instanceof Error
        ? mutation.error.message
        : null;
  const errorMessage = validationError ?? serverError;

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-3 rounded border border-slate-200 bg-white p-4 shadow-sm"
      aria-label="Upload lecture"
    >
      <div className="grid gap-3 md:grid-cols-3">
        <div className="md:col-span-2">
          <label htmlFor="upload-file" className="block text-sm font-medium text-slate-700">
            Audio file (.mp3 or .wav)
          </label>
          <input
            ref={fileInputRef}
            id="upload-file"
            type="file"
            accept=".mp3,.wav,audio/mpeg,audio/wav"
            onChange={handleFileChange}
            className="mt-1 block w-full text-sm text-slate-700 file:mr-3 file:rounded file:border-0 file:bg-slate-100 file:px-3 file:py-1 file:text-sm file:text-slate-700 hover:file:bg-slate-200"
          />
        </div>
        <div>
          <label htmlFor="upload-language" className="block text-sm font-medium text-slate-700">
            Language
          </label>
          <select
            id="upload-language"
            value={language}
            onChange={(e) => setLanguage(e.target.value as Language)}
            className="mt-1 block w-full rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="auto">Auto-detect</option>
            <option value="uk">Ukrainian</option>
            <option value="en">English</option>
          </select>
        </div>
      </div>

      <div>
        <label htmlFor="upload-title" className="block text-sm font-medium text-slate-700">
          Title (optional)
        </label>
        <input
          id="upload-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          className="mt-1 block w-full rounded border border-slate-300 px-2 py-1 text-sm"
          placeholder="Defaults to the original filename"
        />
      </div>

      <div>
        <label htmlFor="upload-model" className="block text-sm font-medium text-slate-700">
          LLM model
        </label>
        <select
          id="upload-model"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          className="mt-1 block w-full rounded border border-slate-300 px-2 py-1 text-sm"
          disabled={modelsQuery.isLoading}
        >
          <option value={DEFAULT_MODEL_KEY}>
            {modelsQuery.data !== undefined
              ? `Server default (${modelsQuery.data.default})`
              : 'Server default'}
          </option>
          {(modelsQuery.data?.installed ?? []).map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <p className="mt-1 text-xs text-slate-500">
          Install more with{' '}
          <code className="rounded bg-slate-100 px-1 py-0.5">ollama pull &lt;name&gt;</code>
          {modelsQuery.data !== undefined && modelsQuery.data.installed.length === 0
            ? ' — Ollama is running but no models are installed yet.'
            : ' (e.g. qwen2.5:7b-instruct, llama3.2:3b, phi4).'}
        </p>
      </div>

      {errorMessage !== null ? (
        <p className="text-sm text-status-failed" role="alert">
          {errorMessage}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={submitDisabled}
          title={disabled && disabledReason !== undefined ? disabledReason : undefined}
          className="rounded bg-status-running px-4 py-2 text-sm font-medium text-white shadow hover:bg-blue-600 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {mutation.isPending ? 'Uploading…' : 'Upload'}
        </button>
        {disabled && disabledReason !== undefined ? (
          <p className="text-xs text-slate-500">{disabledReason}</p>
        ) : null}
      </div>
    </form>
  );
}
