import { useEffect, useState } from 'react';
import { ArrowUpRight, Check, ChevronRight, Clock3, LoaderCircle, Play, ShieldCheck, Square, Terminal } from 'lucide-react';
import type { Overview, Task } from '../types';
import { Badge } from './Badge';
import { isActiveTask } from '../lib/api';
import { BoardProgress } from './BoardProgress';
import { PublicSearchLinks } from './PublicSearchLinks';

interface Props {
  cooldown?: Overview['rateLimit']; resumeAvailable?: boolean;
  resumeOptions?: Overview['resumeOptions'];
  sources?: Overview['jobSources']; sourceRates?: Overview['sourceRates'];
  googleSearches?:Overview['googleSearches'];
  publicSearches?:Overview['publicSearches'];
  profile?: Overview['profile']; task: Task | null; submitting: boolean; connected: boolean;
  onRun: (mode: 'sample' | 'full', count: number, sources:string[]) => Promise<void>; onCancel: () => Promise<void>;
}

export function RunControls({ profile, task, submitting, connected, onRun, onCancel, cooldown, resumeAvailable, resumeOptions, sources, publicSearches }: Props) {
  const [mode, setMode] = useState<'sample' | 'full'>('sample');
  const [count, setCount] = useState(6);
  const [showLogs, setShowLogs] = useState(false);
  const [selectedSources,setSelectedSources] = useState<string[]>([]);
  useEffect(()=>{ if (!task || !isActiveTask(task.state)) return; if (task.mode) setMode(task.mode); if (task.maxJobs!=null) setCount(task.maxJobs); if (task.jobSources) setSelectedSources(task.jobSources); },[task?.id]);
  const sourceOptions = sources || [{id:'linkedin',name:'LinkedIn'}];
  const sourceIds = sourceOptions.map(source=>source.id).join(',');
  const configuredIds = (profile?.jobSources || ['linkedin']).join(',');
  useEffect(()=>{
    if (!profile) return;
    const allowed = new Set(sourceIds.split(','));
    setSelectedSources(configuredIds.split(',').filter(source=>allowed.has(source)));
  },[sourceIds,configuredIds,Boolean(profile)]);
  useEffect(()=>{ if (resumeAvailable && resumeOptions?.sources) setSelectedSources(resumeOptions.sources); },[resumeOptions?.id,resumeAvailable]);
  useEffect(()=>{ if (!resumeAvailable || !resumeOptions?.id) return; setMode(resumeOptions.maxJobs==null?'full':'sample'); if (resumeOptions.maxJobs!=null) setCount(resumeOptions.maxJobs); },[resumeOptions?.id,resumeAvailable]);
  const active = isActiveTask(task?.state);
  const [now,setNow] = useState(Date.now());
  const rate = task?.cooldown?.coolingDown ? task.cooldown : cooldown;
  const remaining = Math.max(0,Math.ceil((rate?.retryAtEpoch || 0)-now/1000));
  const sameSources = [...selectedSources].sort().join(',') === [...(resumeOptions?.sources || ['linkedin'])].sort().join(',');
  useEffect(() => { if (!active && !rate?.retryAtEpoch) return; const timer = window.setInterval(()=>setNow(Date.now()),1000); return ()=>window.clearInterval(timer); },[rate?.retryAtEpoch,active]);
  const roles = profile?.roles.length ?? 10;
  return <section className="search-panel panel" aria-labelledby="search-panel-title">
    <div className="search-panel-main">
      <div className="section-eyebrow"><span className="tiny-line" />YOUR SEARCH, READY TO GO</div>
      <h2 id="search-panel-title">Find your next opportunity.</h2>
      <p>Boards search concurrently. Fresh UK roles are checked against your experience and preferences.</p>
      <div className="criteria-pills"><span><Clock3 size={13} />Past {profile?.maxAgeHours ?? 24} hours</span><span><Check size={13} />{roles} role variants</span><span><ShieldCheck size={13} />Personal criteria</span>{profile?.includeRecruiters && <span><Check size={13} />Recruiters included</span>}</div>
      <div className="search-scope">{(profile?.workModels || []).map((model, index) => <span key={model}>{index > 0 && <ChevronRight size={12} />}{model === 'on-site' ? profile?.city + ' on-site' : 'UK ' + model}</span>)}</div>
      <fieldset className="source-picker" disabled={active || submitting}><legend className="control-label">Job boards</legend><div>{sourceOptions.map(source=><label key={source.id}><input type="checkbox" checked={selectedSources.includes(source.id)} onChange={event=>setSelectedSources(previous=>event.target.checked ? [...previous,source.id] : previous.filter(value=>value!==source.id))}/><span>{source.name}</span></label>)}</div></fieldset>
      <PublicSearchLinks searches={publicSearches}/>
    </div>
    <div className="run-config">
      <label className="control-label" htmlFor="run-mode">Search size</label>
      <div className="run-mode-row">
        <select id="run-mode" value={mode} disabled={active || submitting} onChange={event => setMode(event.target.value as 'sample' | 'full')}>
          <option value="sample">Quick check</option><option value="full">Full search</option>
        </select>
        {mode === 'sample' && <label className="sample-input"><span className="sr-only">Maximum jobs to examine</span><input type="number" min="1" max="30" value={count} disabled={active || submitting} onChange={event => setCount(Math.min(30, Math.max(1, Number(event.target.value) || 1)))} /><span>jobs</span></label>}
      </div>
      <button className="button button-primary run-button" disabled={!connected || active || submitting || !selectedSources.length} onClick={() => { void onRun(mode, count, selectedSources); }}>
        {active || submitting ? <LoaderCircle size={17} className="spin" /> : <Play size={16} fill="currentColor" />}
        {task?.state==='cooling_down' ? 'Waiting for cooldowns' : active ? 'Boards searching…' : submitting ? 'Starting search…' : resumeAvailable && sameSources && (mode==='full' ? resumeOptions?.maxJobs==null : resumeOptions?.maxJobs===count) ? 'Resume search' : 'Run search'}
        {!active && !submitting && <ArrowUpRight size={17} className="button-trailing" />}
      </button>
      <span className="run-note">{mode === 'sample' ? `Examines up to ${count} unique candidates across selected boards.` : 'Targets up to 100 candidates per board and work bucket.'}</span>
      {remaining>0 && !task?.sourceProgress && <p className="run-note" role="status">{(rate?.sources || ['linkedin']).map(id=>sourceOptions.find(source=>source.id===id)?.name || id).join(', ')} cooling down · {Math.floor(remaining/60)}m {remaining%60}s remaining. A search can queue these boards and resume automatically.</p>}
    </div>
    {task && <div className={`task-strip ${active ? 'task-active' : ''}`} role="status" aria-live="polite">
      <div className="task-status"><span className={`task-indicator ${active ? 'pulse' : ''}`} /><span>{task.message || (active ? 'Searching boards concurrently…' : `Search ${task.state}`)}</span><Badge value={task.state} /></div>
      <div className="task-actions"><button className="text-button" onClick={() => setShowLogs(!showLogs)} aria-expanded={showLogs}><Terminal size={14} />{showLogs ? 'Hide log' : 'View log'}</button>{active && <button className="text-button text-danger" disabled={task.state === 'cancelling'} onClick={() => { void onCancel(); }}><Square size={11} fill="currentColor" />{task.state === 'cancelling' ? 'Stopping…' : 'Stop'}</button>}</div>
      {active && <BoardProgress progress={task.sourceProgress} now={now}/>}
      {showLogs && <pre className="run-log" aria-label="Search activity log">{task.logs?.length ? task.logs.join('\n') : 'Waiting for the collector to report progress…'}</pre>}
    </div>}
  </section>;
}
