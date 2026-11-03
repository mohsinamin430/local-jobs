import { useMemo, useState } from 'react';
import { ArrowDownToLine, ArrowLeft, ArrowRight, ArrowUpDown, ChevronRight, FileSpreadsheet, Search, SlidersHorizontal, X } from 'lucide-react';
import type { Job, Selection } from '../types';
import { filterJobs, formatDate } from '../lib/data';
import type { SortKey } from '../lib/data';
import { downloadUrl } from '../lib/api';
import { Badge } from './Badge';

interface Props { jobs: Job[]; loading: boolean; imported: boolean; historical?: boolean; selection: Selection | null; onSelect: (job: Job) => void; onImport: () => void }

export function JobTable({ jobs, loading, imported, historical, selection, onSelect, onImport }: Props) {
  const [query, setQuery] = useState('');
  const [fit, setFit] = useState('');
  const [model, setModel] = useState('');
  const [source,setSource] = useState('');
  const [sort, setSort] = useState<SortKey>('fit');
  const [direction, setDirection] = useState<'asc' | 'desc'>('asc');
  const [page, setPage] = useState(1);
  const filtered = useMemo(() => filterJobs(jobs.filter(job=>!source || job.foundOn.includes(source)), query, fit, model, sort, direction), [jobs, query, fit, model, sort, direction,source]);
  const sourceOptions = [...new Set(jobs.flatMap(job=>job.foundOn))].sort();
  const pageCount = Math.max(1, Math.ceil(filtered.length / 15));
  const current = Math.min(page, pageCount);
  const visible = filtered.slice((current - 1) * 15, current * 15);
  const sorting = (key: SortKey) => { if (key === sort) setDirection(direction === 'asc' ? 'desc' : 'asc'); else { setSort(key); setDirection('asc'); } };
  const reset = () => { setQuery(''); setFit(''); setModel(''); setSource(''); setPage(1); };
  const hasFilters = Boolean(query || fit || model || source);
  return <section className="results-panel panel" aria-labelledby="results-title">
    <div className="results-heading"><div><h2 id="results-title">{imported ? 'CSV explorer' : historical ? 'Saved listings' : 'Job results'}<span className="count-pill">{jobs.length}</span></h2><p>{imported ? 'Your file, organised into a clear view.' : historical ? 'Historical listings from an earlier collector run.' : 'Explore the roles that made it through your criteria.'}</p></div>
      <div className="results-heading-actions"><button className="button button-quiet" onClick={onImport}><FileSpreadsheet size={15} />Open CSV</button>{selection && !imported && <a className="button button-quiet" href={downloadUrl(selection, 'csv')} download><ArrowDownToLine size={15} />Export CSV</a>}</div>
    </div>
    <div className="filter-bar"><label className="search-input"><Search size={16} /><span className="sr-only">Search job results</span><input placeholder="Search roles, companies or keywords…" value={query} onChange={event => { setQuery(event.target.value); setPage(1); }} />{query && <button aria-label="Clear search" onClick={() => { setQuery(''); setPage(1); }}><X size={13} /></button>}</label>
      <div className="filter-selects"><label><span className="sr-only">Filter by job board</span><select value={source} onChange={event=>{setSource(event.target.value);setPage(1);}}><option value="">All job boards</option>{sourceOptions.map(value=><option key={value} value={value}>{value === 'welcome' ? 'Welcome to the Jungle' : value === 'linkedin_public_guest' ? 'LinkedIn' : value.charAt(0).toUpperCase()+value.slice(1)}</option>)}</select></label><SlidersHorizontal size={14} className="muted" /><label><span className="sr-only">Filter by fit rating</span><select value={fit} onChange={event => { setFit(event.target.value); setPage(1); }}><option value="">All fit ratings</option>{['Strong', 'Possible', 'Stretch', 'Unrated'].map(value => <option key={value}>{value}</option>)}</select></label><label><span className="sr-only">Filter by work arrangement</span><select value={model} onChange={event => { setModel(event.target.value); setPage(1); }}><option value="">All work types</option><option value="remote">Remote</option><option value="hybrid">Hybrid</option><option value="on-site">On-site</option><option value="unknown">Not stated</option></select></label></div>
    </div>
    <div className="table-scroll"><table><thead><tr>
      <th aria-sort={sort === 'title' ? direction === 'asc' ? 'ascending' : 'descending' : 'none'}><button onClick={() => sorting('title')}>ROLE & COMPANY<ArrowUpDown size={12} /></button></th>
      <th>LOCATION</th><th aria-sort={sort === 'workModel' ? direction === 'asc' ? 'ascending' : 'descending' : 'none'}><button onClick={() => sorting('workModel')}>WORK TYPE<ArrowUpDown size={12} /></button></th>
      <th aria-sort={sort === 'fit' ? direction === 'asc' ? 'ascending' : 'descending' : 'none'}><button onClick={() => sorting('fit')}>FIT<ArrowUpDown size={12} /></button></th><th>POSTED</th><th><span className="sr-only">Details</span></th>
    </tr></thead><tbody>
      {loading ? Array.from({ length: 4 }, (_, index) => <tr key={index} className="skeleton-row"><td><span className="skeleton sk-long" /><span className="skeleton sk-short" /></td><td><span className="skeleton sk-medium" /></td><td><span className="skeleton sk-short" /></td><td><span className="skeleton sk-short" /></td><td><span className="skeleton sk-short" /></td><td /></tr>)
        : visible.map(job => <tr key={job.key} onClick={() => onSelect(job)} className="job-row"><td><div className="role-cell"><span className={`company-avatar company-avatar-${job.company.length % 4}`}>{job.company.slice(0, 2).toUpperCase()}</span><div><button className="job-title" onClick={event => { event.stopPropagation(); onSelect(job); }}>{job.title}</button><span className="company-name">{job.company}</span><span className="job-source">{job.foundOn.map(value=>value==='welcome'?'Welcome to the Jungle':value==='linkedin_public_guest'||value==='linkedin'?'LinkedIn':value.charAt(0).toUpperCase()+value.slice(1)).join(' · ')}</span></div></div></td><td className="location-cell">{job.location}</td><td><Badge value={job.workModel} kind="work" /></td><td><Badge value={job.fit} kind="fit" /></td><td className="posted-cell">{job.posted ? (/^\d{4}-\d\d-\d\d/.test(job.posted) ? formatDate(job.posted, true) : job.posted) : 'Not stated'}</td><td><ChevronRight size={16} className="row-arrow" /></td></tr>)}
    </tbody></table></div>
    {!loading && !visible.length && <div className="empty-state"><span className="empty-icon">{hasFilters ? <Search size={24} /> : <FileSpreadsheet size={25} />}</span><h3>{hasFilters ? 'No roles match these filters' : 'A clear view of your next move'}</h3><p>{hasFilters ? 'Try a broader keyword or change your fit and work-type filters.' : 'Run a search above or open an existing job-results CSV to get started.'}</p><button className="button button-quiet" onClick={hasFilters ? reset : onImport}>{hasFilters ? 'Clear filters' : 'Open a CSV file'}</button></div>}
    <div className="table-footer"><span>{filtered.length ? `${(current - 1) * 15 + 1}–${Math.min(current * 15, filtered.length)} of ${filtered.length}` : '0'} {filtered.length === 1 ? 'role' : 'roles'}{hasFilters ? ` · ${jobs.length} total` : ''}</span><div className="pagination"><button aria-label="Previous page" disabled={current <= 1} onClick={() => setPage(current - 1)}><ArrowLeft size={14} /></button><span>Page {current} of {pageCount}</span><button aria-label="Next page" disabled={current >= pageCount} onClick={() => setPage(current + 1)}><ArrowRight size={14} /></button></div></div>
  </section>;
}
