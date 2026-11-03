import { Badge } from './Badge';
import type { Overview } from '../types';

export function SourceCoverage({ summary, sources }: { summary?: Record<string,unknown>; sources?: Overview['jobSources'] }) {
  const value = summary?.source_breakdown;
  if (!value || typeof value !== 'object' || Array.isArray(value) || !sources) return null;
  const available = new Set(sources.map(source=>source.id));
  const entries = Object.entries(value).filter(([id])=>available.has(id));
  if (!entries.length) return null;
  return <section className="panel source-coverage" aria-labelledby="coverage-title">
    <h2 id="coverage-title">Job-board coverage</h2>
    <p>Counts show where each opening was found. A job listed on several boards appears once in the results.</p>
    <div className="source-grid">{entries.map(([id,raw])=>{
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
      const record = raw as Record<string,unknown>;
      const errors = Array.isArray(record.errors) ? record.errors.filter((error):error is string=>typeof error==='string') : [];
      return <article key={id} className="source-card"><div><strong>{String(record.name || id)}</strong><Badge value={String(record.status || 'incomplete')} /></div>
        <p>{Number(record.examined) || 0} examined · {Number(record.qualifying) || 0} qualifying</p>
        {record.status==='cooling_down' && Number(record.retryAtEpoch)>0 && <p>Resumes automatically at {new Date(Number(record.retryAtEpoch)*1000).toLocaleTimeString('en-GB',{timeZone:'Europe/London'})}. Results stay in this run.</p>}
        {errors.length>0 && <details><summary>Source notes</summary><ul>{errors.map((error,index)=><li key={index}>{error}</li>)}</ul></details>}
        {record.status==='incomplete' && !errors.length && <span className="subtle-note">Search limits reached or searches remain unchecked.</span>}
      </article>;
    })}</div>
  </section>;
}
