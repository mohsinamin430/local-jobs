import { useState } from 'react';
import { ExternalLink } from 'lucide-react';
import type { Overview } from '../types';
import { safeUrl } from '../lib/data';

const names:Record<string,string>={totaljobs:'Totaljobs',google:'Google'};

export function PublicSearchLinks({ searches }: { searches?:Overview['publicSearches'] }) {
  const [source,setSource]=useState('google');
  const [selected,setSelected]=useState('0');
  if (!searches?.length) return null;
  const available=[...new Set(searches.map(item=>item.source))];
  const rows=searches.filter(item=>item.source===source);
  const search=rows[Number(selected)] || rows[0];
  const url=safeUrl(search?.url || '');
  return <details className="google-search-links"><summary>Open a source in your browser</summary>
    <p>If automatic access is blocked, open the same search in your browser. A 403 challenge needs an interactive session; a 429 waits and retries within the current run.</p>
    <label><span className="sr-only">Job search website</span><select aria-label="Job search website" value={source} onChange={event=>{setSource(event.target.value);setSelected('0');}}>{available.map(item=><option value={item} key={item}>{names[item] || item}</option>)}</select></label>
    <label><span className="sr-only">Search criteria</span><select aria-label="Search criteria" value={selected} onChange={event=>setSelected(event.target.value)}>{rows.map((item,index)=><option value={index} key={`${item.role}-${item.bucket}`}>{item.role} · {item.bucket==='on-site'?'On-site':`UK ${item.bucket}`}</option>)}</select></label>
    {url && <a className="text-button inline-link" href={url} target="_blank" rel="noopener noreferrer">Search {names[source] || source}<ExternalLink size={13}/></a>}
    <span className="subtle-note">Opening a page does not import results. Posting dates and work arrangements still need verification.</span>
  </details>;
}
