"""Persistent LinkedIn pacing, cooldowns and successful-response checkpoints."""
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import json
import random
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


def linkedin(url):
    host = (urlsplit(url).hostname or '').lower()
    return host == 'linkedin.com' or host.endswith('.linkedin.com')


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(path)


class RateLimited(RuntimeError):
    def __init__(self, until, message='LinkedIn is cooling down', source='linkedin'):
        self.until = until
        self.source = source
        super().__init__(f'{message}; retry eligible at {datetime.fromtimestamp(until,timezone.utc).isoformat(timespec="seconds")}')


class LinkedInControl:
    name = 'linkedin'
    label = 'LinkedIn'
    accepts = staticmethod(linkedin)
    def __init__(self, data_dir, minimum=8, maximum=12, clock=time.time, sleep=time.sleep, uniform=random.uniform):
        if not 1 <= minimum <= maximum <= 120:
            raise ValueError('LinkedIn spacing must satisfy 1 <= min <= max <= 120 seconds')
        self.root = Path(data_dir)/'network'
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root/(self.name+'.json')
        self.minimum, self.maximum, self.clock, self.sleep, self.uniform = minimum, maximum, clock, sleep, uniform

    @contextmanager
    def locked(self):
        with (self.root/(self.name+'.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            yield state
            save(self.path, state)

    def status(self):
        with self.locked() as state:
            until = float(state.get('cooldown_until', 0))
            return {'coolingDown': until > self.clock(), 'retryAt': datetime.fromtimestamp(until,timezone.utc).isoformat(timespec='seconds') if until else None,
                    'retryAtEpoch': until, 'remainingSeconds': max(0,int(until-self.clock()+.999)), 'failures': state.get('failures',0)}

    def before(self, url):
        if not self.accepts(url):
            return
        while True:
            with self.locked() as state:
                now = self.clock()
                until = float(state.get('cooldown_until',0))
                if until > now:
                    raise RateLimited(until,self.label+' is cooling down',getattr(self,'retry_source',self.name))
                delay = max(0, float(state.get('next_request_at',0))-now)
                if delay <= 0:
                    state['next_request_at'] = now+self.uniform(self.minimum,self.maximum)
                    return
            self.sleep(min(delay,1))

    def limited(self, url, headers):
        if not self.accepts(url):
            raise ValueError('Only LinkedIn uses this shared cooldown')
        with self.locked() as state:
            failures = int(state.get('failures',0))+1
            now = self.clock()
            retry = None
            value = (headers.get('Retry-After') or headers.get('retry-after')) if headers else None
            if value is not None:
                try:
                    if str(value).strip().isdigit():
                        retry = now+int(str(value).strip())
                    else:
                        parsed = parsedate_to_datetime(str(value))
                        if parsed.tzinfo is not None:
                            retry = parsed.timestamp()
                except (ValueError, TypeError, OverflowError):
                    pass
            if retry is None:
                delay = [60,180,600][min(failures-1,2)] if failures <= 3 else 3600
                retry = now+delay+self.uniform(0,min(delay*.1,30))
            retry = max(now+1,retry,float(state.get('cooldown_until',0)))
            state.update(cooldown_until=retry,failures=failures,last_status=429)
        return RateLimited(retry,self.label+' returned HTTP 429',getattr(self,'retry_source',self.name))

    def success(self, url):
        if self.accepts(url):
            with self.locked() as state:
                if float(state.get('cooldown_until',0)) <= self.clock():
                    state.update(failures=0,cooldown_until=0,last_status=200)


class BoardControl(LinkedInControl):
    def __init__(self, data_dir, source, **kwargs):
        from job_boards import DOMAINS, SOURCES, LEGACY_SOURCE_NAMES, owns
        if source not in DOMAINS: raise ValueError('Unsupported board cooldown')
        self.name, self.label = source, {**LEGACY_SOURCE_NAMES,**SOURCES}[source]
        self.accepts = lambda url: owns(source,url)
        super().__init__(data_dir, **kwargs)


class OriginControl(LinkedInControl):
    """Independent cooldowns for employer/ATS hosts discovered through Google."""
    def __init__(self,data_dir,url,**kwargs):
        host=(urlsplit(url).hostname or '').lower()
        if not host: raise ValueError('A discovered page must have a hostname')
        self.name='origin-'+hashlib.sha256(host.encode()).hexdigest()[:24]
        self.label,self.retry_source=host,host
        self.accepts=lambda value:(urlsplit(value).hostname or '').lower()==host
        super().__init__(data_dir,**kwargs)


def source_cooldowns(data_dir, sources):
    return {source:(LinkedInControl(data_dir) if source=='linkedin' else BoardControl(data_dir,source)).status() for source in sources}


def combined_cooldown(data_dir, sources):
    rates = source_cooldowns(data_dir,sources)
    active = {key:value for key,value in rates.items() if value['coolingDown']}
    rate = max(active.values(),key=lambda value:value['retryAtEpoch']) if active else {'coolingDown':False,'remainingSeconds':0,'retryAtEpoch':0,'retryAt':None,'failures':0}
    return {**rate, 'sources':list(active)}


class ResponseCheckpoint:
    """Replay completed GETs to rebuild the work queue without requesting them again."""
    def __init__(self, data_dir, signature, clock=time.time):
        self.root = Path(data_dir)/'checkpoints'
        self.root.mkdir(parents=True,exist_ok=True)
        self.pointer = self.root/'current.json'
        self.clock = clock
        old = json.loads(self.pointer.read_text()) if self.pointer.exists() else {}
        self.resumed = bool(old.get('signature') == signature and old.get('resumable') and clock()-old.get('created_at',0)<86400)
        self.state = old if self.resumed else {'id':uuid4().hex,'signature':signature,'created_at':clock(),'resumable':True}
        self.directory = self.root/self.state['id']
        self.directory.mkdir(exist_ok=True)
        self.observed = {}
        self.lock = threading.RLock()
        save(self.pointer,self.state)

    def _path(self, url, company=False):
        root = self.root/'companies' if company else self.directory
        root.mkdir(exist_ok=True)
        return root/(hashlib.sha256(url.encode()).hexdigest()+'.json')

    @staticmethod
    def company(url):
        return linkedin(url) and urlsplit(url).path.startswith('/company/')

    def get(self, url):
        with self.lock:
            return self._get(url)

    def _get(self, url):
        path = self._path(url,self.company(url))
        if not path.exists():
            return None
        entry = json.loads(path.read_text())
        lifetime = 604800 if self.company(url) else 86400
        if self.clock()-entry['observed_at'] >= lifetime:
            return None
        self.observed[url] = entry['observed_at']
        return entry['body']

    def put(self, url, body):
        with self.lock:
            observed = self.clock()
            self.observed[url] = observed
            save(self._path(url,self.company(url)), {'observed_at':observed,'body':body})

    def observed_at(self, url):
        with self.lock:
            value = self.observed.get(url,self.clock())
        return datetime.fromtimestamp(value,timezone.utc).isoformat(timespec='seconds')

    def finish(self, limited, run_id):
        with self.lock:
            self.state.update(resumable=limited,run_id=run_id,updated_at=self.clock())
            save(self.pointer,self.state)
