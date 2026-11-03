import { useMemo, useRef, useState, type ReactNode } from 'react';
import { Activity, AlertTriangle, ArrowUpRight, BriefcaseBusiness, ChevronRight, Clock3, FileSpreadsheet, FileUp, History, LayoutDashboard, LoaderCircle, MapPin, RefreshCw, ShieldCheck, SlidersHorizontal, Sparkles, Users, X } from 'lucide-react';
import type { CsvImport, Job, Run } from './types';
import { useDashboard } from './hooks/useDashboard';
import { duration, formatDate, normalizeJob, parseCsv } from './lib/data';
import { Badge } from './components/Badge';
import { JobDrawer } from './components/JobDrawer';
import { JobTable } from './components/JobTable';
import { RunControls } from './components/RunControls';
import { SourceCoverage } from './components/SourceCoverage';
import { ProfileSetup } from './components/ProfileSetup';

type View = 'results' | 'history' | 'criteria';
function summaryNotes(summary?: Record<string, unknown>): string[] {
  if (!summary) return [];
  return [...(Array.isArray(summary.errors) ? summary.errors : []), ...(Array.isArray(summary.warnings) ? summary.warnings : [])].filter(value => typeof value === 'string') as string[];
}

function App() {
  const dashboard = useDashboard();
  const [view, setView] = useState<View>('results');
  const [imported, setImported] = useState<CsvImport | null>(null);
  const [importError, setImportError] = useState('');
  const [selectedJob, setSelectedJob] = useState<Job | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const jobs = useMemo(() => imported?.jobs || (dashboard.detail?.jobs || []).map((job, index) => normalizeJob(job, index)), [imported, dashboard.detail?.jobs]);
  const selectedRun = dashboard.detail?.run;
  const historical = !imported && selectedRun?.personalized === false;
  const profile = dashboard.overview?.profile;
  const notes = summaryNotes(dashboard.detail?.summary);
  const incomplete = !imported && Boolean(selectedRun && (['partial', 'failed', 'interrupted', 'running', 'rate_limited'].includes(selectedRun.status) || dashboard.detail?.summary.coverage_limited));
  const workTypes = jobs.reduce<Record<string, number>>((counts, job) => ({ ...counts, [job.workModel]: (counts[job.workModel] || 0) + 1 }), {});
  const strong = imported ? jobs.filter(job => job.fit === 'Strong').length : selectedRun?.strong ?? 0;
  const contacts = imported ? jobs.filter(job => job.contact !== 'Not found').length : selectedRun?.contacts ?? 0;

  async function importCsv(file?: File) {
    if (!file) return;
    setImportError('');
    if (file.size > 8 * 1024 * 1024) { setImportError('Choose a CSV smaller than 8 MB for the local viewer.'); return; }
    try {
      const parsed = parseCsv(await file.text());
      setImported({ name: file.name, ...parsed });
      setSelectedJob(null); setView('results');
    } catch (cause) { setImportError(cause instanceof Error ? cause.message : 'Could not read this CSV.'); }
    finally { if (inputRef.current) inputRef.current.value = ''; }
  }

  function openRun(run: Run) {
    setImported(null); setSelectedJob(null); setView('results');
    dashboard.setSelection({ id: run.id, source: run.source });
  }

  return <div className="app-shell">
    <a href="#main-content" className="skip-link">Skip to content</a>
    <aside className="sidebar">
      <a className="brand" href="#" onClick={event => { event.preventDefault(); setView('results'); }} aria-label="Job desk home"><span className="brand-mark"><BriefcaseBusiness size={20} strokeWidth={1.8} /></span><span>job<span className="brand-word-light">desk</span><span className="brand-period">.</span></span></a>
      <span className="sidebar-label">WORKSPACE</span>
      <nav aria-label="Main navigation">
        <button className={`nav-item ${view === 'results' ? 'active' : ''}`} onClick={() => setView('results')} aria-current={view === 'results' ? 'page' : undefined}><LayoutDashboard size={18} />Job search<span className="nav-count">{jobs.length}</span></button>
        <button className={`nav-item ${view === 'history' ? 'active' : ''}`} onClick={() => setView('history')} aria-current={view === 'history' ? 'page' : undefined}><History size={18} />Run history</button>
        <button className={`nav-item ${view === 'criteria' ? 'active' : ''}`} onClick={() => setView('criteria')} aria-current={view === 'criteria' ? 'page' : undefined}><SlidersHorizontal size={18} />Profile & preferences</button>
      </nav>
      <div className="sidebar-file-card"><span className="file-card-icon"><FileSpreadsheet size={20} /></span><h3>Already have results?</h3><p>Give your CSV a better view.</p><button className="button button-quiet" onClick={() => inputRef.current?.click()}><FileUp size={14} />Open a CSV</button></div>
      <div className="sidebar-bottom"><div className="local-note"><span className="online-dot" />Runs on your machine</div><div className="profile-card"><div className="profile-avatar">{profile?.name.split(/\s+/).slice(0, 2).map(word => word[0]).join('') || 'JD'}</div><div><strong>{profile?.name || 'Local candidate'}</strong><span>{profile?.location || 'Job Desk'}</span></div><span className="profile-online" /></div></div>
    </aside>
    <main id="main-content" className="main-content">
      <header className="topbar"><div className="breadcrumb">Workspace<ChevronRight size={12} /><span>{view === 'results' ? 'Job search' : view === 'history' ? 'Run history' : 'Profile & preferences'}</span></div><span className={`connection-label ${dashboard.error && !dashboard.overview ? 'connection-offline' : ''}`}><span className="online-dot" />{dashboard.overview ? 'LOCAL WORKSPACE' : dashboard.loading ? 'CONNECTING' : 'SERVICE OFFLINE'}</span></header>
      <div className="page-content">
        <div className="page-heading"><div><span className="section-eyebrow">BUILT AROUND YOU</span><h1>{view === 'results' ? 'Your next chapter starts here.' : view === 'history' ? 'Every search, in one place.' : 'A search that understands you.'}</h1><p>{view === 'results' ? 'Less noise. More opportunities that fit your next move.' : view === 'history' ? 'Review previous results, coverage and source status.' : 'Manage the profile behind your local job search.'}</p></div><button className="button button-quiet refresh-button" onClick={() => { void dashboard.refresh(); }} disabled={dashboard.loading} aria-label="Refresh workspace"><RefreshCw size={15} className={dashboard.loading ? 'spin' : ''} /><span>Refresh</span></button></div>
        {dashboard.error && <div className="alert alert-error" role="alert"><AlertTriangle size={18} /><div><strong>Something needs your attention</strong><p>{dashboard.error}</p></div><button className="text-button" onClick={() => { void dashboard.refresh(); }}>Try again</button></div>}
        {importError && <div className="alert alert-error" role="alert"><FileSpreadsheet size={18} /><div><strong>Couldn’t open this file</strong><p>{importError}</p></div><button className="icon-button" aria-label="Dismiss CSV error" onClick={() => setImportError('')}><X size={16} /></button></div>}
        {view === 'results' && <>
          {profile?.ready === false && <section className="panel criteria-card"><h2>Set up your job search</h2><p>Tell Job Desk what roles you’re looking for and what experience you want it to use.</p><button className="button button-primary" onClick={() => setView('criteria')}>Start setup</button></section>}
          {profile?.ready !== false && <RunControls profile={profile} task={dashboard.task} submitting={dashboard.submitting} connected={Boolean(dashboard.overview)} onRun={dashboard.startRun} onCancel={dashboard.cancelRun} cooldown={dashboard.overview?.rateLimit} resumeAvailable={dashboard.overview?.resumeAvailable} resumeOptions={dashboard.overview?.resumeOptions} sources={dashboard.overview?.jobSources} sourceRates={dashboard.overview?.sourceRates} googleSearches={dashboard.overview?.googleSearches} publicSearches={dashboard.overview?.publicSearches} />}
          <div className="metrics-grid">
            <Metric label={imported ? 'Imported rows' : historical ? 'Saved listings' : 'Qualifying jobs'} value={imported ? jobs.length : selectedRun?.qualifying ?? '—'} icon={<BriefcaseBusiness size={18} />} note={imported ? 'From your selected CSV' : historical ? 'Earlier collection; personal checks not applied' : 'After verification & deduplication'} />
            <Metric label={imported ? 'Marked Strong' : 'Strong matches'} value={historical ? '—' : imported || selectedRun ? strong : '—'} icon={<Sparkles size={18} />} note={imported ? 'Rating supplied in the file' : historical ? 'Not rated in this historical run' : 'Closest to your experience'} color="green" />
            <Metric label={imported ? 'Contacts listed' : 'Verified contacts'} value={historical ? '—' : imported || selectedRun ? contacts : '—'} icon={<Users size={18} />} note={imported ? 'Not independently verified' : historical ? 'Not verified in this historical run' : 'Current employment checked'} color="purple" />
            <Metric label="Work arrangements" value={imported || selectedRun ? <span className="work-metric">{workTypes.remote || 0}<small>R</small><i />{workTypes.hybrid || 0}<small>H</small><i />{workTypes['on-site'] || 0}<small>O</small></span> : '—'} icon={<MapPin size={18} />} note="Remote · Hybrid · On-site" color="blue" />
          </div>
          <div className="dataset-bar"><div className="dataset-label"><span className="section-eyebrow">{imported ? 'LOCAL FILE' : 'VIEWING RESULTS'}</span>{imported ? <strong>{imported.name}</strong> : <label><span className="sr-only">Choose a previous run</span><select value={dashboard.selection ? `${dashboard.selection.source}|${dashboard.selection.id}` : ''} disabled={!dashboard.overview?.runs.length} onChange={event => { const run = dashboard.overview?.runs.find(value => `${value.source}|${value.id}` === event.target.value); if (run) openRun(run); }}><option value="" disabled>No runs yet</option>{dashboard.overview?.runs.map(run => <option key={`${run.source}|${run.id}`} value={`${run.source}|${run.id}`}>{formatDate(run.started)} · {run.status} · {run.source}</option>)}</select></label>}</div><div className="dataset-meta">{imported ? <button className="text-button" onClick={() => setImported(null)}><X size={13} />Close file</button> : selectedRun && <><Badge value={selectedRun.status} /><span><Clock3 size={13} />{duration(selectedRun.started, selectedRun.finished)}</span></>}</div></div>
          {imported && <div className="alert alert-info"><FileSpreadsheet size={18} /><div><strong>CSV preview · stays in your browser</strong><p>These are imported values, not newly verified results. Your original file is unchanged.{imported.warnings.length > 0 && ` ${imported.warnings.join(' ')}`}</p></div></div>}
          {!imported && selectedRun?.personalized === false && <div className="alert alert-info"><History size={18} /><div><strong>Earlier collector output</strong><p>These results predate your personal verification rules. Review them as historical listings, not newly qualified matches.</p></div></div>}
          {incomplete && <div className="alert alert-warning"><AlertTriangle size={18} /><div><strong>This run has incomplete coverage</strong><p>Some searches or verification checks were limited, blocked or not completed. A low result count does not mean no other jobs exist.</p>{notes.length > 0 && <details className="coverage-details"><summary>View source notes ({notes.length})</summary><ul>{notes.map((note, index) => <li key={index}>{note}</li>)}</ul></details>}</div></div>}
          {!imported && <SourceCoverage summary={dashboard.detail?.summary} sources={dashboard.overview?.jobSources} />}
          <JobTable key={imported ? `file-${imported.name}-${imported.total}` : `${dashboard.selection?.source}-${dashboard.selection?.id}`} jobs={jobs} loading={!imported && (dashboard.loading || dashboard.detailLoading)} imported={Boolean(imported)} historical={historical} selection={dashboard.selection} onSelect={setSelectedJob} onImport={() => inputRef.current?.click()} />
          <div className="workspace-footer"><span><ShieldCheck size={13} />Evidence first. No applications or messages are sent.</span><span>All times shown in UK time</span></div>
        </>}
        {view === 'history' && <section className="history-panel panel"><div className="results-heading"><div><h2>Search history<span className="count-pill">{dashboard.overview?.runs.length || 0}</span></h2><p>Open any run to explore its jobs and source notes.</p></div><History size={21} className="muted" /></div>{dashboard.overview?.runs.length ? <div className="table-scroll"><table><thead><tr><th>SEARCH RUN</th><th>STATUS</th><th>QUALIFYING</th><th>STRONG</th><th>DURATION</th><th /></tr></thead><tbody>{dashboard.overview.runs.map(run => <tr className="job-row" key={`${run.source}-${run.id}`} onClick={() => openRun(run)}><td><button className="job-title" onClick={event => { event.stopPropagation(); openRun(run); }}>{formatDate(run.started)}</button><span className="company-name">{run.source} · {run.id.slice(-8)}</span></td><td><Badge value={run.status} /></td><td className="number-cell">{run.qualifying}</td><td className="number-cell">{run.strong}</td><td className="muted">{duration(run.started, run.finished)}</td><td><ChevronRight size={16} /></td></tr>)}</tbody></table></div> : <div className="empty-state"><span className="empty-icon"><History size={25} /></span><h3>Your search history starts here</h3><p>Completed and partial runs will appear here with their results.</p><button className="button button-primary" onClick={() => setView('results')}>Go to job search<ArrowUpRight size={15} /></button></div>}</section>}
        {view === 'criteria' && <ProfileSetup boards={dashboard.overview?.jobSources || []} onSaved={() => { void dashboard.refresh(); }} />}
      </div>
    </main>
    <input className="sr-only" ref={inputRef} type="file" accept=".csv,text/csv" aria-label="Open a local job-results CSV" onChange={event => { void importCsv(event.target.files?.[0]); }} />
    {selectedJob && <JobDrawer job={selectedJob} selection={imported ? null : dashboard.selection} trackerUrl={profile?.trackerUrl} onClose={() => setSelectedJob(null)} />}
  </div>;
}

function Metric({ label, value, icon, note, color = 'neutral' }: { label: string; value: ReactNode; icon: ReactNode; note: string; color?: string }) {
  return <section className={`metric-card metric-${color}`}><div className="metric-heading"><span>{label}</span>{icon}</div><strong className="metric-value">{value}</strong><span className="metric-note">{note}</span></section>;
}

export default App;
