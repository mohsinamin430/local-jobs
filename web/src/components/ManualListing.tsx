import { useState } from 'react';
import { normalizeJob, safeUrl } from '../lib/data';
import type { Job, Selection } from '../types';
import type { ListingEvaluation } from '../hooks/useListingEvaluation';
import { ApplicationAssistant } from './ApplicationAssistant';

export interface SelectedListing {
  job: Job;
  selection: Selection | null;
  session: number;
}

interface Props {
  selected: SelectedListing | null;
  evaluation: ListingEvaluation;
  onNewListing: () => void;
  ready: boolean;
  trackerUrl?: string;
  onSetup: () => void;
}

export function ManualListing({ ready, trackerUrl, onSetup, selected, evaluation, onNewListing }: Props) {
  const [draft, setDraft] = useState({
    title: '', company: '', location: '', workModel: '', salary: '', url: '', description: '',
  });
  function change(field: keyof typeof draft, value: string) {
    setDraft(previous => ({ ...previous, [field]: value }));
  }
  if (!ready) {
    return <section className="panel criteria-card">
      <h2>Set up your candidate profile</h2>
      <p>Add your experience so Job Desk can compare this listing with your CV.</p>
      <button className="button button-primary" onClick={onSetup}>Start setup</button>
    </section>;
  }
  if (selected) {
    const listingUrl = safeUrl(selected.job.applyUrl) || safeUrl(selected.job.url);
    return <div className="manual-listing panel criteria-card">
      <h2>{selected.job.title}</h2>
      <p>{selected.job.company} · {selected.job.location}</p>
      {listingUrl && <p><a href={listingUrl} target="_blank" rel="noopener noreferrer">View original listing</a></p>}
      <button type="button" className="button button-quiet" onClick={onNewListing}>Evaluate a different listing</button>
      <ApplicationAssistant key={selected.session} job={selected.job} selection={selected.selection}
        trackerUrl={trackerUrl} evaluation={evaluation} />
    </div>;
  }

  const job = {
    ...normalizeJob(draft, 0, true),
    title: draft.title.trim(),
    company: draft.company.trim(),
    location: draft.location.trim(),
    salary: draft.salary.trim(),
    workModel: draft.workModel,
  };
  const fields = <>
    <label>Vacancy URL<input type="url" pattern="https://.+" placeholder="https://company.com/careers/job"
      maxLength={2000} required={!draft.description.trim()} value={draft.url}
      onChange={event => change('url', event.target.value)} aria-describedby="manual-listing-help" /></label>
    <p id="manual-listing-help" className="subtle-note">Paste the exact job link. Job Desk will retrieve the vacancy details, check the requirements against your CV, and research salary and public hiring contacts.</p>
    <details className="evidence-detail">
      <summary>Add advert text if the page is inaccessible</summary>
      <p>If the advert needs a login or cannot be read, enter its title and employer and paste the full description.</p>
      <div className="manual-listing-grid">
        <label>Job title<input maxLength={180} value={draft.title} onChange={event => change('title', event.target.value)} /></label>
        <label>Employer<input maxLength={180} value={draft.company} onChange={event => change('company', event.target.value)} /></label>
        <label>Location (optional)<input maxLength={250} value={draft.location} onChange={event => change('location', event.target.value)} /></label>
        <label>Work arrangement<select value={draft.workModel} onChange={event => change('workModel', event.target.value)}>
          <option value="">Not stated</option><option value="remote">Remote</option><option value="hybrid">Hybrid</option><option value="on-site">On-site</option>
        </select></label>
        <label>Published salary (optional)<input maxLength={250} value={draft.salary} onChange={event => change('salary', event.target.value)} /></label>
      </div>
      <label>Job description<textarea rows={10} maxLength={35000} minLength={100}
        value={draft.description} onChange={event => change('description', event.target.value)} /></label>
    </details>
  </>;
  return <div className="manual-listing panel criteria-card">
    <ApplicationAssistant key="manual" job={job} selection={null} evaluation={evaluation} trackerUrl={trackerUrl} listingFields={fields} />
  </div>;
}
