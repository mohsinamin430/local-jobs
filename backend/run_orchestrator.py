"""A single persisted run with concurrent, independently retrying board queues."""
from collections import Counter, deque
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from uuid import uuid4

import collector as c
import job_boards as boards
from public_research import ResearchBlocked, ResearchLimit
from job_feeds import fresh_candidate, matches_role
from search_scheduler import Action, Candidate, RequestBudget, Worker

UTC = timezone.utc
LOG = logging.getLogger('local-jobs')


def board_steps(context, source_id, states, research, client):
    """Only yielded actions perform I/O. All queue state stays on the main thread."""
    queue = deque(states)
    plan, summary, checkpoint = context.plan, context.summary, context.checkpoint
    stopped = ''
    try:
        while queue and len(context.seen) < context.limit:
            state = queue.popleft()
            report, search = state['report'], state['search']
            bucket = report['bucket']
            target = summary['bucket_targets'].get(bucket,100)
            key = source_id+':'+bucket if len(context.sources)>1 else bucket
            bucket_ids = context.bucket_seen.setdefault(key,set())
            if len(bucket_ids)>=target and report['pages']:
                report.update(status='limited',end='bucket_target')
                continue
            if report['pages']>=plan['maxPagesPerSearch'] or report['examined']>=plan['maxJobsPerSearch']:
                report.update(status='limited',end='configured_limit')
                continue
            url = search['url']+'&start='+str(state['offset']) if source_id=='linkedin' else boards.search_url(source_id,search['params']['keywords'],bucket,report['pages'],context.config)
            fetch = client.get if source_id=='linkedin' else research.get
            LOG.info('Search: %s / %s / %s page=%d',boards.SOURCES[source_id],search['params']['keywords'],bucket,report['pages']+1)
            try:
                body = yield Action(lambda:fetch(url),url)
                try:
                    cards = c.parse_search(body) if source_id=='linkedin' else boards.parse_search(source_id,body,url)
                    if source_id=='google':
                        report['discovery_mode']='google_public_search'
                        summary['warnings'].append('Google is indexed-link discovery with incomplete coverage. Its search-date filter and snippets do not prove when a vacancy was posted.')
                except ValueError:
                    if source_id!='welcome' or report['pages']: raise
                    cards = yield Action(lambda:boards.public_discovery(source_id,search['params']['keywords'],bucket,research,search['params'].get('location', 'United Kingdom')),'Public listing discovery')
                    report['discovery_mode'] = 'public_search_fallback'
                    summary['warnings'].append('Welcome to the Jungle uses indexed-link discovery with incomplete coverage; exact listing publication time is still required.')
                report.update(pages=report['pages']+1,status='checked')
                if not cards:
                    stripped = re.sub(r'<!--.*?-->','',body,flags=re.S).strip()
                    if stripped and not re.search(r'no (?:matching )?(?:results|jobs)(?: found)?|no more jobs|0 jobs found',body,re.I):
                        raise ValueError('Search response has no recognizable public job cards')
                    report.update(status='confirmed_zero' if not report['examined'] else 'exhausted',end='no_more_results')
                    continue
            except (c.AccessError,ResearchBlocked) as error:
                report.update(status='blocked',end=str(error))
                summary['errors'].append(f'{boards.SOURCES[source_id]}: {error}')
                stopped = str(error)
                return 'blocked'
            except (boards.DiscoveryIncomplete,ResearchLimit) as error:
                report.update(status='incomplete',end=str(error))
                summary['warnings'].append(f'{boards.SOURCES[source_id]}: {error}')
                stopped = str(error)
                return 'incomplete'
            except (ValueError,HTTPError,URLError,TimeoutError) as error:
                report.update(status='failed',end=str(error))
                summary['errors'].append(f'{boards.SOURCES[source_id]} / {search["params"]["keywords"]}: {error}')
                if source_id!='linkedin':
                    stopped = str(error)
                    return 'failed'
                continue
            state['offset'] += len(cards)
            fresh = [card for card in cards if card['id'] not in state['ids']]
            state['ids'].update(card['id'] for card in cards)
            if not fresh:
                report.update(status='limited',end='repeated_page')
                continue
            if context.max_jobs is not None: fresh = fresh[:1]
            for job in fresh:
                job['search_observed_at'] = checkpoint.observed_at(url)
                if len(context.seen)>=context.limit or len(bucket_ids)>=target or report['examined']>=plan['maxJobsPerSearch']: break
                bucket_ids.add(job['id'])
                if job['id'] in context.seen: continue
                report['examined'] += 1
                context.seen.add(job['id'])
                summary['examined'] += 1
                if source_id=='linkedin' and context.config.get('titlePattern') and not re.search(context.config['titlePattern'],job['title'],re.I):
                    context.decision(job,{'decision':'reject','reasons':['out_of_scope_title']})
                    continue
                try:
                    detail_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job['id']}" if source_id=='linkedin' else job['url']
                    body = yield Action(lambda:fetch(detail_url),detail_url)
                    job['detail_observed_at'] = checkpoint.observed_at(detail_url)
                    if source_id=='linkedin':
                        job.update(c.parse_detail(body))
                        evidence = context.api.job_evidence(body,job['url'],target=job)
                        job.update({key:evidence[key] for key in ('structured_dates','listing_work_model','applicant_locations')})
                        job['listing_closed'] = bool(re.search(r'no longer accepting applications|no longer available|job is closed',c.Document(body).root.text(),re.I))
                    else:
                        job.update(boards.parse_detail(source_id,body,job))
                    for candidate in job.get('named_contact_candidates',[]): candidate['source_url'] = job['url']
                except (c.AccessError,ResearchBlocked) as error:
                    if source_id=='google':
                        summary['errors'].append(f"Google-discovered listing {job['url']}: {error}")
                        context.decision(job,{'decision':'unverified','reasons':[f'detail_blocked: {error}']})
                        continue
                    report.update(status='blocked',end=str(error))
                    summary['errors'].append(f"Job {job['id']}: {error}")
                    context.decision(job,{'decision':'unverified','reasons':[f'detail_blocked: {error}']})
                    stopped = str(error)
                    return 'blocked'
                except (ValueError,HTTPError,URLError,TimeoutError,ResearchLimit) as error:
                    context.decision(job,{'decision':'unverified','reasons':[f'detail_unavailable: {error}']})
                    continue
                job.update(observed_at=c.iso(datetime.now(UTC)),requested_work_models=[bucket],
                           source='linkedin_public_guest' if source_id=='linkedin' else source_id,
                           found_on=[source_id],source_urls=[job['url']])
                # Retry enrichment from an unchanged base job if any host cools
                # down partway through. Successful GETs are reused from cache.
                base = deepcopy(job)
                job = yield Action(lambda:context.api.enrich_job(deepcopy(base),research,context.policy),'Verify employer and listing')
                if source_id=='google':
                    from google_discovery import employer_association
                    if not (job.get('google_employer_association_verified') or employer_association(job)):
                        context.decision(job,{'decision':'unverified','reasons':['Google result has no verified employer-domain or company-linked ATS association']})
                        continue
                yield Candidate(job)
            if report['status']=='checked':
                if report.get('discovery_mode')=='public_search_fallback':
                    report.update(status='incomplete',end='public_search_fallback; native date filter and complete coverage unavailable')
                elif source_id=='google':
                    report.update(status='incomplete',end='indexed Google links; complete board coverage cannot be established')
                    queue.append(state)
                else:
                    report['end'] = 'next_page_pending'
                    queue.append(state)
    finally:
        for state in queue:
            report = state['report']
            if report['status'] in {'not_checked','checked'}:
                report.update(status='not_checked' if report['status']=='not_checked' else 'limited',
                              end='source_unavailable: '+stopped if stopped else 'run_limit')


def feed_steps(context, source, states, research):
    feed = boards.FEEDS[source]
    errors = (ValueError, KeyError, TypeError, AttributeError, HTTPError, URLError, TimeoutError, ResearchLimit)
    detail_errors = (ResearchBlocked, *errors)
    queue = deque(states)
    while queue and len(context.seen) < context.limit:
        state = queue.popleft()
        search, report = state["search"], state["report"]
        if report["pages"] >= context.plan["maxPagesPerSearch"] or report["examined"] >= context.plan["maxJobsPerSearch"]:
            report.update(status="limited", end="configured_limit")
            continue
        try:
            cards, consumed, has_more = yield Action(
                lambda: feed.page(search, state["offset"], research.get), f"Read {feed.label} listings")
            if has_more and not consumed:
                raise ValueError("Feed returned an empty page before the end of the result set")
        except ResearchBlocked as error:
            report.update(status="blocked", end=str(error))
            context.summary["errors"].append(f"{feed.label}: {error}")
            continue
        except errors as error:
            report.update(status="incomplete" if isinstance(error, ResearchLimit) else "failed", end=str(error))
            context.summary["errors"].append(f"{feed.label}: {error}")
            continue
        report["pages"] += 1
        state["offset"] += consumed
        for card in cards:
            if len(context.seen) >= context.limit or report["examined"] >= context.plan["maxJobsPerSearch"]:
                report.update(status="limited", end="candidate_limit")
                break
            if not card.get("url") or card["id"] in context.seen or not matches_role(context, card["title"]):
                continue
            now = datetime.now(UTC)
            if not fresh_candidate(card, now, context.policy):
                continue
            context.seen.add(card["id"])
            report["examined"] += 1
            context.summary["examined"] += 1
            def detail():
                job = deepcopy(card)
                feed.detail(job, search, research.get)
                job.update(source=source, found_on=[source], source_urls=[job["url"]], observed_at=c.iso(now))
                if source != "greenhouse":
                    job = context.api.enrich_job(job, research, context.policy)
                return job

            try:
                job = yield Action(detail, f"Verify {feed.label} job")
            except detail_errors as error:
                context.decision(card, {"decision": "unverified", "reasons": [f"detail_unavailable: {error}"]})
                continue
            yield Candidate(job)
        else:
            if has_more:
                report.update(status="checked", end="next_page_pending")
                queue.append(state)
            else:
                status = "exhausted" if consumed or report["pages"] > 1 else "confirmed_zero"
                report.update(status=status, end="no_more_results")
    for state in queue:
        report = state["report"]
        if report["status"] in {"not_checked", "checked"}:
            report.update(status="limited", end="run_limit")


def contact_steps(context,research):
    for job in context.retained:
        options = {**context.policy.get('contactResearch',{}),'allowed_agencies':context.policy.get('allowed_agencies',[])}
        result = yield Action(lambda:context.api.discover_and_verify_contact(deepcopy(job),research.get,research.search,options),'Verify hiring contact')
        job.update(result)
        for evidence in job.get('contact_evidence',[]):
            if evidence.get('url'): evidence['source_observed_at'] = context.checkpoint.observed_at(evidence['url'])
        c.record_job(context.db,context.run_id,job,datetime.now(UTC))
        context.dirty = True


class RunContext:
    def __init__(self,api,config,data_dir,max_jobs,db,checkpoint,run_id,started,execution_started,plan):
        self.api,self.config,self.data,self.max_jobs,self.db = api,config,data_dir,max_jobs,db
        self.checkpoint,self.run_id,self.plan = checkpoint,run_id,plan
        self.policy,self.sources = config['personalPolicy'],boards.enabled_sources(config)
        self.limit = min(max_jobs,plan['maxJobsTotal']) if max_jobs else plan['maxJobsTotal']
        self.policy_hash = hashlib.sha256(json.dumps(self.policy,sort_keys=True).encode()).hexdigest()
        self.retained,self.jobs,self.decisions,self.seen,self.keys,self.by_key = [],[],[],set(),set(),{}
        self.bucket_seen,self.researchers,self.clients,self.progress = {},[],[],{}
        self.dirty,self.last_save = True,0
        self.directory = data_dir/'runs'/run_id
        self.previous = {row[0]:json.loads(row[1]) for row in db.execute('SELECT job_id,payload FROM run_jobs WHERE run_id=?',(run_id,))}
        self.summary = {'run_id':run_id,'started':started,'execution_started':c.iso(execution_started),'finished':None,'status':'running',
            'warnings':list(plan['warnings']),'errors':[],'searches':[],'requests':0,'examined':0,'exported':0,'new':0,'rejected':{},
            'qualifying_after_dedup':0,'cross_digest_duplicates':0,'within_run_duplicates':0,'unverified':0,
            'reliable_hiring_contacts':0,'verified_email_count':0,'email_research_status':'not_researched',
            'breakdown':{'fit':{},'work_type':{}},'candidate_source':self.policy.get('candidate',{}).get('source_cv',{}),
            'policy_sha256':self.policy_hash,'bucket_targets':config.get('bucketTargets',{'remote':100,'hybrid':100,'on-site':100}),
            'bucket_candidates':{},'verification_sources':[],'job_sources':self.sources,'resumed':checkpoint.resumed,
            'checkpoint_id':checkpoint.state['id'],'phase':'discovery','source_progress':{},'source_breakdown':{}}
        rows,self.summary['cross_digest_sources'] = api.index_jobs(config,execution_started)
        self.external_keys = set().union(*(api.key_set(row) for row in rows)) if rows else set()
        if any(row['status']!='ok' for row in self.summary['cross_digest_sources']):
            self.summary['warnings'].append('Cross-digest deduplication is incomplete: a current index of the other digest sources is unavailable')
        if max_jobs is not None: self.summary['warnings'].append(f'Bounded live check examines at most {max_jobs} unique candidates; this is not the complete daily digest')

    def decision(self,job,result):
        self.decisions.append({'id':job['id'],'title':job['title'],'company':job['company'],'url':job['url'],
            'decision':result['decision'],'reasons':result['reasons'],'fit':result.get('fit',''),
            'verification_sources':job.get('verification_sources',[]),'freshness_evidence':result.get('freshness_evidence',[])})
        for reason in result['reasons']: self.summary['rejected'][reason] = self.summary['rejected'].get(reason,0)+1
        self.summary['unverified'] += int(result['decision']=='unverified')
        self.dirty = True

    def candidate(self,job):
        result = self.api.evaluate_job(job,self.policy,datetime.now(UTC))
        if result['decision']!='retain':
            self.decision(job,result)
            return
        job.update(result)
        bounds = [item for item in result.get('freshness_evidence',[]) if item.get('earliest') and item.get('latest')]
        job['posted_earliest'] = max((item['earliest'] for item in bounds),default='')
        job['posted_latest'] = min((item['latest'] for item in bounds),default='')
        job['posting_time_display'] = job.get('posted_at') or job.get('detail_posted_text','')
        job['preferred_apply_url'] = self.api.canonical_url((job.get('canonical_apply_url') or job.get('apply_url')) if job.get('apply_url_verified') else job['url'])
        keys = self.api.key_set(job if job.get('apply_url_verified') else {**job,'apply_url':'','canonical_apply_url':''})
        if keys & self.external_keys:
            self.summary['cross_digest_duplicates'] += 1
            self.decision(job,{'decision':'duplicate','reasons':['already_in_other_digest_sources']})
            return
        if keys & self.keys:
            self.summary['within_run_duplicates'] += 1
            existing = next(self.by_key[key] for key in keys if key in self.by_key)
            existing['found_on'] = sorted(set(existing.get('found_on',[])) | set(job.get('found_on',[])))
            existing['source_urls'] = list(dict.fromkeys(existing.get('source_urls',[])+job.get('source_urls',[])))
            if job.get('apply_url_verified') and not existing.get('apply_url_verified'):
                for field in ('preferred_apply_url','apply_url','canonical_apply_url','apply_url_verified'):
                    if field in job: existing[field] = job[field]
            self.keys.update(keys)
            self.by_key.update({key:existing for key in keys})
            c.record_job(self.db,self.run_id,existing,datetime.now(UTC))
            self.decision(job,{'decision':'duplicate','reasons':['same_opening_id_apply_url_or_company_title_location']})
            return
        self.keys.update(keys)
        self.by_key.update({key:job for key in keys})
        previous = self.db.execute('SELECT payload FROM jobs WHERE id=?',(job['id'],)).fetchone()
        old = json.loads(previous[0]) if previous else {}
        new = self.previous.get(job['id'],{}).get('new_qualifying_job',old.get('personal_policy_sha256')!=self.policy_hash)
        job.update(contact='Not found',contact_status='incomplete',verified_email='',email_status='not_researched',
                   personal_policy_sha256=self.policy_hash,new_qualifying_job=new)
        c.record_job(self.db,self.run_id,job,datetime.now(UTC))
        self.retained.append(job)
        if not self.config.get('saveOnlyUniqueItems') or new or self.checkpoint.resumed: self.jobs.append(job)
        self.dirty = True
        LOG.info('Qualified %s — %s [%s/%s]',job['title'],job['company'],job['work_model'],job['fit'])

    def update(self):
        summary = self.summary
        summary.update(qualifying_after_dedup=len(self.retained),exported=len(self.jobs),new=sum(bool(job.get('new_qualifying_job')) for job in self.retained),
            strong_fit_count=sum(job['fit']=='Strong' for job in self.retained),
            reliable_hiring_contacts=sum(job['contact_status']=='verified' for job in self.retained),
            verified_email_count=sum(bool(job.get('verified_email')) for job in self.retained),
            contact_statuses=dict(Counter(job['contact_status'] for job in self.retained)),
            breakdown={'fit':dict(Counter(job['fit'] for job in self.retained)),'work_type':dict(Counter(job['work_model'] for job in self.retained))},
            requests=sum(client.requests for client in self.clients)+sum(research.requests for research in self.researchers),
            research_requests=sum(research.requests for research in self.researchers),
            bucket_candidates={key:len(value) for key,value in self.bucket_seen.items()},
            source_progress=deepcopy(self.progress))
        summary['verification_sources'] = [dict(record) for research in self.researchers for record in list(research.records)]
        summary['source_breakdown'] = {}
        for source_id in self.sources:
            reports = [value for value in summary['searches'] if value['source']==source_id]
            statuses = Counter(value['status'] for value in reports)
            progress = self.progress.get(source_id,{})
            status = 'cooling_down' if progress.get('state')=='cooling_down' else 'running' if progress.get('state') in {'running','pending'} else 'blocked' if statuses['blocked'] else 'failed' if statuses['failed'] or progress.get('error') else 'incomplete' if any(value['status'] in {'not_checked','limited','incomplete','checked'} for value in reports) else 'confirmed_zero' if reports and all(value['status']=='confirmed_zero' for value in reports) else 'checked'
            summary['source_breakdown'][source_id] = {'name':boards.SOURCES[source_id],'status':status,'searches':dict(statuses),
                'examined':sum(value['examined'] for value in reports),'qualifying':sum(source_id in value.get('found_on',[]) for value in self.retained),
                'errors':list(dict.fromkeys(value['end'] for value in reports if value['status'] in {'blocked','failed','incomplete'})),
                'retryAtEpoch':progress.get('retryAtEpoch',0),'waitingOn':progress.get('waitingOn',''),'retryCount':progress.get('retryCount',0)}
        summary['coverage_limited'] = any(value['status'] in {'not_checked','limited','blocked','failed','incomplete','checked'} for value in summary['searches'])
        summary['warnings'] = list(dict.fromkeys(summary['warnings']))

    def persist(self,force=False):
        import time
        if not force and time.monotonic()-self.last_save<1: return
        self.update()
        self.summary['updated'] = c.iso(datetime.now(UTC))
        if self.dirty or force:
            self.api.write_report(self.directory,self.jobs,self.decisions,self.summary)
            self.dirty = False
        else:
            c.atomic_text(self.directory/'summary.json',json.dumps(self.summary,indent=2)+'\n')
        self.db.execute('UPDATE runs SET finished=?,status=?,summary=? WHERE id=?',(self.summary.get('finished'),self.summary['status'],json.dumps(self.summary),self.run_id))
        self.db.commit()
        c.atomic_text(self.data/'latest.json',json.dumps({'run_id':self.run_id,'directory':str(self.directory.resolve()),'summary':self.summary},indent=2)+'\n')
        self.last_save = time.monotonic()

    def tick(self,scheduler):
        self.progress.update(scheduler.progress())
        for value in self.progress.values():
            if value.get('error'):
                message = f"{value['name']}: {value['error']}"
                if message not in self.summary['errors']: self.summary['errors'].append(message)
        active = [value for value in self.progress.values() if value['state'] in {'pending','running','cooling_down'}]
        self.summary['status'] = 'cooling_down' if active and all(value['state']=='cooling_down' for value in active) else 'running'
        self.persist()

    def recheck_freshness(self):
        for job in self.retained[:]:
            result = self.api.evaluate_job(job,self.policy,datetime.now(UTC))
            if result['decision']!='retain':
                self.decision(job,{**result,'reasons':['Final execution-time recheck: '+value for value in result['reasons']]})
                self.retained.remove(job)
                if job in self.jobs: self.jobs.remove(job)
            else: job.update(result)


def run_personalized(api,config,data_dir,max_jobs=None):
    import fcntl
    plan = c.build_plan(config)
    if max_jobs is not None and max_jobs<1: raise ValueError('--max-jobs must be positive')
    data_dir.mkdir(parents=True,exist_ok=True)
    lock = (data_dir/'collector.lock').open('a')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ValueError('Another run is using this data directory')
    db = c.open_db(data_dir/'jobs.sqlite3')
    execution_started = datetime.now(UTC)
    signature = hashlib.sha256(json.dumps({'config':config,'max_jobs':max_jobs},sort_keys=True).encode()).hexdigest()
    checkpoint = api.ResponseCheckpoint(data_dir,signature)
    prior_id = str(checkpoint.state.get('run_id','')) if checkpoint.resumed else ''
    prior = db.execute('SELECT started FROM runs WHERE id=?',(prior_id,)).fetchone() if re.fullmatch(r'[A-Za-z0-9_-]{1,100}',prior_id) else None
    run_id = prior_id if prior else execution_started.strftime('%Y%m%dT%H%M%SZ-')+uuid4().hex[:8]
    started = prior[0] if prior else c.iso(execution_started)
    checkpoint.state.update(max_jobs=max_jobs,sources=boards.enabled_sources(config))
    checkpoint.finish(True,run_id)
    context = RunContext(api,config,data_dir,max_jobs,db,checkpoint,run_id,started,execution_started,plan)
    db.execute('INSERT INTO runs VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET finished=NULL,status=excluded.status,summary=excluded.summary',
               (run_id,started,None,'running',json.dumps(context.summary)))
    db.commit()
    pacing = config.get('rateLimit',{})
    limiter = api.LinkedInControl(data_dir,pacing.get('minIntervalSeconds',8),pacing.get('maxIntervalSeconds',12))
    controls = {source:api.BoardControl(data_dir,source,minimum=pacing.get('minIntervalSeconds',8),maximum=pacing.get('maxIntervalSeconds',12)) for source in boards.DOMAINS if source in boards.SOURCES}
    options = context.policy.get('verification',{})
    budget = RequestBudget(options.get('maxRequests',300))
    def research_client(source=None):
        research = api.PublicResearch(max_requests=options.get('maxRequests',300),delay=options.get('requestDelaySeconds',2))
        research.limiter,research.checkpoint,research.source_controls,research.budget = limiter,checkpoint,controls,budget
        if source=='google':
            from rate_control import OriginControl
            research.other_limiter=lambda url:OriginControl(data_dir,url,minimum=2,maximum=4)
        context.researchers.append(research)
        return research
    states = {source:[] for source in context.sources}
    for search in plan['searches']:
        source = search.get('source','linkedin')
        bucket = next((key for key,value in c.WORK_MODELS.items() if value==search['params'].get('f_WT')),'unknown')
        report = {'source':source,'url':search['url'],'params':search['params'],'bucket':bucket,'status':'not_checked','pages':0,'examined':0,'end':'pending'}
        context.summary['searches'].append(report)
        states[source].append({'search':search,'report':report,'offset':0,'ids':set()})
    scheduler = api.SearchScheduler()
    workers = {}
    for source in context.sources:
        client = c.Client(plan['requestDelaySeconds'],limiter=limiter,checkpoint=checkpoint)
        context.clients.append(client)
        research = research_client(source)
        steps = feed_steps(context, source, states[source], research) if source in boards.FEEDS else board_steps(
            context, source, states[source], research, client)
        workers[source] = Worker(boards.SOURCES[source], steps)
    context.persist(force=True)
    print(json.dumps({'run_id':run_id,'event':'started','resumed':checkpoint.resumed}),flush=True)
    try:
        scheduler.run(workers,context.candidate,lambda:context.tick(scheduler))
        context.progress.update(scheduler.progress())
        context.summary['phase'] = 'contacts'
        # Closed/aged-out vacancies must not trigger contact research after a wait.
        context.recheck_freshness()
        if context.retained:
            scheduler = api.SearchScheduler()
            contact_worker = Worker('Hiring contacts',contact_steps(context,research_client()))
            scheduler.run({'contacts':contact_worker},context.candidate,lambda:context.tick(scheduler))
            context.progress.update(scheduler.progress())
        context.recheck_freshness()
        context.update()
        incomplete = (context.summary['coverage_limited'] or context.summary['unverified'] or context.summary['errors']
            or any(row['status']!='ok' for row in context.summary['cross_digest_sources'])
            or any(job.get('contact_status') in {'blocked','incomplete'} for job in context.retained)
            or any(row['status'] not in {'ok','rate_limited'} for row in context.summary['verification_sources']))
        only_blocked = len(context.sources)==1 and any(row['status']=='blocked' for row in context.summary['searches']) and not context.retained
        context.summary['status'] = 'failed' if only_blocked else 'partial' if incomplete else 'completed'
    except BaseException as error:
        context.summary['status'] = 'interrupted' if isinstance(error,KeyboardInterrupt) else 'failed'
        context.summary['errors'].append(f'{type(error).__name__}: {error}')
        raise
    finally:
        scheduler.close()
        if context.summary['status'] in {'interrupted','failed'}:
            for value in context.progress.values():
                if value['state'] in {'pending','running','cooling_down'}: value['state'] = 'interrupted'
        context.summary['finished'] = c.iso(datetime.now(UTC))
        context.summary['config_sha256'] = hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest()
        checkpoint.finish(context.summary['status']=='interrupted',run_id)
        if any(job.get('contact_status') in {'blocked','incomplete'} for job in context.retained):
            context.summary['warnings'].append('Some hiring contacts remain Not found because current employment could not be verified')
        # Replace this run's membership only. The global jobs table and other
        # historical runs are preserved, including when a resume ages jobs out.
        db.execute('DELETE FROM run_jobs WHERE run_id=?',(run_id,))
        for job in context.retained: c.record_job(db,run_id,job,datetime.now(UTC))
        context.persist(force=True)
        db.close()
        lock.close()
    for message in context.summary['errors']:
        LOG.warning('Source error: %s', message)
    for message in context.summary['warnings']:
        LOG.warning('Coverage: %s', message)
    if os.environ.get('JOB_DESK_COMPACT_LOG') == '1':
        print(json.dumps({'event':'finished','run_id':run_id,'status':context.summary['status'],
                          'examined':context.summary['examined'],'qualifying':len(context.retained),
                          'unverified':context.summary['unverified'],'errors':len(context.summary['errors'])}),flush=True)
    else:
        print(json.dumps({'directory':str(context.directory.resolve()),**{key:value for key,value in context.summary.items() if key!='verification_sources'}},indent=2))
    return 0 if context.summary['status']=='completed' else 2
