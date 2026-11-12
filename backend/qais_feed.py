"""Jobs by Qais discovery, followed by exact public-vacancy verification."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import hashlib
import re
from urllib.parse import urlsplit

from personal_rules import _company_identity, _norm, canonical_url
from job_feeds import https_url


BOARD_URL = "https://jobsbyqais.coderco.io/"
REMOVED_DOMAINS = ("indeed.com", "indeed.co.uk", "glassdoor.com", "glassdoor.co.uk",
                   "welcometothejungle.com", "adzuna.com", "adzuna.co.uk")


class ScriptParser(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.scripts, self.current = [], None
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.current = []

    def handle_data(self, value):
        if self.current is not None:
            self.current.append(value)

    def handle_endtag(self, tag):
        if tag == "script" and self.current is not None:
            self.scripts.append("".join(self.current))
            self.current = None


def board_data(body):
    """Decode embedded JSON without executing JavaScript or following marketing links."""
    for script in ScriptParser(body).scripts:
        match = re.search(r"(?:^|[;\n])\s*(?:const|let|var)\s+DATA\s*=\s*", script)
        if not match:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(script, match.end())
        except (ValueError, TypeError):
            raise ValueError("Jobs by Qais embedded data could not be decoded") from None
        if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
            raise ValueError("Jobs by Qais did not expose a recognised jobs list")
        if any(not isinstance(row, dict) or any(not isinstance(row.get(key), str) for key in
               ("title", "company", "location", "url")) for row in data["jobs"]):
            raise ValueError("Jobs by Qais returned malformed job records; coverage is incomplete")
        return data
    raise ValueError("Jobs by Qais public job data is missing; page layout may have changed")


def linkedin_id(url):
    parts = urlsplit(url)
    if parts.hostname == "linkedin.com" or (parts.hostname or "").endswith(".linkedin.com"):
        match = re.fullmatch(r"/jobs/view/(\d{7,})/?", parts.path)
        return match.group(1) if match else ""
    return ""


class QaisFeed:
    label, domain = "Jobs by Qais", "jobsbyqais.coderco.io"

    def plans(self, searches, config):
        yield {"source": "jobsbyqais", "params": {"keywords": "UK DevOps, Platform, Cloud and SRE roles"},
               "url": BOARD_URL}

    def page(self, search, offset, get):
        data = board_data(get(BOARD_URL))
        cards, seen = [], set()
        for row in data["jobs"]:
            url = canonical_url(https_url(row["url"]))
            host = urlsplit(url).hostname or ""
            if not url or host == self.domain or any(host == domain or host.endswith("." + domain)
                                                     for domain in REMOVED_DOMAINS):
                continue
            if url in seen:
                continue
            seen.add(url)
            flags = row.get("flags") or []
            if not isinstance(flags, list) or any(not isinstance(flag, str) for flag in flags):
                raise ValueError("Jobs by Qais flags have an unsupported format")
            if any(re.search(r"pay[ -]to[ -]train|training course", flag, re.I) for flag in flags):
                continue
            linked_id = linkedin_id(url)
            identifier = "jobsbyqais:" + (linked_id or hashlib.sha256(url.encode()).hexdigest()[:24])
            card = {"id": identifier, "title": row["title"], "company": row["company"],
                    "location": row["location"], "url": url, "discovery_location": row["location"],
                    "source_urls": [BOARD_URL, url], "qais_original_source": row.get("source", ""),
                    "qais_flags": flags}
            if linked_id:
                card["linkedin_job_id"] = linked_id
            posted = row.get("posted")
            if isinstance(posted, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T[^\s]+)?", posted):
                card["feed_search_date"] = posted
            # Board refresh time, skill tags and junior labels are not vacancy evidence.
            cards.append(card)
        return cards, len(data["jobs"]), False

    def detail(self, job, search, research):
        identifier = linkedin_id(job["url"])
        if not identifier:
            from additional_feeds import exact_advert
            job["location"] = ""  # Discovery geography cannot prove the exact vacancy's location.
            exact_advert(job, research)
            return
        from collector import Document, parse_detail
        from public_research import job_evidence

        endpoint = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/" + identifier
        body = research.get(endpoint)
        root = Document(body).root
        title = root.first("top-card-layout__title").text()
        company = root.first("topcard__org-name-link").text()
        if not title or _norm(title) != _norm(job["title"]):
            raise ValueError("The exact advert title did not match the Jobs by Qais listing")
        if not company or _company_identity(company) != _company_identity(job["company"]):
            raise ValueError("The exact advert employer did not match the Jobs by Qais listing")
        details = parse_detail(body)
        location = root.first("topcard__flavor--bullet").text()
        canonical = "https://www.linkedin.com/jobs/view/" + identifier + "/"
        job.update(details, title=title, company=company, location=location, url=canonical)
        observation = getattr(getattr(research, "checkpoint", None), "observed_at", None)
        observed = observation(endpoint) if callable(observation) else None
        job["detail_observed_at"] = observed if isinstance(observed, str) and observed else datetime.now(timezone.utc).isoformat()
        evidence = job_evidence(body, canonical, target=job)
        for field in ("structured_dates", "listing_work_model", "applicant_locations"):
            job[field] = evidence[field]
        for candidate in job.get("named_contact_candidates", []):
            candidate["source_url"] = canonical
        job["listing_closed"] = bool(re.search(r"no longer accepting applications|no longer available|job is closed", root.text(), re.I))
        job["source_urls"] = list(dict.fromkeys(job.get("source_urls", []) + [canonical, endpoint]))
