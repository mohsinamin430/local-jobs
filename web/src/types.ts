export type Source = 'container' | 'native' | 'reports' | string;

export interface Run {
  id: string; started: string; finished: string | null; status: string;
  qualifying: number; exported: number; strong: number;
  workType: Record<string, number>; fit: Record<string, number>;
  contacts: number; emails: number; source: Source; files: string[]; personalized?: boolean;
}

export interface Task {
  mode?:'sample'|'full'; maxJobs?:number|null; jobSources?:string[];
  id: string; state: 'starting' | 'running' | 'cancelling' | 'cooling_down' | 'rate_limited' | 'completed' | 'partial' | 'failed' | 'cancelled';
  cooldown?: Cooldown;
  started: string; finished: string | null; exitCode: number | null;
  runId: string | null; source: Source; logs: string[]; message?: string;
  sourceProgress?: Record<string,BoardProgress>;
}

export interface BoardProgress { name:string; state:string; retryAtEpoch:number; retryCount:number; waitingOn:string; action?:string; error?:string }

export interface Overview {
  rateLimit?: Cooldown; resumeAvailable?: boolean;
  resumeOptions?: {id?: string;maxJobs:number|null;sources?:string[]};
  jobSources?: {id:string;name:string}[];
  googleSearches?: {role:string;bucket:string;url:string}[];
  publicSearches?: {source:string;role:string;bucket:string;url:string}[];
  sourceRates?: Record<string,Cooldown>;
  runs: Run[]; active: Task | null;
  profile: { name: string; roles: string[]; bucketTargets: Record<string, number>; sourceCvTitle?: string; jobSources?:string[]; location?:string; city?:string; ready?:boolean; trackerUrl?:string; maxAgeHours?:number; workModels?:string[] };
  sources: string[];
}

export interface Cooldown { coolingDown: boolean; retryAt: string | null; retryAtEpoch: number; remainingSeconds: number; sources?:string[] }

export interface RunDetail {
  run: Run;
  summary: Record<string, unknown>;
  jobs: Record<string, unknown>[];
  decisions: Record<string, unknown>[];
}

export interface Job {
  key: string; id: string; title: string; company: string; location: string;
  workModel: string; fit: string; why: string; salary: string;
  posted: string; description: string; contact: string; contactStatus: string;
  email: string; applyUrl: string; url: string; imported: boolean;
  evidence: Record<string, unknown>; source:string; foundOn:string[];
}

export interface Selection { id: string; source: Source }
export interface CsvImport { name: string; jobs: Job[]; warnings: string[]; total: number }
