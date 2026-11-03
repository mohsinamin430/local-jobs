"""Personalized digest orchestration with auditable verification and bounded discovery."""
from __future__ import annotations

from collections import Counter, deque
from search_scheduler import SearchScheduler
from datetime import datetime, timezone
import csv
import hashlib
import json
import logging
from pathlib import Path
import re
import traceback
from urllib.error import HTTPError, URLError
from uuid import uuid4
from zoneinfo import ZoneInfo
from rate_control import LinkedInControl, ResponseCheckpoint, RateLimited, BoardControl, combined_cooldown

import collector as c
from contact_research import discover_and_verify_contact
from personal_rules import evaluate_job, dedup_keys, canonical_url
from public_research import PublicResearch, enrich_job, job_evidence, ResearchLimit, ResearchBlocked
import job_boards as boards

LOG = logging.getLogger("local-jobs")
UTC = timezone.utc


def index_jobs(config, now):
    rows, reports = [], []
    for value in config.get("crossDigestPaths", []):
        path = Path(value)
        report = {"path": str(path), "status": "incomplete"}
        reports.append(report)
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(document, list):
                raise ValueError("A digest index requires jobs plus an as_of date; an undated list is not current")
            report.update(as_of=document.get("as_of", ""), source_url=document.get("source_url", ""))
            if document.get("as_of", "")[:10] != now.astimezone(ZoneInfo("Europe/London")).date().isoformat():
                report["error"] = "Cross-digest snapshot is not from today's execution date; supply a fresh index"
            else:
                rows.extend(document.get("jobs", []))
                report.update(status="ok", count=len(document.get("jobs", [])))
        except (OSError, ValueError, TypeError) as error:
            report["error"] = str(error)
    if not reports:
        reports.append({"status": "incomplete", "error": "No cross-digest index configured; other digest sources could not be deduplicated"})
    return rows, reports


def key_set(job):
    values = dedup_keys(job)
    if isinstance(values, dict):
        return {str(value) for value in values.values() if value}
    return {str(value) for value in values if value}


def write_report(directory, jobs, decisions, summary):
    directory.mkdir(parents=True,exist_ok=True)
    c.atomic_text(directory/'jobs.json',json.dumps(jobs,indent=2,ensure_ascii=False)+'\n')
    c.atomic_text(directory/'summary.json',json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
    c.atomic_text(directory / "decisions.json", json.dumps(decisions, indent=2, ensure_ascii=False) + "\n")
    c.atomic_text(directory / "verification-sources.json", json.dumps(summary["verification_sources"], indent=2, ensure_ascii=False) + "\n")
    fields = ["id", "source", "found_on", "title", "company", "location", "work_model", "salary", "posting_time_display", "posted_at", "posted_earliest", "posted_latest", "fit", "fit_explanation", "contact", "contact_status", "verified_email", "email_status", "preferred_apply_url", "url", "description", "freshness_evidence", "work_model_evidence", "verification_sources", "contact_evidence"]
    with (directory / "jobs.csv.tmp").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            writer.writerow({field: c.csv_cell(job.get(field, "")) for field in fields})
    (directory/'jobs.csv.tmp').replace(directory/'jobs.csv')
    counts = summary["breakdown"]
    lines = [f"# Job Desk search results — {summary['started'][:10]}", "",
             f"Status: **{summary['status']}** · Qualifying after deduplication: **{summary['qualifying_after_dedup']}** · Exported: **{len(jobs)}**",
             f"Work type: Remote {counts['work_type'].get('remote', 0)} · Hybrid {counts['work_type'].get('hybrid', 0)} · On-site {counts['work_type'].get('on-site', 0)}",
             f"Fit: Strong {counts['fit'].get('Strong', 0)} · Possible {counts['fit'].get('Possible', 0)} · Stretch {counts['fit'].get('Stretch', 0)}",
             f"Reliable hiring contacts: {summary['reliable_hiring_contacts']} · Verified emails: {summary['verified_email_count']}", ""]
    for job in jobs:
        title = re.sub(r"[\[\]\r\n]", " ", job["title"])
        lines.extend([f"## [{title}]({job['preferred_apply_url']})", f"{job['company']} · {job['location']} · {job['work_model']} · {job['fit']}",
                      job["fit_explanation"], f"Hiring contact: {job['contact']}", ""])
    lines.extend(["## Coverage and source status", ""])
    for report in summary["searches"]:
        lines.append(f"- {boards.SOURCES.get(report.get('source', 'linkedin'))} / {report['params']['keywords']} / {report['bucket']}: {report['status']}; examined {report['examined']}; {report['end']}")
    lines.extend(["", "## Notes", "", *[f"- {value}" for value in summary["warnings"]], *[f"- {value}" for value in summary["errors"]]])
    c.atomic_text(directory / "digest.md", "\n".join(lines) + "\n")


def run_personalized(config, data_dir, max_jobs=None):
    import sys
    from run_orchestrator import run_personalized as run
    return run(sys.modules[__name__], config, data_dir, max_jobs)
