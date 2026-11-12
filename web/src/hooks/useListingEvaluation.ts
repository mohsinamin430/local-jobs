import { useState } from 'react';
import { request } from '../lib/api';
import type { Job, Selection } from '../types';

interface Assessment {
  id: string;
  details: Record<string, string>;
  requirements: { requirement: string; quote: string; experience: string; match: string; desirable: boolean }[];
  recommendation: string;
  reason: string;
  source: string;
  sources: { url: string; status: string }[];
  salary: string;
  salaryStatus: string;
  salarySources: { label: string; url: string; title: string; excerpt: string; verified: boolean }[];
  flags: string[];
  contact: string;
  contactStatus: string;
  email: string;
}

type EvaluationResult =
  | { status: 'idle' | 'evaluating' }
  | { status: 'ready'; assessment: Assessment }
  | { status: 'error'; message: string };

export function useListingEvaluation() {
  const [result, setResult] = useState<EvaluationResult>({ status: 'idle' });

  async function evaluate(job: Job, selection: Selection | null, pasted = '') {
    const pending: EvaluationResult = { status: 'evaluating' };
    setResult(pending);
    let outcome: EvaluationResult;
    try {
      const assessment = await request<Assessment>('/api/applications/evaluate', {
        method: 'POST',
        body: JSON.stringify({
          title: job.title, company: job.company, location: job.location, salary: job.salary,
          url: job.url, applyUrl: job.applyUrl, description: job.description, pasted,
          workModel: job.workModel, jobId: job.id, runId: selection?.id, runSource: selection?.source,
        }),
      });
      outcome = { status: 'ready', assessment };
    } catch (cause) {
      outcome = { status: 'error', message: cause instanceof Error ? cause.message : 'Evaluation failed.' };
    }
    setResult(current => current === pending ? outcome : current);
  }

  return { result, evaluate, reset: () => setResult({ status: 'idle' }) };
}

export type ListingEvaluation = ReturnType<typeof useListingEvaluation>;
