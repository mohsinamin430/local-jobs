import { useState } from 'react';
import { ExternalLink } from 'lucide-react';
import type { Overview } from '../types';
import { safeUrl } from '../lib/data';

const names:Record<string,string>={totaljobs:'Totaljobs',google:'Google'};
const publicSector = [
  {name: 'Civil Service Jobs', url: 'https://www.civilservicejobs.service.gov.uk/csr/index.cgi'},
  {name: 'NHS Jobs', url: 'https://www.jobs.nhs.uk/candidate/search'},
  {name: 'GOV.UK Find a Job', url: 'https://www.gov.uk/find-a-job'},
];

export function PublicSearchLinks({ searches }: { searches?:Overview['publicSearches'] }) {
  const [source,setSource]=useState('google');
  const [selected,setSelected]=useState('0');
  const available=[...new Set(searches?.map(item=>item.source))];
  const currentSource = available.includes(source) ? source : available[0];
  const rows=searches?.filter(item=>item.source===currentSource) || [];
  const search=rows[Number(selected)] || rows[0];
  const url=safeUrl(search?.url || '');
  return <details className="google-search-links"><summary>Open a source in your browser</summary>
    <p>If automatic access is blocked, open the same search in your browser. A 403 challenge needs an interactive session; a 429 waits and retries within the current run.</p>
    {available.length > 0 && <>
    <label><span className="sr-only">Job search website</span><select aria-label="Job search website" value={currentSource} onChange={event=>{setSource(event.target.value);setSelected('0');}}>{available.map(item=><option value={item} key={item}>{names[item] || item}</option>)}</select></label>
    <label><span className="sr-only">Search criteria</span><select aria-label="Search criteria" value={selected} onChange={event=>setSelected(event.target.value)}>{rows.map((item,index)=><option value={index} key={`${item.role}-${item.bucket}`}>{item.role} · {item.bucket==='on-site'?'On-site':`UK ${item.bucket}`}</option>)}</select></label>
    {url && <a className="text-button inline-link" href={url} target="_blank" rel="noopener noreferrer">Search {names[currentSource] || currentSource}<ExternalLink size={13}/></a>}
    </>}
    <p>Public-sector sites for manual searches</p>
    {publicSector.map(site => <a key={site.name} className="text-button inline-link" href={site.url}
      target="_blank" rel="noopener noreferrer">{site.name}<ExternalLink size={13}/></a>)}
    <span className="subtle-note">Paste a vacancy URL into Evaluate a listing to assess it.</span>
  </details>;
}
