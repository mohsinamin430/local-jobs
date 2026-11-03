#!/usr/bin/env python3
"""Small, dependency-free collector for LinkedIn's public guest job pages."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
from html.parser import HTMLParser
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit, unquote
from urllib.request import Request, urlopen
from uuid import uuid4

UTC = timezone.utc
LOG = logging.getLogger("local-jobs")
WORK_MODELS = {"on-site": "1", "remote": "2", "hybrid": "3"}
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds")


class Node:
    def __init__(self, tag="root", attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []

    def all(self, cls=None, tag=None):
        found = []
        for child in self.children:
            if isinstance(child, Node):
                if (cls is None or cls in (child.attrs.get("class") or "").split()) and (tag is None or child.tag == tag):
                    found.append(child)
                found.extend(child.all(cls, tag))
        return found

    def first(self, cls=None, tag=None):
        return next(iter(self.all(cls, tag)), Node())

    def text(self):
        parts = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            elif child.tag not in {"script", "style"}:
                value = child.text()
                parts.append(("\n" if child.tag in {"br", "p", "li", "div", "h2", "h3", "ul"} else " ") + value)
        return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", "".join(parts))).strip()


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_search(source):
    root = Document(source).root
    jobs, seen = [], set()
    for card in root.all("base-search-card"):
        urn = card.attrs.get("data-entity-urn", "")
        link = card.first("base-card__full-link").attrs.get("href", "")
        match = re.search(r"jobPosting:(\d+)", urn) or re.search(r"(?:-|/)(\d{7,})(?:[/?]|$)", link)
        if not match or match[1] in seen:
            continue
        seen.add(match[1])
        posted = card.first(tag="time")
        jobs.append({
            "id": match[1], "url": f"https://www.linkedin.com/jobs/view/{match[1]}/",
            "title": card.first("base-search-card__title").text(),
            "company": card.first("base-search-card__subtitle").text(),
            "location": card.first("job-search-card__location").text(),
            "search_posted_text": posted.text(), "search_posted_date": posted.attrs.get("datetime", ""),
            "company_url": card.first("base-search-card__subtitle").first(tag="a").attrs.get("href", ""),
        })
    return jobs


def public_url(value):
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"} and parsed.hostname and not parsed.username:
        return value
    return ""


def parse_detail(source):
    root = Document(source).root
    description = root.first("show-more-less-html__markup").text()
    if not description:
        raise ValueError("No public job description: page may be closed, gated, or markup has changed")
    criteria = {}
    for item in root.all("description__job-criteria-item"):
        criteria[item.first("description__job-criteria-subheader").text()] = item.first("description__job-criteria-text").text()
    apply_url = ""
    for link in root.all(tag="a"):
        href = link.attrs.get("href", "")
        if "/jobs/view/externalApply/" in href:
            apply_url = public_url(parse_qs(urlsplit(href).query).get("url", [""])[0])
            break
    # Public guest pages sometimes expose the application target in a JSON comment.
    if not apply_url:
        match = re.search(r'"companyApplyUrl"\s*:\s*("(?:[^"\\]|\\.)*")', source)
        if match:
            try:
                apply_url = public_url(html.unescape(json.loads(match[1])))
            except ValueError:
                pass
    recruiter = root.first("message-the-recruiter__cta")
    recruiter_url = public_url(recruiter.attrs.get("href", ""))
    salary = root.first("salary").text() or root.first("compensation__salary").text()
    if not salary:
        salary = next((line.strip() for line in description.splitlines() if re.search(r"\b(?:salary|base pay|pay range)\b", line, re.I) and re.search(r"[£$€]\s*\d|\d\s*(?:GBP|USD|EUR)\b", line, re.I)), "")[:500]
    candidates = []
    poster = root.first("message-the-recruiter")
    for link in poster.all(tag="a"):
        href = link.attrs.get("href", "")
        if "/in/" in href:
            candidates.append({"name": poster.first("base-main-card__title").text() or link.text(), "title": poster.first("base-main-card__subtitle").text(), "url": href})
    organisation = root.first("topcard__org-name-link")
    return {
        "description": description, "criteria": criteria, "apply_url": apply_url,
        "detail_posted_text": root.first("posted-time-ago__text").text(),
        "applicants_text": root.first("num-applicants__caption").text() or root.first("num-applicants__figure").text(),
        "recruiter_url": recruiter_url, "salary": salary, "named_contact_candidates": candidates,
        "company_url": organisation.attrs.get("href", ""),
    }


def relative_interval(text, observed):
    """Return an interval, rather than inventing an exact time from '6 hours ago'."""
    match = re.fullmatch(r"\s*(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago\s*", text.lower())
    if not match:
        if text.strip().lower() in {"just now", "moments ago"}:
            return observed - timedelta(minutes=1), observed
        return None
    number = int(match[1])
    unit = {"second": 1, "minute": 60, "hour": 3600, "day": 86400, "week": 604800, "month": 2678400, "year": 31622400}[match[2]]
    return observed - timedelta(seconds=(number + 1) * unit), observed - timedelta(seconds=number * unit)


def freshness(job, observed, seconds):
    intervals = []
    for field in ("search_posted_text", "detail_posted_text"):
        interval = relative_interval(job.get(field, ""), observed)
        if interval:
            intervals.append(interval)
    if not intervals:
        return {"freshness": "unverified", "posted_earliest": "", "posted_latest": "", "freshness_basis": "No usable relative posting time; date-only data is insufficient"}
    earliest, latest = min(x[0] for x in intervals), max(x[1] for x in intervals)
    lower, upper = max(x[0] for x in intervals), min(x[1] for x in intervals)
    status = "recent" if earliest >= observed - timedelta(seconds=seconds) else "outside_window_or_uncertain"
    # Two non-overlapping observations (allow 5 minutes of rounding / retrieval drift) are conflicting.
    if lower > upper + timedelta(minutes=5):
        status = "conflicting"
    raw_date = job.get("search_posted_date", "")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
        date = datetime.fromisoformat(raw_date).date()
        if date < (earliest - timedelta(days=1)).date() or date > (latest + timedelta(days=1)).date():
            status = "conflicting"
    return {"freshness": status, "posted_earliest": iso(earliest), "posted_latest": iso(latest), "freshness_basis": "LinkedIn relative labels; bounded estimate, not exact publication timestamp"}


def match_keywords(text, groups):
    matches = []
    for group in groups:
        terms = [group["keyword"], *group.get("aliases", [])]
        if any(re.search(r"(?<![\w])" + re.escape(term) + r"(?![\w])", text, re.I) for term in terms if term):
            matches.append(group["keyword"])
    return matches


def seconds_from(value):
    if not re.fullmatch(r"r\d+", str(value)) or not 0 < int(value[1:]) <= 2678400:
        raise ValueError("publishedAt must be r followed by 1..2678400 seconds, e.g. r86400")
    return int(value[1:])


def build_plan(config):
    warnings = []
    for flag in ("useAiJobDescriptionParsing", "enrichCompanyData", "excludeRecruitingAgencies", "filterEasyApply", "filterUnder10Applicants", "requireRecruiterProfile", "requireSalaryInfo"):
        if config.get(flag):
            raise ValueError(f"{flag}=true is not supported by this first version; refusing to silently skip it")
    if config.get("aiQuestions"):
        warnings.append("AI questions are inactive because useAiJobDescriptionParsing is false; no sponsorship, language or work-model AI filtering is performed")
    seconds = seconds_from(config.get("publishedAt", "r86400"))
    searches = []
    if config.get("roleVariants"):
        for role in config["roleVariants"]:
            for model, location in config.get("searchLocations", {"remote": "United Kingdom", "hybrid": "United Kingdom"}).items():
                searches.append({"keyword": role, "location": location, "workModels": [model]})
    elif config.get("searches"):
        searches = [dict(s) for s in config["searches"]]
    else:
        mode = config.get("sourceMode", "keyword")
        if mode not in {"keyword", "urls", "both"}:
            raise ValueError("sourceMode must be keyword, urls or both")
        if mode in {"keyword", "both"}:
            for keyword in config.get("keyword", []):
                for location in config.get("locations", []):
                    searches.append({"keyword": keyword, "location": location})
        if mode == "keyword" and config.get("startUrls"):
            warnings.append("startUrls ignored in sourceMode=keyword; they contain separate searches. Select urls or both to include them")
        if mode in {"urls", "both"}:
            for item in config.get("startUrls", []):
                parsed = urlsplit(item["url"] if isinstance(item, dict) else item)
                if parsed.scheme != "https" or not (parsed.hostname == "linkedin.com" or (parsed.hostname or "").endswith(".linkedin.com")) or parsed.path.rstrip("/") != "/jobs/search":
                    raise ValueError("startUrls must be HTTPS LinkedIn /jobs/search/ URLs")
                query = parse_qs(parsed.query)
                supported = {"keywords", "location", "geoId", "f_TPR", "f_WT", "f_E", "f_JT", "f_C", "distance", "sortBy", "position", "pageNum", "start", "currentJobId", "refresh", "trk", "origin"}
                unknown = sorted(set(query) - supported)
                if unknown:
                    raise ValueError(f"Unsupported URL filters: {', '.join(unknown)}")
                search = {"keyword": query.get("keywords", [""])[0], "location": query.get("location", [""])[0]}
                for key in ("geoId", "f_WT", "f_E", "f_JT", "f_C", "distance"):
                    if key in query:
                        search[key] = query[key][0]
                if query.get("f_TPR", [f"r{seconds}"])[0] != f"r{seconds}":
                    warnings.append("publishedAt overrides a different f_TPR in startUrls")
                searches.append(search)
    if not searches:
        raise ValueError("No searches: supply searches, or keyword + locations, or sourceMode=urls + startUrls")
    plans = []
    for search in searches:
        allowed = {"keyword", "location", "geoId", "workModels", "f_WT", "f_E", "f_JT", "f_C", "distance"}
        if set(search) - allowed:
            raise ValueError(f"Unknown search options: {sorted(set(search) - allowed)}")
        if not search.get("keyword") or not (search.get("location") or search.get("geoId")):
            raise ValueError("Every search needs keyword and location or geoId")
        params = {"keywords": search["keyword"], "f_TPR": f"r{seconds}", "sortBy": "DD"}
        for key in ("location", "geoId", "f_WT", "f_E", "f_JT", "f_C", "distance"):
            if search.get(key):
                params[key] = str(search[key])
        if search.get("workModels"):
            params["f_WT"] = ",".join(WORK_MODELS[v] for v in search["workModels"])
        url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?" + urlencode(params)
        if url not in [p["url"] for p in plans]:
            plans.append({"search": search, "params": params, "url": url})
    for key, default, low, high in (("maxJobsPerSearch", 25, 1, 1000), ("maxPagesPerSearch", 5, 1, 100), ("maxJobsTotal", 100, 1, 5000), ("requestDelaySeconds", 3, 1, 60)):
        value = config.get(key, default)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not low <= value <= high or (key != "requestDelaySeconds" and int(value) != value):
            raise ValueError(f"{key} must be within {low}..{high}" )
    title_pattern = config.get("titlePattern")
    if title_pattern:
        re.compile(title_pattern, re.I)
    if "jobSources" in config:
        if not config.get("personalPolicy"):
            raise ValueError("Multiple job sources require personalPolicy verification")
        from job_boards import expand_plan
        plans = expand_plan(plans, config)
    return {"window_seconds": seconds, "searches": plans, "warnings": warnings,
            "maxJobsPerSearch": int(config.get("maxJobsPerSearch", 25)), "maxPagesPerSearch": int(config.get("maxPagesPerSearch", 5)),
            "maxJobsTotal": int(config.get("maxJobsTotal", 100)), "requestDelaySeconds": config.get("requestDelaySeconds", 3)}


class AccessError(RuntimeError):
    pass


class Client:
    def __init__(self, delay=3, limiter=None, checkpoint=None):
        self.delay, self.last_request, self.requests = delay, 0.0, 0
        self.limiter, self.checkpoint = limiter, checkpoint

    def get(self, url):
        # Only the two fixed public LinkedIn endpoint paths are requested.
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != "www.linkedin.com" or not parts.path.startswith("/jobs-guest/jobs/api/"):
            raise ValueError("Unexpected request target")
        if self.checkpoint:
            cached = self.checkpoint.get(url)
            if cached is not None:
                return cached
        for attempt in range(3):
            if self.limiter:
                self.limiter.before(url)
            else:
                time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            self.requests += 1
            try:
                request = Request(url, headers={"User-Agent": "LocalJobsCollector/0.1", "Accept-Language": "en-GB,en;q=0.9"})
                with urlopen(request, timeout=30) as response:
                    if urlsplit(response.url).path.startswith(("/authwall", "/checkpoint", "/login", "/uas/")):
                        raise AccessError("LinkedIn redirected to an access/sign-in page")
                    body = response.read(4_000_001)
                    if len(body) > 4_000_000:
                        raise ValueError("Unexpectedly large response")
                    source = body.decode("utf-8", errors="replace")
                    if self.limiter:
                        self.limiter.success(url)
                    if self.checkpoint:
                        self.checkpoint.put(url, source)
                    return source
            except HTTPError as error:
                if error.code == 429 and self.limiter:
                    raise self.limiter.limited(url,error.headers) from error
                if error.code in {401, 403, 429, 999}:
                    raise AccessError(f"LinkedIn returned HTTP {error.code}; stopped without bypassing the restriction") from error
                if error.code < 500 or attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))
            except (URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(2 ** (attempt + 1))
        raise RuntimeError("Request failed")


def open_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, started TEXT, finished TEXT, status TEXT, summary TEXT);
        CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, first_seen TEXT, last_seen TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS run_jobs (run_id TEXT, job_id TEXT, payload TEXT, PRIMARY KEY(run_id,job_id));
    """)
    return db


def record_job(db, run_id, job, observed):
    previous = db.execute("SELECT first_seen FROM jobs WHERE id=?", (job["id"],)).fetchone()
    job["first_seen"] = previous[0] if previous else iso(observed)
    job["last_seen"] = iso(observed)
    job["new_to_database"] = previous is None
    payload = json.dumps(job, ensure_ascii=False)
    db.execute("INSERT INTO jobs VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen,payload=excluded.payload", (job["id"], job["first_seen"], job["last_seen"], payload))
    db.execute("INSERT OR REPLACE INTO run_jobs VALUES(?,?,?)", (run_id, job["id"], payload))
    db.commit()


def atomic_text(path, content):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def csv_cell(value):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False)
    value = str(value) if value is not None else ""
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def export_results(directory, jobs, summary):
    directory.mkdir(parents=True, exist_ok=True)
    atomic_text(directory / "jobs.json", json.dumps(jobs, indent=2, ensure_ascii=False) + "\n")
    atomic_text(directory / "summary.json", json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    fields = ["id", "title", "company", "location", "url", "apply_url", "search_posted_text", "detail_posted_text", "posted_earliest", "posted_latest", "freshness", "freshness_basis", "requested_work_models", "salary", "criteria", "matched_keywords", "keyword_score_percent", "first_seen", "last_seen", "new_to_database", "description"]
    with (directory / "jobs.csv").open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            writer.writerow({key: csv_cell(value) for key, value in job.items() if key in fields})
    lines = [f"# LinkedIn jobs — {summary['started'][:10]}", "", f"Run status: **{summary['status']}** · Exported: **{len(jobs)}** · Requests: {summary['requests']}", "", "Posting times below are bounded estimates from LinkedIn's relative labels. Search work-model filters are requested from LinkedIn; they are not independently verified classifications.", ""]
    for job in jobs:
        title = re.sub(r"[\[\]\r\n]", " ", job["title"])
        lines.extend([f"- [{title}]({job['url']}) — {job['company']} — {job['location']}", f"  Posted: {job.get('detail_posted_text') or job.get('search_posted_text', '')} · Skill groups matched: {', '.join(job.get('matched_keywords', [])) or 'none'}"])
    lines.extend(["", "Run notes:", *[f"- {note}" for note in summary["warnings"]], *[f"- {error}" for error in summary["errors"]]])
    atomic_text(directory / "digest.md", "\n".join(lines) + "\n")


def run(config, data_dir, max_jobs=None):
    if config.get("personalPolicy"):
        from personal_pipeline import run_personalized
        return run_personalized(config, data_dir, max_jobs)
    import fcntl  # Linux/WSL: prevents overlapping scheduled runs against the same data directory.
    plan = build_plan(config)
    if max_jobs is not None and max_jobs < 1:
        raise ValueError("--max-jobs must be positive")
    if max_jobs:
        plan["maxJobsTotal"] = min(max_jobs, plan["maxJobsTotal"])
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = (data_dir / "collector.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ValueError("Another run is using this data directory")
    db = open_db(data_dir / "jobs.sqlite3")
    started = datetime.now(UTC)
    run_id = started.strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]
    summary = {"run_id": run_id, "started": iso(started), "status": "running", "warnings": plan["warnings"], "errors": [], "searches": [], "requests": 0, "examined": 0, "rejected": {}, "exported": 0, "new": 0}
    db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (run_id, iso(started), None, "running", json.dumps(summary)))
    db.commit()
    client = Client(plan["requestDelaySeconds"])
    seen, jobs = set(), []
    terminal = False
    def reject(reason):
        summary["rejected"][reason] = summary["rejected"].get(reason, 0) + 1
    try:
        for search in plan["searches"]:
            if len(seen) >= plan["maxJobsTotal"] or terminal:
                break
            report = {"params": search["params"], "pages": 0, "examined": 0, "end": "page_limit"}
            summary["searches"].append(report)
            offset, search_seen = 0, set()
            for _ in range(plan["maxPagesPerSearch"]):
                if report["examined"] >= plan["maxJobsPerSearch"] or len(seen) >= plan["maxJobsTotal"]:
                    report["end"] = "job_limit"
                    break
                url = search["url"] + "&start=" + str(offset)
                LOG.info("Search %s / %s offset=%s", search["params"]["keywords"], search["params"].get("location", search["params"].get("geoId")), offset)
                try:
                    source = client.get(url)
                    cards = parse_search(source)
                    report["pages"] += 1
                    if not cards:
                        stripped = re.sub(r"<!--.*?-->", "", source, flags=re.S).strip()
                        if stripped and not re.search(r"no (?:matching )?(?:results|jobs)(?: found)?|no more jobs", source, re.I):
                            raise ValueError("Nonempty search response contained no recognizable job cards")
                        report["end"] = "no_more_results"
                        break
                except AccessError as error:
                    summary["errors"].append(str(error))
                    terminal, report["end"] = True, "blocked"
                    break
                except (ValueError, HTTPError, URLError, TimeoutError) as error:
                    summary["errors"].append(f"Search failed: {error}")
                    report["end"] = "error"
                    break
                offset += len(cards)
                fresh_cards = [card for card in cards if card["id"] not in search_seen]
                search_seen.update(card["id"] for card in cards)
                if not fresh_cards:
                    report["end"] = "repeated_page"
                    summary["warnings"].append("Pagination repeated the same jobs; search stopped")
                    break
                for job in fresh_cards:
                    if report["examined"] >= plan["maxJobsPerSearch"] or len(seen) >= plan["maxJobsTotal"]:
                        report["end"] = "job_limit"
                        break
                    report["examined"] += 1
                    if job["id"] in seen:
                        continue
                    seen.add(job["id"])
                    summary["examined"] += 1
                    if config.get("titlePattern") and not re.search(config["titlePattern"], job["title"], re.I):
                        reject("title_mismatch")
                        continue
                    observed = datetime.now(UTC)
                    preliminary = freshness(job, observed, plan["window_seconds"])
                    if preliminary["freshness"] == "outside_window_or_uncertain":
                        reject("outside_window_or_uncertain")
                        continue
                    try:
                        detail = client.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job['id']}")
                        job.update(parse_detail(detail))
                    except AccessError as error:
                        summary["errors"].append(f"Job {job['id']}: {error}")
                        terminal, report["end"] = True, "blocked"
                        break
                    except (ValueError, HTTPError, URLError, TimeoutError) as error:
                        summary["errors"].append(f"Job {job['id']} detail failed: {error}")
                        continue
                    observed = datetime.now(UTC)
                    job.update(freshness(job, observed, plan["window_seconds"]))
                    if job["freshness"] != "recent":
                        reject(job["freshness"])
                        continue
                    if re.search(r"no longer accepting applications|no longer available|job is closed", Document(detail).root.text(), re.I):
                        reject("closed")
                        continue
                    job["observed_at"] = iso(observed)
                    job["requested_work_models"] = [key for key, value in WORK_MODELS.items() if value in search["params"].get("f_WT", "").split(",")]
                    job["source"] = "linkedin_public_guest"
                    job["matched_keywords"] = match_keywords(job["title"] + "\n" + job["description"], config.get("resumeKeywords", []))
                    groups = len(config.get("resumeKeywords", []))
                    job["keyword_score_percent"] = round(len(job["matched_keywords"]) / groups * 100) if groups else None
                    record_job(db, run_id, job, observed)
                    summary["new"] += int(job["new_to_database"])
                    if not config.get("saveOnlyUniqueItems") or job["new_to_database"]:
                        jobs.append(job)
                    LOG.info("Collected %s — %s", job["title"], job["company"])
                if terminal:
                    break
        summary["status"] = "partial" if summary["errors"] and jobs else "failed" if summary["errors"] else "completed"
    except BaseException as error:
        summary["status"] = "interrupted" if isinstance(error, KeyboardInterrupt) else "failed"
        summary["errors"].append(f"{type(error).__name__}: {error}")
        raise
    finally:
        summary["coverage_limited"] = len(summary["searches"]) < len(plan["searches"]) or any(item["end"] in {"job_limit", "page_limit", "repeated_page"} for item in summary["searches"])
        if summary["coverage_limited"]:
            summary["warnings"].append("Collection stopped at a configured limit or repeated page; results are not an exhaustive set of matching LinkedIn jobs")
        summary.update({"finished": iso(datetime.now(UTC)), "requests": client.requests, "exported": len(jobs), "config_sha256": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()})
        directory = data_dir / "runs" / run_id
        export_results(directory, jobs, summary)
        atomic_text(data_dir / "latest.json", json.dumps({"run_id": run_id, "directory": str(directory.resolve()), "summary": summary}, indent=2) + "\n")
        db.execute("UPDATE runs SET finished=?,status=?,summary=? WHERE id=?", (summary["finished"], summary["status"], json.dumps(summary), run_id))
        db.commit()
        db.close()
        lock.close()
    print(json.dumps({"directory": str(directory.resolve()), **summary}, indent=2))
    return 0 if summary["status"] == "completed" else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "run", "status"])
    parser.add_argument("--config", type=Path, default=Path(os.environ.get("DATA_DIR", "data")) / "profile.json")
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    parser.add_argument("--max-jobs", type=int, help="Smoke-test cap on unique jobs examined (not on jobs returned)")
    parser.add_argument("--sources", help="Comma-separated job sources; overrides configured jobSources")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.command == "status":
            print((args.data_dir / "latest.json").read_text(encoding="utf-8"))
            return 0
        config = json.loads(args.config.read_text(encoding="utf-8-sig"))
        if args.sources:
            config['jobSources'] = args.sources.split(',')
        if args.command == "plan":
            print(json.dumps(build_plan(config), indent=2, ensure_ascii=False))
            return 0
        return run(config, args.data_dir, args.max_jobs)
    except (ValueError, OSError, KeyError) as error:
        LOG.error("%s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
