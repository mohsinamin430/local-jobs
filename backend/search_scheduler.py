"""Concurrent board queues with in-process, cancellable cooldown retries."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
import logging
import threading
import time

from rate_control import RateLimited


@dataclass
class Action:
    call: object
    label: str


@dataclass
class Candidate:
    job: dict


@dataclass
class Worker:
    name: str
    steps: object
    state: str = 'pending'
    action: object = None
    retry_at: float = 0
    retries: int = 0
    waiting_on: str = ''
    future: object = None
    error: str = ''


class SearchScheduler:
    """One outstanding action per board, up to one worker per selected board.

    Generators and qualification callbacks run on the coordinator thread. Only
    network actions run in the pool, keeping SQLite/deduplication single-writer.
    A 429 leaves the exact action queued and never creates another run/process.
    """
    def __init__(self, clock=time.time, idle=time.sleep):
        self.clock, self.idle = clock, idle
        self.workers = {}
        self.pool = None
        self.stop = threading.Event()

    def progress(self):
        return {key:{'name':worker.name, 'state':worker.state,
                     'retryAtEpoch':worker.retry_at if worker.state=='cooling_down' else 0,
                     'retryCount':worker.retries, 'waitingOn':worker.waiting_on,
                     'action':worker.action.label if worker.action else '', 'error':worker.error}
                for key,worker in self.workers.items()}

    def run(self, workers, on_candidate, on_tick=lambda:None):
        self.workers = workers
        self.pool = ThreadPoolExecutor(max_workers=max(1,len(workers)),thread_name_prefix='job-board')

        def advance(worker, value=None, error=None, first=False):
            while True:
                try:
                    step = next(worker.steps) if first else worker.steps.throw(error) if error else worker.steps.send(value)
                    first, error, value = False, None, None
                except StopIteration as end:
                    worker.state = end.value or 'completed'
                    worker.action = None
                    return
                except Exception as error:
                    worker.state,worker.error,worker.action = 'failed',str(error),None
                    return
                if isinstance(step,Candidate):
                    on_candidate(step.job)
                    continue
                if not isinstance(step,Action):
                    raise TypeError('Board queues must yield Action or Candidate')
                worker.action, worker.state = step, 'running'
                worker.retry_at, worker.waiting_on = 0, ''
                return

        try:
            for worker in workers.values():
                advance(worker,first=True)
            while not self.stop.is_set():
                for worker in workers.values():
                    if worker.action and worker.future is None and worker.retry_at <= self.clock():
                        worker.state = 'running'
                        worker.future = self.pool.submit(worker.action.call)
                on_tick()
                futures = {worker.future:worker for worker in workers.values() if worker.future is not None}
                if not futures:
                    pending = [worker for worker in workers.values() if worker.action]
                    if not pending: break
                    self.idle(min(1,max(.01,min(worker.retry_at for worker in pending)-self.clock())))
                    continue
                done,_ = wait(futures,timeout=.2,return_when=FIRST_COMPLETED)
                for future in done:
                    worker = futures[future]
                    worker.future = None
                    try:
                        value = future.result()
                    except RateLimited as error:
                        worker.retry_at = max(self.clock()+.01,error.until)
                        worker.retries += 1
                        worker.waiting_on = getattr(error,'source','linkedin')
                        worker.state = 'cooling_down'
                        logging.getLogger('local-jobs').warning('%s cooling down for %.0fs; same request remains queued (retry %d)',
                                                              worker.name,max(0,error.until-self.clock()),worker.retries)
                    except Exception as error:
                        advance(worker,error=error)
                    else:
                        advance(worker,value=value)
            on_tick()
        finally:
            self.close()

    def close(self):
        self.stop.set()
        for worker in self.workers.values():
            if worker.future: worker.future.cancel()
        if self.pool:
            self.pool.shutdown(wait=False,cancel_futures=True)
            self.pool = None


class RequestBudget:
    def __init__(self, maximum):
        self.maximum, self.used = maximum, 0
        self.lock = threading.Lock()

    def claim(self):
        from public_research import ResearchLimit
        with self.lock:
            if self.used >= self.maximum:
                raise ResearchLimit('Public research request budget reached; remaining verification is incomplete')
            self.used += 1
