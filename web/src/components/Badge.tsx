export function Badge({ value, kind = 'status' }: { value: string; kind?: 'status' | 'fit' | 'work' }) {
  const tone = value === 'Strong' || value === 'completed' || value === 'verified' ? 'green'
    : value === 'Possible' || value === 'running' || value === 'remote' ? 'blue'
    : value === 'Stretch' || value === 'partial' || value === 'hybrid' || value === 'rate_limited' || value === 'cooling_down' ? 'amber'
    : value === 'failed' || value === 'blocked' ? 'red' : 'neutral';
  const label = value === 'on-site' ? 'On-site' : value === 'unknown' ? 'Not stated' : value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, ' ');
  return <span className={`badge badge-${tone} badge-${kind}`}>{kind === 'status' && <span className="badge-dot" />}{label}</span>;
}
