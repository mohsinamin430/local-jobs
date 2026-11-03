import { useEffect, useState } from 'react';
import type { Job, Selection } from '../types';
import { safeUrl } from '../lib/data';
import './application-assistant.css';

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

interface Download { name: string; url: string }

async function request<T>(action: string, body: unknown): Promise<T> {
  const response = await fetch(`/api/applications/${action}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The application assistant could not complete the request.');
  return result as T;
}

export function ApplicationAssistant({ job, selection, trackerUrl }: { job: Job; selection: Selection | null; trackerUrl?: string; }) {
  const tracker = safeUrl(trackerUrl);
  const [pasted, setPasted] = useState('');
  const [assessment, setAssessment] = useState<Assessment | null>(null);
  const [downloads, setDownloads] = useState<Download[]>([]);
  const [working, setWorking] = useState<'evaluate' | 'documents' | null>(null);
  const [error, setError] = useState('');
  useEffect(() => () => downloads.forEach(file => URL.revokeObjectURL(file.url)), [downloads]);

  async function evaluate() {
    setError('');
    setAssessment(null);
    setDownloads([]);
    setWorking('evaluate');
    try {
      setAssessment(await request<Assessment>('evaluate', {
        title: job.title, company: job.company, location: job.location, salary: job.salary,
        url: job.url, applyUrl: job.applyUrl, description: job.description, pasted,
        workModel: job.workModel, jobId: job.id, runId: selection?.id, runSource: selection?.source,
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Evaluation failed.');
    } finally {
      setWorking(null);
    }
  }

  async function build() {
    if (!assessment) return;
    setError('');
    setWorking('documents');
    try {
      const result = await request<{ files: { name: string; content: string }[] }>('documents', {
        assessmentId: assessment.id,
      });
      setDownloads(result.files.map(file => {
        const bytes = Uint8Array.from(atob(file.content), char => char.charCodeAt(0));
        const type = file.name.endsWith('.pdf') ? 'application/pdf' : 'application/zip';
        return { name: file.name, url: URL.createObjectURL(new Blob([bytes], { type })) };
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Document creation failed.');
    } finally {
      setWorking(null);
    }
  }

  return <section className="drawer-section application-assistant">
    <h3>Application assistant</h3>
    <p>Review the vacancy, compare it with your CV and decide whether to apply.</p>
    <details className="evidence-detail">
      <summary>Paste vacancy text if the listing is incomplete</summary>
      <textarea aria-label="Vacancy text" rows={5} maxLength={35000} value={pasted}
        onChange={event => { setPasted(event.target.value); setAssessment(null); setDownloads([]); }}
        disabled={Boolean(working)} />
    </details>
    <button className="button button-primary" disabled={Boolean(working)} onClick={() => void evaluate()}>
      {working === 'evaluate' ? 'Researching vacancy…' : 'Enhanced evaluation'}
    </button>
    {error && <p role="alert">{error}</p>}
    {assessment && <>
      <h4>{assessment.recommendation}</h4>
      <p>{assessment.reason}</p>
      {assessment.flags.length > 0 && <ul>{assessment.flags.map(flag => <li key={flag}>{flag}</li>)}</ul>}
      <p className="subtle-note">Description evidence · {assessment.source}</p>
      <dl>{Object.entries(assessment.details).map(([name, value]) =>
        <div key={name}><dt><strong>{name}</strong></dt><dd>{value}</dd></div>)}</dl>
      <h4>Requirements and CV evidence</h4>
      {assessment.requirements.length > 0 ? <div className="table-scroll"><table>
        <thead><tr><th>Requirement</th><th>My relevant experience</th><th>Match</th></tr></thead>
        <tbody>{assessment.requirements.map(row => <tr key={`${row.requirement}-${row.desirable}`}>
          <td>{row.requirement}{row.desirable && <small> · Desirable</small>}<p className="subtle-note">{row.quote}</p></td>
          <td>{row.experience}</td><td>{row.match}</td>
        </tr>)}</tbody>
      </table></div> : <p>No recognised requirements were found in the available text.</p>}
      <h4>Salary research</h4><p>{assessment.salary} · {assessment.salaryStatus}</p>
      {assessment.salarySources.map(item => <p key={item.label}>
        <a href={safeUrl(item.url) || undefined} target="_blank" rel="noopener noreferrer">{item.title}</a>
        {' · '}{item.label}. {item.verified ? '' : 'Unverified search result. '}{item.excerpt}
      </p>)}
      <h4>Recruitment contact</h4>
      <p>{assessment.contact.split('|').slice(0, 2).join(' · ')} · {assessment.contactStatus}</p>
      {safeUrl(assessment.contact.split('|')[2]?.trim()) && <a href={safeUrl(assessment.contact.split('|')[2].trim())}
        target="_blank" rel="noopener noreferrer">Public LinkedIn profile</a>}
      {assessment.email && <p>Public work email · {assessment.email}</p>}
      <p className="subtle-note">Contact suggestions need vacancy-specific confirmation.</p>
      {assessment.sources.length > 0 && <ul>{assessment.sources.map(item => <li key={item.url}>
        <a href={safeUrl(item.url) || undefined} target="_blank" rel="noopener noreferrer">{item.status}</a>
      </li>)}</ul>}
      <p>Would you like me to create a tailored CV and short cover letter for this role?</p>
      <button className="button button-primary" disabled={Boolean(working)} onClick={() => void build()}>
        {working === 'documents' ? 'Building and checking PDFs…' : 'Build CV and cover letter'}
      </button>
      {downloads.length > 0 && <ul>{downloads.map(file => <li key={file.name}>
        <a href={file.url} download={file.name}>{file.name}</a>
      </li>)}</ul>}
    </>}
    {tracker && <p><a href={tracker} target="_blank" rel="noopener noreferrer">Open application tracker</a></p>}
  </section>;
}
