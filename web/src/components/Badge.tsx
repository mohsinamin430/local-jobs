type Tone = 'green' | 'blue' | 'amber' | 'red' | 'neutral';

const tones: Record<string, Tone> = {
  Strong: 'green',
  'Strong match': 'green',
  completed: 'green',
  verified: 'green',
  Good: 'blue',
  'Good match': 'blue',
  Possible: 'blue',
  running: 'blue',
  remote: 'blue',
  Partial: 'amber',
  Stretch: 'amber',
  'Stretch opportunity': 'amber',
  partial: 'amber',
  hybrid: 'amber',
  rate_limited: 'amber',
  cooling_down: 'amber',
  Gap: 'red',
  'Poor match': 'red',
  failed: 'red',
  blocked: 'red',
};

export function badgeTone(value: string): Tone {
  return tones[value] || 'neutral';
}

export function Badge({ value, kind = 'status' }: { value: string; kind?: 'status' | 'fit' | 'work' }) {
  const tone = badgeTone(value);
  const label = value === 'on-site' ? 'On-site' : value === 'unknown' ? 'Not stated' : value.charAt(0).toUpperCase() + value.slice(1).replace(/_/g, ' ');
  return <span className={'badge badge-' + tone + ' badge-' + kind}>{kind === 'status' && <span className="badge-dot" />}{label}</span>;
}
