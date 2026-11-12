import Papa from 'papaparse';
import type { Job } from '../types.js';

function string(value: unknown): string {
  return typeof value === 'string' ? value.trim() : typeof value === 'number' ? String(value) : '';
}

export function safeUrl(value: unknown): string {
  try {
    const url = new URL(string(value));
    return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
  } catch { return ''; }
}

export function sourceLabel(source: string): string {
  const labels: Record<string, string> = { jobsbyqais: 'Jobs by Qais', remoteok: 'Remote OK', welcome: 'Welcome to the Jungle', linkedin: 'LinkedIn', linkedin_public_guest: 'LinkedIn' };
  return labels[source] || source.charAt(0).toUpperCase() + source.slice(1);
}

export function hiddenAdvertTitle(title: string): boolean {
  return /\btrainee(?:ship)?\b|\bno[\s-]+(?:(?:prior|previous|commercial)[\s-]+)?experience[\s-]+(?:is[\s-]+)?(?:required|needed|necessary)\b/i.test(title);
}

export function normalizeJob(raw: Record<string, unknown>, index: number, imported = false): Job {
  const fields = new Map(Object.entries(raw).map(([key, value]) => [key.toLowerCase().replace(/[^a-z0-9]/g, ''), value]));
  const get = (...keys: string[]) => keys.map(key => string(fields.get(key))).find(Boolean) || '';
  const fit = get('fit', 'fitrating');
  const work = get('workmodel', 'worktype', 'workarrangement', 'workplace');
  const model = /hybrid/i.test(work) ? 'hybrid' : /remote/i.test(work) ? 'remote' : /on.?site/i.test(work) ? 'on-site' : work || 'unknown';
  return {
    key: `${get('id', 'jobid') || 'job'}-${index}`,
    id: get('id', 'jobid'), title: get('title', 'jobtitle', 'position') || 'Untitled role',
    company: get('company', 'employer', 'organization', 'organisation') || 'Employer not stated',
    location: get('location', 'joblocation') || 'Location not stated', workModel: model,
    fit: ['Strong', 'Possible', 'Stretch'].find(value => value.toLowerCase() === fit.toLowerCase()) || fit || 'Unrated',
    why: get('fitexplanation', 'why', 'fitreason'), salary: get('salary', 'salaryrange') || 'Not stated',
    posted: get('postingtimedisplay', 'postedat', 'dateposted', 'detailpostedtext', 'searchpostedtext'),
    description: get('description', 'jobdescription'), contact: get('contact', 'hiringcontact') || 'Not found',
    contactStatus: imported ? 'imported' : get('contactstatus') || 'unverified',
    email: get('verifiedemail', 'contactemail', 'email'),
    applyUrl: safeUrl(get('preferredapplyurl', 'applyurl', 'applicationurl')), url: safeUrl(get('url', 'linkedinurl', 'joburl')),
    imported, evidence: raw, source:get('source','jobsource') || 'Not stated',
    foundOn:Array.isArray(raw.found_on) ? raw.found_on.filter((value):value is string=>typeof value==='string') : [get('source','jobsource')].filter(Boolean),
  };
}

export function parseCsv(text: string): { jobs: Job[]; warnings: string[]; total: number } {
  const result = Papa.parse<Record<string, string>>(text.replace(/^\uFEFF/, ''), {
    header: true, skipEmptyLines: 'greedy', dynamicTyping: false,
    transformHeader: header => header.trim(),
  });
  if (result.errors.some(error => error.type === 'Quotes')) throw new Error('This CSV contains an unfinished or invalid quoted field. Please check the file and try again.');
  const headers = (result.meta.fields || []).map(field => field.toLowerCase().replace(/[^a-z0-9]/g, ''));
  if (!headers.some(field => ['title', 'jobtitle', 'position', 'company', 'employer'].includes(field))) {
    throw new Error('Choose a job-results CSV containing a Title, Job Title, Company or Employer column.');
  }
  if (!result.data.length) throw new Error('This CSV has column headers but no job rows.');
  const warnings = [...new Set(result.errors.map(error => error.message))].slice(0, 3);
  if (result.data.length > 10_000) warnings.push('This preview shows the first 10,000 rows. Your original file is unchanged.');
  return { jobs: result.data.slice(0, 10_000).map((row, index) => normalizeJob(row, index, true)), warnings, total: result.data.length };
}

export type SortKey = 'title' | 'company' | 'fit' | 'workModel';
export function filterJobs(jobs: Job[], query: string, fit: string, model: string, sort: SortKey, direction: 'asc' | 'desc'): Job[] {
  const term = query.trim().toLowerCase();
  const rank: Record<string, number> = { Strong: 0, Possible: 1, Stretch: 2, Unrated: 3 };
  return jobs.filter(job => (!fit || job.fit === fit) && (!model || job.workModel === model)
    && (!term || [job.title, job.company, job.location, job.description].some(value => value.toLowerCase().includes(term))))
    .sort((a, b) => {
      const comparison = sort === 'fit' ? (rank[a.fit] ?? 4) - (rank[b.fit] ?? 4) : a[sort].localeCompare(b[sort]);
      return direction === 'asc' ? comparison : -comparison;
    });
}

export function formatDate(value?: string | null, short = false): string {
  if (!value) return 'Not available';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', day: 'numeric', month: 'short', ...(short ? {} : { hour: '2-digit', minute: '2-digit' }) }).format(parsed);
}

export function duration(start?: string | null, end?: string | null): string {
  if (!start || !end) return '—';
  const seconds = Math.max(0, Math.round((Date.parse(end) - Date.parse(start)) / 1000));
  if (!Number.isFinite(seconds)) return '—';
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
