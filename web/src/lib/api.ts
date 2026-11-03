import type { Selection } from '../types';

export function isActiveTask(state?: string): boolean {
  return state === 'starting' || state === 'running' || state === 'cancelling' || state === 'cooling_down';
}

export async function request<T>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { ...options, headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers } });
  const data = await response.json().catch(() => null) as { error?: string; message?: string } | null;
  if (!response.ok) throw new Error(data?.error || data?.message || `The local service returned ${response.status}. Please try again.`);
  return data as T;
}

export function runUrl(selection: Selection): string {
  return `/api/runs/${encodeURIComponent(selection.id)}?source=${encodeURIComponent(selection.source)}`;
}

export function downloadUrl(selection: Selection, format: 'csv' | 'json' | 'digest'): string {
  return `/api/runs/${encodeURIComponent(selection.id)}/download?source=${encodeURIComponent(selection.source)}&format=${format}`;
}
