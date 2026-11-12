import { useEffect, useState, type ReactNode } from 'react';
import type { Job, Selection } from '../types';
import { safeUrl } from '../lib/data';
import { request } from '../lib/api';
import type { ListingEvaluation } from '../hooks/useListingEvaluation';
import './application-assistant.css';
import { Badge, badgeTone } from './Badge';

interface Download { name: string; url: string }

export function ApplicationAssistant({ job, selection, trackerUrl, listingFields, evaluation }: { job: Job; selection: Selection | null; trackerUrl?: string; listingFields?: ReactNode; evaluation: ListingEvaluation }) {
  const tracker = safeUrl(trackerUrl);
  const [pasted, setPasted] = useState('');
  const { result } = evaluation;
  const assessment = result.status === 'ready' ? result.assessment : null;
  const [downloads, setDownloads] = useState<Download[]>([]);
  const [building, setBuilding] = useState(false);
  const [documentError, setDocumentError] = useState('');
  const busy = result.status === 'evaluating' || building;
  const error = result.status === 'error' ? result.message : documentError;
  useEffect(() => () => downloads.forEach(file => URL.revokeObjectURL(file.url)), [downloads]);

  function clearDocuments() {
    setDocumentError('');
    setDownloads([]);
  }

  async function build() {
    if (!assessment) return;
    setDocumentError('');
    setBuilding(true);
    try {
      const result = await request<{ files: { name: string; content: string }[] }>('/api/applications/documents', {
        method: 'POST', body: JSON.stringify({ assessmentId: assessment.id }),
      });
      setDownloads(result.files.map(file => {
        const bytes = Uint8Array.from(atob(file.content), char => char.charCodeAt(0));
        const type = file.name.endsWith('.pdf') ? 'application/pdf' : 'application/zip';
        return { name: file.name, url: URL.createObjectURL(new Blob([bytes], { type })) };
      }));
    } catch (cause) {
      setDocumentError(cause instanceof Error ? cause.message : 'Document creation failed.');
    } finally {
      setBuilding(false);
    }
  }

  return <section className="drawer-section application-assistant">
    <h3>Application assistant</h3>
    <p>Review the vacancy, compare it with your CV and decide whether to apply.</p>
    <form onSubmit={event => {
      event.preventDefault();
      clearDocuments();
      void evaluation.evaluate(job, selection, listingFields ? job.description : pasted);
    }} onChange={() => { evaluation.reset(); clearDocuments(); }}>
      <fieldset className="listing-fields" disabled={busy}>
      {listingFields || <details className="evidence-detail">
      <summary>Paste vacancy text if the listing is incomplete</summary>
      <textarea aria-label="Vacancy text" rows={5} maxLength={35000} value={pasted}
        onChange={event => setPasted(event.target.value)} />
    </details>}
      <button type="submit" className="button button-primary" disabled={busy}>
        {assessment ? 'Evaluate again' : 'Evaluate listing'}
      </button>
      </fieldset>
    </form>
    {result.status === 'evaluating' && <p role="status">Researching vacancy…</p>}
    {error && <p className="assessment-error" role="alert">{error}</p>}
    {assessment && <>
      <div className={'assessment-summary assessment-' + badgeTone(assessment.recommendation)}>
        <h4><Badge value={assessment.recommendation} kind="fit" /></h4>
        <p>{assessment.reason}</p>
      </div>
      {assessment.flags.length > 0 && <ul className="assessment-flags" aria-label="Points to review">{assessment.flags.map(flag => <li key={flag}>{flag}</li>)}</ul>}
      <p className="subtle-note">Description evidence · {assessment.source}</p>
      <dl>{Object.entries(assessment.details).map(([name, value]) =>
        <div key={name} className={value.startsWith('Not stated') ? 'assessment-unknown'
          : /clearance|nationality|travel/i.test(name) ? 'assessment-review' : undefined}>
          <dt><strong>{name}</strong></dt><dd>{value}</dd>
        </div>)}</dl>
      <h4>Requirements and CV evidence</h4>
      {assessment.requirements.length > 0 ? <div className="table-scroll"><table>
        <thead><tr><th>Requirement</th><th>My relevant experience</th><th>Match</th></tr></thead>
        <tbody>{assessment.requirements.map(row => <tr className={'requirement-row requirement-' + badgeTone(row.match)} key={`${row.requirement}-${row.desirable}`}>
          <td>{row.requirement}{row.desirable && <small> · Desirable</small>}<p className="subtle-note">{row.quote}</p></td>
          <td>{row.experience}</td><td><Badge value={row.match} kind="fit" /></td>
        </tr>)}</tbody>
      </table></div> : <p>No recognised requirements were found in the available text.</p>}
      <h4>Salary research</h4><p>{assessment.salary} · {assessment.salaryStatus}</p>
      {assessment.salarySources.map(item => <p key={item.label}>
        <a href={safeUrl(item.url) || undefined} target="_blank" rel="noopener noreferrer">{item.title}</a>
        {' · '}{item.label}. {item.verified ? '' : 'Unverified search result. '}{item.excerpt}
      </p>)}
      <h4>Recruitment contact</h4>
      <p>{assessment.contact.split('|').slice(0, 2).join(' · ')} · <Badge value={assessment.contactStatus} /></p>
      {safeUrl(assessment.contact.split('|')[2]?.trim()) && <a href={safeUrl(assessment.contact.split('|')[2].trim())}
        target="_blank" rel="noopener noreferrer">Public LinkedIn profile</a>}
      {assessment.email && <p>Public work email · {assessment.email}</p>}
      <p className="subtle-note">Contact suggestions need vacancy-specific confirmation.</p>
      {assessment.sources.length > 0 && <ul>{assessment.sources.map(item => <li key={item.url}>
        <a href={safeUrl(item.url) || undefined} target="_blank" rel="noopener noreferrer">{item.status}</a>
      </li>)}</ul>}
      <p>Would you like me to create a tailored CV and short cover letter for this role?</p>
      <button className="button button-primary" disabled={busy} onClick={() => void build()}>
        {building ? 'Building and checking PDFs…' : 'Build CV and cover letter'}
      </button>
      {downloads.length > 0 && <ul className="assessment-downloads" aria-label="Application downloads">{downloads.map(file => <li key={file.name}>
        <a href={file.url} download={file.name}>{file.name}</a>
      </li>)}</ul>}
    </>}
    {tracker && <p><a href={tracker} target="_blank" rel="noopener noreferrer">Open application tracker</a></p>}
  </section>;
}
