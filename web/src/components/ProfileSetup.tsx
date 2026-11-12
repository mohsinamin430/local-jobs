import { useEffect, useState } from 'react';
import { request } from '../lib/api';

interface Profile {
  name: string; title: string; city: string; email: string; trackerUrl: string;
  roles: string[]; workModels: string[]; sources: string[]; maxAgeHours: number;
  skills: string[]; evidence: string; projects: string; education: string; links: string[];
  excludedEmployers: string[]; allowedAgencies: string[]; contactResearch: boolean; includeRecruiters: boolean;
}
interface Settings { values: Profile; configured: boolean }
const steps = ['Job search', 'Experience & CV', 'Review'];
const arrangements = [{ id: 'remote', name: 'Remote across the UK' }, { id: 'hybrid', name: 'Hybrid across the UK' }, { id: 'on-site', name: 'On-site in your town or city' }];

export function ProfileSetup({ boards, onSaved }: { boards: { id: string; name: string }[]; onSaved: () => void }) {
  const [settings, setSettings] = useState<Settings>();
  const [step, setStep] = useState(0);
  const [error, setError] = useState('');
  const [working, setWorking] = useState(false);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    let current = true;
    request<Settings>('/api/profile').then(value => { if (current) setSettings(value); })
      .catch(cause => { if (current) setError(cause.message); });
    return () => { current = false; };
  }, []);
  const values = settings?.values;
  function change<K extends keyof Profile>(key: K, value: Profile[K]) {
    setSaved(false);
    setSettings(old => old && ({ ...old, values: { ...old.values, [key]: value } }));
  }
  async function importCv(file?: File) {
    if (!file || !values) return;
    if (file.size > 6 * 1024 * 1024) { setError('Choose a PDF smaller than 6 MB.'); return; }
    setError(''); setWorking(true);
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = '';
      for (let index = 0; index < bytes.length; index += 8192) {
        binary += String.fromCharCode(...bytes.subarray(index, index + 8192));
      }
      const result = await request<{ text: string }>('/api/profile/import-cv', {
        method: 'POST', body: JSON.stringify({ pdf: btoa(binary) }),
      });
      change('evidence', result.text);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not import the CV.'); }
    finally { setWorking(false); }
  }
  async function save() {
    if (!values) return;
    setError(''); setWorking(true);
    try {
      setSettings(await request<Settings>('/api/profile', { method: 'POST', body: JSON.stringify(values) }));
      setSaved(true);
      onSaved();
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not save the profile.'); }
    finally { setWorking(false); }
  }

  return <section className="panel criteria-card profile-setup">
    <h2>{settings?.configured ? 'Profile & preferences' : 'Set up your local profile'}</h2>
    <p>Job Desk uses this profile for your searches, evaluations and application documents.</p>
    {error && <p role="alert" className="profile-error">{error}</p>}
    {saved && <p role="status">Profile saved. Your next dashboard search will use these preferences.</p>}
    {!values && !error && <p role="status">Loading profile…</p>}
    {values && <>
      <ol className="profile-steps">{steps.map((name, index) => <li key={name} aria-current={step === index ? 'step' : undefined}>{name}</li>)}</ol>
      <fieldset disabled={working} className="profile-fields">
        {step === 0 && <>
          <label>UK town or city<input value={values.city} onChange={event => change('city', event.target.value)} placeholder="Leeds" /></label>
          <LineList label="Target job titles (one per line)" value={values.roles} onChange={value => change('roles', value)} />
          <Options label="Work arrangements" options={arrangements} value={values.workModels} onChange={value => change('workModels', value)} />
          <Options label="Job boards" options={boards} value={values.sources} onChange={value => change('sources', value)} />
          <label>Posting window<select value={values.maxAgeHours} onChange={event => change('maxAgeHours', Number(event.target.value))}>
            <option value="24">Past 24 hours</option><option value="72">Past 3 days</option><option value="168">Past 7 days</option>
          </select></label>
          <LineList label="Excluded employers (optional)" value={values.excludedEmployers} onChange={value => change('excludedEmployers', value)} />
          <label className="profile-check"><input type="checkbox" checked={values.includeRecruiters} onChange={event => change('includeRecruiters', event.target.checked)} />Include recruitment agencies</label>
          {!values.includeRecruiters && <LineList label="Recruitment agency exceptions (optional)" value={values.allowedAgencies} onChange={value => change('allowedAgencies', value)} />}
        </>}
        {step === 1 && <>
          <label>Full name<input value={values.name} onChange={event => change('name', event.target.value)} /></label>
          <label>CV headline<input value={values.title} onChange={event => change('title', event.target.value)} /></label>
          <label>Email for CV (optional)<input type="email" value={values.email} onChange={event => change('email', event.target.value)} /></label>
          <LineList label="Skills (one per line)" value={values.skills} onChange={value => change('skills', value)} />
          <label>Import experience text from a PDF CV (optional)
            <input type="file" accept=".pdf,application/pdf" disabled={Boolean(values.evidence.trim())} onChange={event => { void importCv(event.target.files?.[0]); event.target.value = ''; }} />
            <small>Review the extracted text and move projects, education and links into their own fields. Clear the experience box to import another file.</small>
          </label>
          <label>Experience and achievements<textarea rows={8} value={values.evidence} onChange={event => change('evidence', event.target.value)} /></label>
          <label>Projects<textarea rows={4} value={values.projects} onChange={event => change('projects', event.target.value)} /></label>
          <label>Education<textarea rows={3} value={values.education} onChange={event => change('education', event.target.value)} /></label>
          <LineList label="CV links (one HTTPS link per line)" value={values.links} onChange={value => change('links', value)} />
        </>}
        {step === 2 && <>
          <p><strong>{values.name}</strong> · {values.title} · {values.city}, UK</p>
          <p>{values.roles.filter(role => role.trim()).length} job titles × {values.workModels.length} work arrangements × {values.sources.length} sources. Jobs must be posted within {values.maxAgeHours} hours.</p>
          <p>{values.roles.filter(role => role.trim()).join(' · ')}</p>
          <p>{values.includeRecruiters ? 'Recruitment agencies included.' : 'Agency exceptions apply.'}</p>
          <p>Documents use the standard Job Desk template. Review the experience text before saving. It will appear in your CV.</p>
          <label className="profile-check"><input type="checkbox" checked={values.contactResearch} onChange={event => change('contactResearch', event.target.checked)} />Research public hiring contacts</label>
          <label>Application tracker link (optional)<input type="url" value={values.trackerUrl} onChange={event => change('trackerUrl', event.target.value)} placeholder="https://" /></label>
        </>}
      </fieldset>
      <div className="profile-actions">
        {step > 0 && <button className="button button-quiet" disabled={working} onClick={() => { setError(''); setStep(step - 1); }}>Back</button>}
        {step < 2 ? <button className="button button-primary" disabled={working} onClick={() => {
          if (step === 0 && (!values.city.trim() || !values.roles.some(role => role.trim()) || !values.workModels.length || !values.sources.length)) {
            setError('Enter a town or city and a job title, and select a work arrangement and source.');
          } else if (step === 1 && (!values.name.trim() || !values.title.trim())) {
            setError('Enter your name and CV headline.');
          } else { setError(''); setStep(step + 1); }
        }}>Continue</button> : <button className="button button-primary" disabled={working} onClick={() => void save()}>{working ? 'Saving…' : 'Save profile'}</button>}
      </div>
    </>}
  </section>;
}

function LineList({ label, value, onChange }: { label: string; value: string[]; onChange: (value: string[]) => void }) {
  return <label>{label}<textarea rows={3} value={value.join('\n')} onChange={event => onChange(event.target.value.split('\n'))} /></label>;
}

function Options({ label, options, value, onChange }: { label: string; options: { id: string; name: string }[]; value: string[]; onChange: (value: string[]) => void }) {
  return <fieldset><legend>{label}</legend>{options.map(option => <label className="profile-check" key={option.id}>
    <input type="checkbox" checked={value.includes(option.id)} onChange={event => onChange(event.target.checked ? [...value, option.id] : value.filter(id => id !== option.id))} />
    {option.name}
  </label>)}</fieldset>;
}
