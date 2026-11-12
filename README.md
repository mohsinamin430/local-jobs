import assert from 'node:assert/strict';
import test from 'node:test';
import { filterJobs, hiddenAdvertTitle, normalizeJob, parseCsv, safeUrl, sourceLabel } from '../src/lib/data.js';

test('CSV parses quoted commas, multiline descriptions, escaped quotes and a BOM', () => {
  const result = parseCsv('\uFEFFTitle,Company,Description,Fit\r\n"DevOps Engineer","Example, Ltd","Build AWS\nUse ""Terraform""",Strong\r\n');
  assert.equal(result.jobs.length, 1);
  assert.equal(result.jobs[0].company, 'Example, Ltd');
  assert.equal(result.jobs[0].description, 'Build AWS\nUse "Terraform"');
  assert.equal(result.jobs[0].fit, 'Strong');
  assert.equal(result.jobs[0].contactStatus, 'imported');
});

test('legacy digest columns normalize without inventing verification', () => {
  const result = parseCsv('Title,Company,Work Type,Why,Hiring Contact,Apply URL\nPlatform Engineer,Example,Hybrid,Some gaps,Alex | Recruiter | https://linkedin.com/in/alex,https://example.com/job/1\n');
  assert.equal(result.jobs[0].workModel, 'hybrid');
  assert.equal(result.jobs[0].why, 'Some gaps');
  assert.equal(result.jobs[0].contactStatus, 'imported');
  assert.equal(result.jobs[0].applyUrl, 'https://example.com/job/1');
});

test('malformed quoted fields and unrelated files fail clearly', () => {
  assert.throws(() => parseCsv('Title,Company\n"unterminated,Example'), /quoted field/);
  assert.throws(() => parseCsv('a,b\n1,2'), /job-results CSV/);
  assert.throws(() => parseCsv('Title,Company\n'), /no job rows/);
});

test('unsafe and credential-bearing links are never exposed as navigation targets', () => {
  assert.equal(safeUrl('javascript:alert(1)'), '');
  assert.equal(safeUrl('data:text/html,test'), '');
  assert.equal(safeUrl('https://name:password@example.com/'), '');
  assert.equal(safeUrl('https://www.linkedin.com/jobs/view/123/'), 'https://www.linkedin.com/jobs/view/123/');
});

test('filtering combines search, work model and fit without mutating source order', () => {
  const jobs = [normalizeJob({ title: 'Senior DevOps', company: 'B', fit: 'Stretch', work_model: 'hybrid', description: 'Azure' }, 0), normalizeJob({ title: 'Cloud Engineer', company: 'A', fit: 'Strong', work_model: 'remote', description: 'Terraform' }, 1)];
  assert.equal(filterJobs(jobs, 'terraform', 'Strong', 'remote', 'fit', 'asc').length, 1);
  assert.equal(filterJobs(jobs, '', '', '', 'fit', 'asc')[0].company, 'A');
  assert.equal(jobs[0].company, 'B');
  assert.equal(filterJobs(jobs, '', 'Possible', '', 'title', 'asc').length, 0);
});

test('cross-board provenance survives normalization without creating extra rows', () => {
  const job=normalizeJob({title:'DevOps Engineer',source:'totaljobs',found_on:['totaljobs','glassdoor'],source_urls:['https://example.com/job/1']},0);
  assert.equal(job.source,'totaljobs');
  assert.deepEqual(job.foundOn,['totaljobs','glassdoor']);
});

test('CSV source columns remain visible and imports stay unverified', () => {
  const result=parseCsv('Title,Company,Source\nSRE,Example,indeed\n');
  assert.deepEqual(result.jobs[0].foundOn,['indeed']);
  assert.equal(result.jobs[0].contactStatus,'imported');
});


test('saved and imported trainee titles are hidden while genuine junior roles remain', () => {
  const csv = 'Title,Company\n"Trainee DevOps Engineer | No experience needed (Ref: 7501)",Example\nJunior DevOps Engineer,Example\nCloud Engineer - no-prior-experience-required,Example\n';
  const result = parseCsv(csv);
  assert.deepEqual(result.jobs.filter(job => !hiddenAdvertTitle(job.title)).map(job => job.title), ['Junior DevOps Engineer']);
  for (const title of ['Platform Engineer Traineeship', 'Cloud Engineer - NO PREVIOUS EXPERIENCE IS NECESSARY']) {
    assert.equal(hiddenAdvertTitle(title), true, title);
  }
  for (const title of ['Graduate Cloud Engineer', 'DevOps Engineer - no Kubernetes experience required']) {
    assert.equal(hiddenAdvertTitle(title), false, title);
  }
});


test('Jobs by Qais provenance is labelled consistently in job results', () => {
  const job = normalizeJob({ title: 'Platform Engineer', source: 'jobsbyqais', found_on: ['jobsbyqais', 'linkedin'] }, 0);
  assert.equal(sourceLabel(job.source), 'Jobs by Qais');
  assert.deepEqual(job.foundOn.map(sourceLabel), ['Jobs by Qais', 'LinkedIn']);
});


## Jobs by Qais

**Jobs by Qais** is available in the source picker and profile setup. Your search reads the public embedded jobs list at [jobsbyqais.coderco.io](https://jobsbyqais.coderco.io/#jobs), without a paid API or executing its marketing scripts.

The board is used for discovery. The original LinkedIn, Reed or employer vacancy is verified before a job qualifies. Board refresh dates, skill tags and entry-level labels are not proof of a newly posted vacancy or a hybrid arrangement. Date-only discovery values can prune clearly old candidates; exact-listing evidence still has to establish the configured posting window. Pay-to-train adverts and the existing trainee/no-experience title exclusions stay excluded. Links to the previously removed Indeed, Glassdoor, Welcome to the Jungle and Adzuna sources are skipped.

Verified hybrid jobs can be anywhere in the UK. The on-site city preference applies to on-site jobs, and remote roles still require UK eligibility. A city alone, a UK label or the word “hybrid” on a discovery page does not prove the arrangement. Jobs are merged with matching openings found through other sources and show **Jobs by Qais** as provenance. Missing dates, access failures and changed board data are recorded as incomplete or unverified rather than zero results.
