import { useEffect, useState } from 'react';
import { AlertTriangle, Link2, RefreshCw, ShieldCheck } from 'lucide-react';
import { request } from '../lib/api';
import type { ExternalDigestState } from '../types';

export function ExternalDigestSettings({ onChanged }: { onChanged: () => void }) {
  const [status, setStatus] = useState<ExternalDigestState | null>(null);
  const [folder, setFolder] = useState('');
  const [prefix, setPrefix] = useState('Jobs');
  const [tab, setTab] = useState('');
  const [client, setClient] = useState<Record<string, unknown> | null>(null);
  const [clientName, setClientName] = useState('');
  const [working, setWorking] = useState('');
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    void request<ExternalDigestState>('/api/external-digest', { signal: controller.signal }).then(value => {
      if (controller.signal.aborted) return;
      setStatus(value); setFolder(value.folder); setPrefix(value.prefix); setTab(value.tab);
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Could not load the connection.'); });
    return () => controller.abort();
  }, []);
  async function action(kind: 'settings' | 'connect' | 'refresh') {
    setWorking(kind); setError('');
    try {
      if (kind === 'connect') {
        const value = await request<{ url: string }>('/api/external-digest/connect', { method: 'POST', body: '{}' });
        if (!value.url.startsWith('https://accounts.google.com/o/oauth2/v2/auth?')) throw new Error('Unexpected authorisation URL.');
        window.location.assign(value.url);
      } else {
        const payload = kind === 'settings' ? { folder, prefix, tab, ...(client ? { client } : {}) } : {};
        const value = await request<ExternalDigestState>(`/api/external-digest/${kind}`, { method: 'POST', body: JSON.stringify(payload) });
        setStatus(value);
        if (kind === 'settings') { setFolder(value.folder); setPrefix(value.prefix); setTab(value.tab); setClient(null); setClientName(''); }
        onChanged();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not complete the connection.');
      if (kind === 'refresh') {
        onChanged();
        try { setStatus(await request<ExternalDigestState>('/api/external-digest')); } catch { /* Preserve the actionable refresh error. */ }
      }
    } finally { setWorking(''); }
  }
  async function importClient(file?: File) {
    if (!file) return;
    setError('');
    try {
      if (file.size > 32_000) throw new Error('Choose the small OAuth client JSON downloaded for a Google Desktop app.');
      const value: unknown = JSON.parse(await file.text());
      if (!value || typeof value !== 'object' || !('installed' in value)) throw new Error('Choose a Google Desktop app OAuth client JSON.');
      setClient(value as Record<string, unknown>); setClientName(file.name);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Could not read the client file.'); }
  }
  const dirty = Boolean(client) || Boolean(status && (status.folder !== folder || status.prefix !== prefix || status.tab !== tab));
  return <section className="panel criteria-card" id="external-digest-settings" aria-labelledby="external-digest-heading">
    <h2 id="external-digest-heading">External Google Sheets digest</h2>
    <p>Compare against your separate daily Jobs sheet as well as deduplicating across the boards in this app. The index refreshes before each search.</p>
    <p>Google access is read-only. Your sheets are never changed. A missing sheet or failed check stays incomplete.</p>
    {error && <p role="alert" className="profile-error">{error}</p>}
    {status && <div className={`alert ${status.status === 'ok' ? 'alert-info' : 'alert-warning'}`}>
      {status.status === 'ok' ? <ShieldCheck size={18} /> : <AlertTriangle size={18} />}
      <div><strong>{status.connected ? 'Google connected' : 'Google connection needed'}</strong><p>{status.message}</p>
        {status.asOf && <p>Verified digest date: {status.asOf}{status.count !== null && ` · ${status.count} indexed jobs`}</p>}
      </div>
    </div>}
    <fieldset disabled={Boolean(working)} className="profile-fields">
      <label>Google Drive folder URL or ID<input value={folder} onChange={event => setFolder(event.target.value)} placeholder="https://drive.google.com/drive/folders/…" /></label>
      <label>Daily sheet title prefix<input value={prefix} onChange={event => setPrefix(event.target.value)} /><small>For example, Jobs finds “Jobs -- YYYY-MM-DD” for the execution date in UK time.</small></label>
      <label>Exact jobs tab name (optional)<input value={tab} onChange={event => setTab(event.target.value)} placeholder="Leave empty to detect a single jobs tab" /></label>
      <label>Google Desktop app OAuth client JSON<input type="file" accept=".json,application/json" onChange={event => { void importClient(event.target.files?.[0]); event.target.value = ''; }} /><small>{clientName || (status?.clientUploaded ? 'A client file is saved locally.' : 'Download this from your Google Cloud project. It is kept in the app’s private data volume.')}</small></label>
    </fieldset>
    <div className="profile-actions">
      <button className="button button-primary" disabled={Boolean(working) || !folder.trim()} onClick={() => void action('settings')}>{working === 'settings' ? 'Saving…' : 'Save digest settings'}</button>
      <button className="button button-quiet" disabled={Boolean(working) || !status?.clientUploaded || dirty} onClick={() => void action('connect')}><Link2 size={15} />Connect Google</button>
      <button className="button button-quiet" disabled={Boolean(working) || !status?.connected || dirty} onClick={() => void action('refresh')}><RefreshCw size={15} className={working === 'refresh' ? 'spin' : ''} />{working === 'refresh' ? 'Checking…' : 'Refresh index'}</button>
    </div>
    <details className="coverage-details"><summary>One-time Google setup</summary><ol>
      <li>In Google Cloud, enable the Google Drive API and Google Sheets API.</li>
      <li>Configure the OAuth consent screen for your own account. If the app is in Testing, add yourself as a test user.</li>
      <li>Create an OAuth client with application type Desktop app and download its JSON.</li>
      <li>Upload it here, save the digest folder, then choose Connect Google and allow both read-only permissions.</li>
      <li>Choose Refresh index. A same-day sheet with recognised job columns is required. Testing-mode authorisation may need reconnecting when Google expires it.</li>
    </ol></details>
  </section>;
}

export function ExternalDigestCoverage({ summary, status, onSetup }: { summary?: Record<string, unknown>; status?: ExternalDigestState; onSetup: () => void }) {
  const raw = summary?.cross_digest_sources;
  const reports = Array.isArray(raw) ? raw.filter((value): value is Record<string, unknown> => Boolean(value) && typeof value === 'object') : [];
  if (!reports.length && !status?.configured) return null;
  const complete = reports.length > 0 && reports.every(report => report.status === 'ok');
  return <section className="panel criteria-card" aria-labelledby="external-comparison-title">
    <h2 id="external-comparison-title">External digest comparison · {complete ? 'Checked' : 'Incomplete'}</h2>
    <p>Duplicates across this app’s boards are merged independently of this check.</p>
    {reports.map((report, index) => <p key={index}>{report.status === 'ok'
      ? `${Number(report.count) || 0} jobs indexed from the separate digest dated ${String(report.as_of || 'unknown')}.`
      : String(report.error || 'The separate Google Sheets digest has not been fully checked.')}</p>)}
    {!reports.length && <p>{status?.message}</p>}
    <button className="button button-quiet" onClick={onSetup}><Link2 size={15} />Manage Google digest connection</button>
  </section>;
}
