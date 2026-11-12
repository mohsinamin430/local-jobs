#!/usr/bin/env python3
"""Loopback dashboard API and static React application for the local collector."""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4
from rate_control import LinkedInControl, combined_cooldown, source_cooldowns
from job_boards import FEEDS, SOURCES, enabled_sources, search_url, without_retired_sources

from external_digest import ExternalDigest, DigestError

LOG = logging.getLogger("job-desk")
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,100}\Z")
ACTIVE_STATES = {"starting", "running", "cancelling", "cooling_down"}
MAX_FILE = 32 * 1024 * 1024


from local_profile import blank_profile, build_config, config_path, validate_profile


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class APIError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def read_json(path, default=None):
    if path.is_symlink():
        raise ValueError("Symbolic-link data files are not served")
    if not path.exists():
        return default
    if path.stat().st_size > MAX_FILE:
        raise ValueError("Data file is too large to display")
    return json.loads(path.read_text(encoding="utf-8-sig"))


def save_json(path, data):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temp.replace(path)


def activity_lines(text):
    """Render old multiline reports as one activity entry, preserving errors."""
    rows, position = [], 0
    decoder = json.JSONDecoder()
    while position < len(text):
        end = text.find('\n',position)
        end = len(text) if end < 0 else end
        line = text[position:end]
        stripped = line.lstrip()
        if stripped.startswith('{'):
            try:
                value, parsed_end = decoder.raw_decode(text,position+len(line)-len(stripped))
                if isinstance(value,dict) and value.get('run_id') and value.get('status'):
                    rows.append(f"Run {value['run_id']}: {value['status']}; examined {value.get('examined','?')}; qualifying {value.get('qualifying_after_dedup',value.get('qualifying','?'))}")
                elif isinstance(value,dict) and value.get('event'):
                    rows.append(json.dumps(value,separators=(',',':')))
                position = parsed_end
                continue
            except ValueError:
                pass
        if stripped:
            rows.append(line)
        position = end+1
    return rows[-160:]


class RunStore:
    def __init__(self, sources):
        self.sources = {name: Path(root).resolve() for name, root in sources.items() if root is not None}

    def directory(self, source, run_id):
        if source not in self.sources or not SAFE_ID.fullmatch(run_id):
            raise APIError(404, "Run not found")
        root = self.sources[source]
        path = root / run_id
        if path.is_symlink() or not path.is_dir() or path.resolve().parent != root:
            raise APIError(404, "Run not found")
        return path

    def metadata(self, source, directory):
        summary = read_json(directory / "summary.json", {})
        if not isinstance(summary, dict):
            raise ValueError("Invalid run summary")
        breakdown = summary.get("breakdown", {})
        if not isinstance(breakdown, dict):
            raise ValueError("Invalid run breakdown")
        fits = breakdown.get("fit", {})
        work = breakdown.get("work_type", {})
        if not isinstance(fits, dict) or not isinstance(work, dict):
            raise ValueError("Invalid run count breakdown")
        if not isinstance(summary.get("started", ""), str):
            raise ValueError("Invalid run start time")
        for key in ["exported", "qualifying_after_dedup", "strong_fit_count", "reliable_hiring_contacts", "verified_email_count"]:
            if key in summary and (isinstance(summary[key], bool) or not isinstance(summary[key], int) or summary[key] < 0):
                raise ValueError("Invalid run counts")
        exported = summary.get("exported", 0)
        return {"id": directory.name, "started": summary.get("started", ""), "finished": summary.get("finished"),
                "status": summary.get("status", "unknown"), "qualifying": summary.get("qualifying_after_dedup", exported),
                "exported": exported, "strong": summary.get("strong_fit_count", fits.get("Strong", 0)),
                "workType": work, "fit": fits, "contacts": summary.get("reliable_hiring_contacts", 0),
                "emails": summary.get("verified_email_count", 0), "source": source,
                "personalized": bool(summary.get("policy_sha256")), "coverageLimited": bool(summary.get("coverage_limited")),
                "files": [name for name in ["jobs.csv", "jobs.json", "digest.md", "summary.json", "decisions.json"] if (directory/name).is_file() and not (directory/name).is_symlink()]}

    def list(self):
        rows, issues = [], []
        for source, root in self.sources.items():
            if not root.exists():
                continue
            directories = sorted(root.iterdir(), key=lambda value: value.name, reverse=True)[:300]
            for path in directories:
                if not path.is_dir() or path.is_symlink() or not SAFE_ID.fullmatch(path.name) or not (path/"summary.json").exists():
                    continue
                try:
                    rows.append(self.metadata(source, path))
                except (OSError, ValueError, TypeError) as error:
                    issues.append({"source": source, "id": path.name, "error": str(error)})
        rows.sort(key=lambda row: row["started"] or row["id"], reverse=True)
        return rows[:300], issues

    def detail(self, source, run_id):
        path = self.directory(source, run_id)
        try:
            jobs = read_json(path/"jobs.json")
            if jobs is None and (path/"jobs.csv").is_file() and not (path/"jobs.csv").is_symlink():
                if (path/"jobs.csv").stat().st_size > MAX_FILE:
                    raise ValueError("CSV is too large to display")
                with (path/"jobs.csv").open(encoding="utf-8-sig", newline="") as file:
                    jobs = list(csv.DictReader(file))
            if jobs is None:
                jobs = []
            if not isinstance(jobs, list) or not all(isinstance(row, dict) for row in jobs):
                raise ValueError("Invalid jobs data")
            return {"run": self.metadata(source, path), "summary": read_json(path/"summary.json", {}),
                    "jobs": jobs, "decisions": read_json(path/"decisions.json", [])}
        except (OSError, ValueError, TypeError) as error:
            raise APIError(422, f"Could not read this run: {error}") from error

    def download(self, source, run_id, format):
        name = {"csv": "jobs.csv", "json": "jobs.json", "digest": "digest.md"}.get(format)
        if not name:
            raise APIError(400, "Unknown download format")
        path = self.directory(source, run_id) / name
        if path.is_symlink() or not path.is_file():
            raise APIError(404, "Export not found")
        if path.stat().st_size > MAX_FILE:
            raise APIError(413, "Export is too large")
        return path


class Runner:
    def __init__(self, project_dir, data_dir, command_builder=None):
        self.project = Path(project_dir).resolve()
        self.data = Path(data_dir).resolve()
        self.tasks_dir = self.data / "ui/tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        self.command_builder = command_builder or self._command
        self.require_profile = False
        self.lock = threading.RLock()
        self.tasks, self.processes, self.workers, self.prior_runs = {}, {}, {}, {}
        self.log_cache = {}
        for path in self.tasks_dir.glob("*.json"):
            if path.is_symlink() or not SAFE_ID.fullmatch(path.stem):
                continue
            try:
                task = read_json(path)
                if not isinstance(task, dict) or task.get("id") != path.stem:
                    continue
                if task.get("state") in ACTIVE_STATES:
                    task.update(state="failed", finished=stamp(), message="Dashboard restarted; previous task state is unavailable. Review its run history before restarting.")
                    save_json(path, task)
                self.tasks[task["id"]] = task
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def _command(self, mode, max_jobs):
        command = [sys.executable, "-u", str(self.project/"collector.py"), "run", "--config", str(config_path(self.data)), "--data-dir", str(self.data)]
        if mode == "sample":
            command += ["--max-jobs", str(max_jobs)]
        return command

    def _save(self, task):
        self.tasks[task["id"]] = task
        save_json(self.tasks_dir/(task["id"]+".json"), task)

    def _snapshot(self, task):
        result = dict(task)
        result['cooldown'] = combined_cooldown(self.data,list(dict.fromkeys(task.get('jobSources', ['linkedin']) + ['linkedin'])))
        summary = self._checked_summary(task.get('runId'),task['id'])
        result['sourceProgress'] = summary.get('source_progress',{})
        if task['state'] in {'running','cooling_down'} and summary.get('status')=='cooling_down':
            result.update(state='cooling_down',message='Waiting for source cooldowns; this search will resume automatically in the same run.')
        log = self.tasks_dir/(task["id"]+".log")
        result["logs"] = []
        if log.is_file() and not log.is_symlink():
            stat = log.stat()
            signature = (stat.st_mtime_ns,stat.st_size)
            cached = self.log_cache.get(task['id'])
            if not cached or cached[0] != signature:
                with log.open('rb') as file:
                    rows = activity_lines(file.read(2_000_000).decode('utf-8',errors='replace'))
                cached = (signature,rows)
                self.log_cache[task['id']] = cached
            result['logs'] = list(cached[1])
        for level,key in [('ERROR','errors'),('WARNING','warnings')]:
            for message in summary.get(key,[])[:50]:
                if isinstance(message,str) and not any(message in row for row in result['logs']):
                    result['logs'].append(f'{level}: {message}')
        return result

    def _checked_summary(self, run_id, task_id):
        if not isinstance(run_id,str) or not SAFE_ID.fullmatch(run_id): return {}
        try:
            summary = read_json(self.data/'runs'/run_id/'summary.json',{})
            if not isinstance(summary,dict) or summary.get('run_id')!=run_id: return {}
            task = self.tasks[task_id]
            start = summary.get('execution_started') or summary.get('started','')
            actual_start = datetime.fromisoformat(start.replace('Z','+00:00'))
            task_start = datetime.fromisoformat(task['started'])
            if actual_start.tzinfo is None or actual_start<task_start-timedelta(seconds=5): return {}
            if run_id in self.prior_runs.get(task_id,set()) and not summary.get('execution_started'): return {}
            return summary
        except (OSError,ValueError,TypeError,KeyError,AttributeError):
            return {}

    @property
    def active(self):
        with self.lock:
            task = next((task for task in self.tasks.values() if task["state"] in ACTIVE_STATES), None)
            return self._snapshot(task) if task else None

    def get(self, task_id):
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                raise APIError(404, "Task not found")
            return self._snapshot(task)

    def start(self, mode, max_jobs=6, sources=None):
        if not isinstance(mode, str) or mode not in {"sample", "full"}:
            raise APIError(400, "Choose sample or full collection")
        if isinstance(max_jobs, bool) or not isinstance(max_jobs, int) or not 1 <= max_jobs <= 30:
            raise APIError(400, "Sample size must be an integer between 1 and 30")
        if sources is not None:
            try: sources = enabled_sources({'jobSources':sources})
            except ValueError as error: raise APIError(400,str(error)) from error
        with self.lock:
            path = config_path(self.data)
            if self.require_profile and not path.is_file():
                raise APIError(409, "Save your profile before running a search.")
            configured = without_retired_sources(read_json(path, {}))
            selected = sources or enabled_sources(configured)
            if self.active:
                raise APIError(409, "A collection is already running")
            # Respect the same file lock as CLI / scheduled runs.
            with (self.data/"collector.lock").open("a") as file:
                try:
                    fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(file, fcntl.LOCK_UN)
                except BlockingIOError as error:
                    raise APIError(409, "Another collector is using the data volume") from error
            task = {"id": uuid4().hex, "state": "starting", "started": stamp(), "finished": None,
                    "mode": mode, "maxJobs": max_jobs if mode == "sample" else None,
                    "exitCode": None, "runId": None, "source": "container", "message": "Starting collection", 'jobSources': selected, 'sourceOverride': sources is not None}
            self.prior_runs[task["id"]] = {path.name for path in (self.data/"runs").iterdir()} if (self.data/"runs").is_dir() else set()
            self._save(task)
            worker = threading.Thread(target=self._work, args=(task["id"], mode, max_jobs), daemon=True)
            self.workers[task["id"]] = worker
            worker.start()
            return self._snapshot(task)

    def _work(self, task_id, mode, max_jobs):
        try:
            self._work_once(task_id,mode,max_jobs)
        finally:
            with self.lock:
                self.workers.pop(task_id,None)
                self.prior_runs.pop(task_id,None)

    def _work_once(self, task_id, mode, max_jobs):
        output, log_size = [], 0
        reported_run_ids = []
        process = None
        try:
            with self.lock:
                task = dict(self.tasks[task_id])
                if task["state"] == "cancelled":
                    return
                command = self.command_builder(mode, max_jobs)
                if task.get('sourceOverride'): command += ['--sources', ','.join(task['jobSources'])]
                process = subprocess.Popen(command, cwd=self.project, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, bufsize=1, start_new_session=True, env={**os.environ, "PYTHONUNBUFFERED": "1", "JOB_DESK_COMPACT_LOG": "1"})
                self.processes[task_id] = process
                task.update(state="running", message="Collecting and verifying public job listings")
                self._save(task)
            with (self.tasks_dir/(task_id+".log")).open("a", encoding="utf-8") as log:
                for line in process.stdout:
                    reported_run_ids.extend(re.findall(r'"run_id"\s*:\s*"([A-Za-z0-9_-]+)"',line))
                    reported_run_ids = reported_run_ids[-20:]
                    if reported_run_ids:
                        candidate = reported_run_ids[-1]
                        with self.lock:
                            if candidate != self.tasks[task_id].get('runId') and self._checked_summary(candidate,task_id):
                                current = dict(self.tasks[task_id])
                                current['runId'] = candidate
                                self._save(current)
                    output.append(line)
                    output = output[-2000:]
                    if log_size < 2_000_000:
                        log.write(line)
                        log.flush()
                        log_size += len(line.encode("utf-8"))
            code = process.wait()
            matches = reported_run_ids or re.findall(r'"run_id"\s*:\s*"([A-Za-z0-9_-]+)"', "".join(output))
            run_id = matches[-1] if matches else None
            summary = self._checked_summary(run_id,task_id)
            if not summary: run_id = None
            with self.lock:
                task = dict(self.tasks[task_id])
                cancelled = task["state"] in {"cancelling", "cancelled"}
                state = "cancelled" if cancelled else 'rate_limited' if code==75 else "completed" if code == 0 else "partial" if summary.get("status") == "partial" or code == 2 else "failed"
                task.update(state=state, exitCode=code, runId=run_id, finished=stamp(), message={"rate_limited":"Collection stopped before its queued retries completed. Resume to continue the same run.","cancelled":"Collection cancelled; saved results are retained", "completed":"Collection finished", "partial":"Partial results saved. Review coverage and source restrictions.", "failed":"Collection failed. Review logs and any saved run."}[state])
                self.processes.pop(task_id, None)
                self._save(task)
        except Exception as error:
            with self.lock:
                task = dict(self.tasks[task_id])
                task.update(state="failed", finished=stamp(), message=str(error))
                self.processes.pop(task_id, None)
                self._save(task)
            LOG.exception("Dashboard collection failed")
        finally:
            if process is not None:
                if process.stdout:
                    process.stdout.close()
                if process.poll() is None:
                    try:
                        os.killpg(process.pid, signal.SIGINT)
                        self._ensure_stopped(process)
                    except ProcessLookupError:
                        pass

    def cancel(self, task_id):
        with self.lock:
            if task_id not in self.tasks:
                raise APIError(404, "Task not found")
            task = dict(self.tasks[task_id])
            if task["state"] not in ACTIVE_STATES:
                raise APIError(409, "This task is no longer running")
            process = self.processes.get(task_id)
            task.update(state="cancelling" if process else "cancelled", message="Stopping collection and preserving partial data")
            if not process:
                task["finished"] = stamp()
            self._save(task)
            if process and process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                threading.Thread(target=self._ensure_stopped, args=(process,), daemon=True).start()
            return self._snapshot(task)

    @staticmethod
    def _ensure_stopped(process):
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)

    def close(self):
        with self.lock:
            ids = [task["id"] for task in self.tasks.values() if task["state"] in ACTIVE_STATES]
        for task_id in ids:
            try:
                self.cancel(task_id)
            except (APIError, ProcessLookupError):
                pass
        with self.lock:
            workers = list(self.workers.values())
        for worker in workers:
            worker.join(timeout=14)


class DashboardApp:
    def __init__(self, project_dir, data_dir, native_dir=None, reports_dir=None, dist_dir=None, runner=None):
        self.project = Path(project_dir).resolve()
        self.data = Path(data_dir).resolve()
        self.dist = Path(dist_dir or self.project/"web/dist").resolve()
        sources = {"container": self.data/"runs"}
        if native_dir:
            sources["native"] = Path(native_dir)/"runs"
        if reports_dir:
            sources["reports"] = Path(reports_dir)
        self.store = RunStore(sources)
        self.runner = runner or Runner(self.project, self.data)
        self.runner.require_profile = True
        self.external_digest = ExternalDigest(self.data)
        self.assessments = {}
        self.assessment_lock = threading.Lock()

    def external_digest_status(self):
        status = self.external_digest.status()
        if not status["configured"]:
            defaults = read_json(self.project / "config/external-digest-defaults.json", {})
            status["folder"] = defaults.get("folder", "")
        return status

    def external_digest_action(self, action, payload, port):
        try:
            if action == "settings":
                with self.runner.lock:
                    if self.runner.active:
                        raise APIError(409, "Wait for the current search to finish before changing the external digest connection.")
                    return self.external_digest.configure(payload)
            if action == "connect":
                return self.external_digest.connect(port)
            if action == "refresh":
                self.external_digest.sync()
                return self.external_digest.status()
        except DigestError as error:
            raise APIError(422, str(error)) from error
        raise APIError(404, "Unknown external digest action")

    def profile_settings(self):
        config = without_retired_sources(read_json(self.data / "profile.json", {}))
        saved = config.get("localProfile")
        defaults = blank_profile()
        defaults["includeRecruiters"] = bool(config.get("personalPolicy", {}).get("include_agencies", False))
        if saved:
            return {"values": {**defaults, **saved}, "configured": True}
        return {"values": defaults, "configured": False}

    def update_profile(self, payload):
        try:
            profile = validate_profile(payload)
        except ValueError as error:
            raise APIError(400, str(error)) from error
        with self.runner.lock, self.assessment_lock:
            if self.runner.active:
                raise APIError(409, "Wait for the current search to finish before changing your profile.")
            save_json(self.data / "profile.json", {**build_config(profile), "localProfile": profile})
            self.assessments.clear()
        return self.profile_settings()

    def assess_application(self, payload):
        settings = self.profile_settings()
        if not settings["configured"]:
            raise APIError(409, "Save your candidate profile before evaluating jobs.")
        candidate = settings["values"]
        from application_assistant import evaluate
        fields = {"title", "company", "location", "workModel", "salary", "description",
                  "pasted", "url", "applyUrl", "contact", "contactStatus", "email"}
        if any(not isinstance(payload.get(key, ""), str) for key in fields):
            raise APIError(400, "Invalid vacancy fields")
        job = {key: payload.get(key, "").strip() for key in fields}
        if max(len(job["title"]), len(job["company"])) > 180:
            raise APIError(400, "Vacancy title and employer must be at most 180 characters.")
        if not (job["url"] or job["applyUrl"]) and not (job["title"] and job["company"]):
            raise APIError(400, "Enter a vacancy URL, or provide the advert text, job title and employer.")
        job["imported"] = True  # Client-supplied contact fields must be independently verified.
        job["apply_url"] = job["applyUrl"]
        run_id, run_source, job_id = (payload.get(key) for key in ("runId", "runSource", "jobId"))
        if all(isinstance(value, str) and value for value in (run_id, run_source, job_id)):
            saved = self.store.detail(run_source, run_id)["jobs"]
            row = next((entry for entry in saved if str(entry.get("id")) == job_id
                        and entry.get("title") == job["title"] and entry.get("company") == job["company"]), None)
            if row:
                job.update(imported=False, contact=row.get("contact", "Not found"),
                           contactStatus=row.get("contact_status"), email=row.get("verified_email", ""),
                           named_contact_candidates=row.get("named_contact_candidates", []),
                           employer_quality=row.get("employer_quality"))
        try:
            assessment = evaluate(job, self.data, candidate)
        except ValueError as error:
            raise APIError(422, str(error)) from error
        token = uuid4().hex
        with self.assessment_lock:
            if len(self.assessments) >= 24:
                self.assessments.pop(next(iter(self.assessments)))
            if self.profile_settings() != settings:
                raise APIError(409, "Your profile changed during evaluation. Run it again.")
            self.assessments[token] = (job, assessment, candidate)
        return {**assessment, "id": token}

    def application_documents(self, payload):
        from profile_documents import create_profile_bundle
        token = payload.get("assessmentId")
        if not isinstance(token, str):
            raise APIError(400, "An assessment is required")
        with self.assessment_lock:
            record = self.assessments.get(token)
        if not record:
            raise APIError(409, "Run the enhanced evaluation again before creating documents")
        try:
            job, assessment, candidate = record
            files = create_profile_bundle(candidate, job, assessment)
        except ValueError as error:
            raise APIError(422, str(error)) from error
        return {"files": [{"name": name, "content": base64.b64encode(content).decode("ascii")}
                          for name, content in files.items()]}

    def overview(self):
        rows, issues = self.store.list()
        config = {}
        try:
            settings = self.profile_settings()
            if settings["configured"]:
                config = without_retired_sources(read_json(config_path(self.data), {}))
            candidate = config.get("personalPolicy", {}).get("candidate", {})
            profile = {"name": candidate.get("name", ""), "location": candidate.get("location", ""),
                       "roles": config.get("roleVariants", []), "bucketTargets": config.get("bucketTargets", {}),
                       "jobSources": enabled_sources(config), "ready": settings["configured"],
                       "trackerUrl": settings["values"]["trackerUrl"],
                       "city": settings["values"]["city"],
                       "maxAgeHours": settings["values"]["maxAgeHours"],
                       "includeRecruiters": bool(config.get("personalPolicy", {}).get("include_agencies", False)),
                       "workModels": list(config.get("searchLocations", {"remote": "", "hybrid": "", "on-site": ""})),
                       "sourceCvTitle": candidate.get("source_cv", {}).get("title", "Saved profile")}
        except (ValueError, OSError, TypeError):
            profile = {"name": "", "roles": [], "bucketTargets": {}, "ready": False, "sourceCvTitle": "Profile unavailable"}
        checkpoint = read_json(self.data/'checkpoints/current.json',{})
        resume_config = dict(config)
        if checkpoint.get('sources'):
            resume_config['jobSources'] = checkpoint['sources']
        signature = hashlib.sha256(json.dumps({'config':resume_config,'max_jobs':checkpoint.get('max_jobs')},sort_keys=True).encode()).hexdigest()
        active = self.runner.active
        valid_checkpoint_sources = all(source in SOURCES for source in checkpoint.get('sources', ['linkedin']))
        resume_available = bool(valid_checkpoint_sources and not active and checkpoint.get('resumable') and checkpoint.get('signature')==signature and time.time()-checkpoint.get('created_at',0)<86400)
        return {"externalDigest": self.external_digest_status(), "runs": rows, "active": active, "profile": profile, "sources": list(self.store.sources), "issues": issues,
                'googleSearches':[{'role':role,'bucket':bucket,'url':search_url('google',role,bucket,config=config)} for role in config.get('roleVariants',[]) for bucket in profile.get("workModels", []) if "google" in enabled_sources(config)],
                'publicSearches':[{'source':source,'role':role,'bucket':bucket,'url':search_url(source,role,bucket,config=config)} for source in enabled_sources(config) if source not in FEEDS and source!='linkedin' for role in config.get('roleVariants',[]) for bucket in profile.get("workModels", [])],
                'rateLimit':combined_cooldown(self.data,list(SOURCES)), 'sourceRates':source_cooldowns(self.data,list(SOURCES)),
                'jobSources':[{'id':key,'name':value} for key,value in SOURCES.items()], 'resumeAvailable':resume_available,
                'resumeOptions':{'id':checkpoint.get('id'),'maxJobs':checkpoint.get('max_jobs'),'sources':checkpoint.get('sources',['linkedin'])}}


def make_server(app, host="127.0.0.1", port=8787):
    class Handler(BaseHTTPRequestHandler):
        server_version = "JobDesk/1"

        def log_message(self, message, *args):
            if self.path.split("?", 1)[0] == "/api/external-digest/callback":
                LOG.info("Google OAuth callback handled")
                return
            LOG.info(message, *args)

        def _validate_host(self):
            value = self.headers.get("Host", "")
            try:
                parsed = urlsplit("http://"+value)
                port_value = parsed.port
            except ValueError:
                raise APIError(403, "Invalid host")
            if parsed.hostname not in {"localhost", "127.0.0.1", "::1"} or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or port_value != self.server.server_port:
                raise APIError(403, "Dashboard is available only on its local host and port")

        def _headers(self, status, content_type, length=None, extra=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'")
            if length is not None:
                self.send_header("Content-Length", str(length))
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            self.end_headers()

        def _json(self, data, status=200):
            body = json.dumps(data, ensure_ascii=False).encode()
            self._headers(status, "application/json; charset=utf-8", len(body))
            self.wfile.write(body)

        def _dispatch(self):
            self._validate_host()
            parsed = urlsplit(self.path)
            path, query = unquote(parsed.path), parse_qs(parsed.query)
            parts = path.strip("/").split("/")
            if self.command == "POST":
                origin = self.headers.get("Origin")
                if origin is not None and origin != "http://"+self.headers["Host"]:
                    raise APIError(403, "Cross-origin actions are not allowed")
                if self.headers.get("Content-Type", "").split(";", 1)[0].lower() != "application/json":
                    raise APIError(415, "Send JSON")
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    limit = {"/api/profile/import-cv": 8_500_000, "/api/profile": 500_000,
                             "/api/applications/evaluate": 100_000, "/api/external-digest/settings": 70_000}.get(path, 8192)
                    if not 0 < length <= limit:
                        raise APIError(413, "Request body is missing or too large")
                    payload = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeDecodeError):
                    raise APIError(400, "Invalid JSON body")
                if not isinstance(payload, dict):
                    raise APIError(400, "JSON body must be an object")
                if len(parts) == 3 and parts[:2] == ["api", "external-digest"]:
                    return self._json(app.external_digest_action(parts[2], payload, self.server.server_port))
                if parts == ["api", "profile"]:
                    return self._json(app.update_profile(payload))
                if parts == ["api", "profile", "import-cv"]:
                    from profile_documents import extract_cv
                    try:
                        return self._json({"text": extract_cv(payload.get("pdf"))})
                    except ValueError as error:
                        raise APIError(422, str(error)) from error
                if parts == ["api", "applications", "evaluate"]:
                    return self._json(app.assess_application(payload))
                if parts == ["api", "applications", "documents"]:
                    return self._json(app.application_documents(payload))
                if parts == ["api", "runs"]:
                    if set(payload) - {"mode", "maxJobs", "sources"}:
                        raise APIError(400, "Unsupported run option")
                    if 'sources' in payload:
                        return self._json({"task": app.runner.start(payload.get("mode", "sample"), payload.get("maxJobs", 6), payload['sources'])}, 202)
                    return self._json({"task": app.runner.start(payload.get("mode", "sample"), payload.get("maxJobs", 6))}, 202)
                if len(parts) == 4 and parts[:2] == ["api", "tasks"] and parts[3] == "cancel":
                    return self._json({"task": app.runner.cancel(parts[2])})
                raise APIError(404, "Action not found")
            if self.command != "GET":
                raise APIError(405, "Method not allowed")
            if parts == ["api", "external-digest"]:
                return self._json(app.external_digest_status())
            if parts == ["api", "external-digest", "callback"]:
                try:
                    app.external_digest.callback(query)
                except DigestError as error:
                    raise APIError(422, str(error)) from error
                self._headers(303, "text/plain", 0, {"Location": f"http://localhost:{self.server.server_port}/?digest=connected"})
                return
            if parts == ["api", "profile"]:
                return self._json(app.profile_settings())
            if parts == ["api", "overview"]:
                return self._json(app.overview())
            if len(parts) == 3 and parts[:2] == ["api", "tasks"]:
                return self._json(app.runner.get(parts[2]))
            if len(parts) in {3, 4} and parts[:2] == ["api", "runs"]:
                source = query.get("source", ["container"])[0]
                if len(parts) == 3:
                    return self._json(app.store.detail(source, parts[2]))
                if parts[3] == "download":
                    file = app.store.download(source, parts[2], query.get("format", ["csv"])[0])
                    body = file.read_bytes()
                    self._headers(200, mimetypes.guess_type(file.name)[0] or "application/octet-stream", len(body), {"Content-Disposition": f'attachment; filename="{parts[2]}-{file.name}"'})
                    return self.wfile.write(body)
            if parts[0] == "api":
                raise APIError(404, "API route not found")
            relative = path.lstrip("/") or "index.html"
            file = (app.dist/relative).resolve()
            if not file.is_relative_to(app.dist) or any(part.startswith(".") for part in Path(relative).parts):
                raise APIError(404, "File not found")
            if not file.is_file():
                if "." in Path(relative).name:
                    raise APIError(404, "File not found")
                file = app.dist/"index.html"
            if not file.is_file():
                raise APIError(503, "Frontend is not built. Build the dashboard Docker image first.")
            body = file.read_bytes()
            self._headers(200, mimetypes.guess_type(file.name)[0] or "application/octet-stream", len(body))
            self.wfile.write(body)

        def _handle(self):
            try:
                self._dispatch()
            except APIError as error:
                self._json({"error": error.message}, error.status)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                LOG.exception("Dashboard request failed")
                self._json({"error": "Could not complete the local request. Check dashboard logs."}, 500)

        do_GET = _handle
        do_POST = _handle
        do_OPTIONS = _handle

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    parser.add_argument("--native-dir", type=Path)
    parser.add_argument("--reports-dir", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    backend_dir = Path(__file__).resolve().parent
    app_root = backend_dir.parent if backend_dir.name == "backend" else backend_dir
    app = DashboardApp(backend_dir, args.data_dir, args.native_dir, args.reports_dir,
                       dist_dir=app_root / "web/dist")
    server = make_server(app, args.host, args.port)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    print(f"Job desk ready at http://localhost:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.runner.close()
        server.server_close()


if __name__ == "__main__":
    main()
