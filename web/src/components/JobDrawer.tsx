import { useEffect, useRef } from 'react';
import { ExternalLink, MapPin, ShieldCheck, X } from 'lucide-react';
import type { Job } from '../types';
import { safeUrl, sourceLabel } from '../lib/data';
import { Badge } from './Badge';

export function JobDrawer({ job, onEvaluate, onClose }: { job: Job; onEvaluate: () => void; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    const focused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog?.showModal();
    return () => { dialog?.close(); focused?.focus(); };
  }, []);
  const contactParts = job.contact.split('|').map(part => part.trim());
  const contactUrl = safeUrl(contactParts[2]);
  const evidence = ['freshness_evidence', 'work_model_evidence', 'contact_evidence', 'verification_sources'].filter(key => job.evidence[key]);
  return <dialog ref={ref} className="job-drawer" onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => { if (event.target === ref.current) onClose(); }} aria-labelledby="job-detail-title">
    <div className="drawer-content"><div className="drawer-top"><span className="section-eyebrow">ROLE DETAILS</span><button className="icon-button" aria-label="Close job details" onClick={onClose}><X size={20} /></button></div>
      <div className="drawer-company"><span className="company-avatar company-avatar-large">{job.company.slice(0, 2).toUpperCase()}</span><span>{job.company}<small className="job-source">{job.foundOn.map(sourceLabel).join(' · ')}</small></span></div>
      <h2 id="job-detail-title">{job.title}</h2><p className="drawer-location"><MapPin size={14} />{job.location}</p>
      <div className="drawer-badges"><Badge value={job.workModel} kind="work" /><Badge value={job.fit} kind="fit" />{job.imported && <span className="badge badge-neutral">Imported data</span>}</div>
      {(job.applyUrl || job.url) && <a className="button button-primary drawer-apply" href={job.applyUrl || job.url} target="_blank" rel="noopener noreferrer">View original listing<ExternalLink size={15} /></a>}
      <div className="detail-grid"><div><span className="control-label">SALARY</span><strong>{job.salary}</strong></div><div><span className="control-label">POSTED</span><strong>{job.posted || 'Not stated'}</strong></div></div>
      <section className="drawer-section"><h3><ShieldCheck size={16} />Fit assessment</h3><p>{job.why || 'No fit explanation was provided in this result.'}</p>{job.imported && <p className="subtle-note">Ratings in this CSV are displayed as supplied. Importing a file does not verify its contents.</p>}</section>
      <section className="drawer-section">
        <h3>Application assistant</h3>
        <button type="button" className="button button-primary" onClick={onEvaluate}>Enhanced evaluation</button>
      </section>
      <section className="drawer-section"><div className="drawer-section-title"><h3>Hiring contact</h3><Badge value={job.contactStatus} /></div>{job.contact !== 'Not found' ? <><strong>{contactParts[0]}</strong>{contactParts[1] && <p>{contactParts[1]}</p>}{contactUrl && <a className="inline-link" href={contactUrl} target="_blank" rel="noopener noreferrer">View LinkedIn profile<ExternalLink size={12} /></a>}</> : <p>Not found</p>}{['blocked', 'incomplete', 'unverified'].includes(job.contactStatus) && <p className="subtle-note">A reliable current contact could not be verified. This does not mean there is no hiring contact.</p>}{job.email && <p className="contact-email">Email listed: {job.email}{job.imported ? ' (unverified import)' : ''}</p>}</section>
      <section className="drawer-section"><h3>Job description</h3><div className="job-description">{job.description || 'The source did not include a description.'}</div></section>
      {evidence.length > 0 && <section className="drawer-section"><h3>Source evidence</h3>{evidence.map(key => <details className="evidence-detail" key={key}><summary>{key.replace(/_/g, ' ')}</summary><pre>{typeof job.evidence[key] === 'string' ? job.evidence[key] as string : JSON.stringify(job.evidence[key], null, 2)}</pre></details>)}</section>}
    </div>
  </dialog>;
}
