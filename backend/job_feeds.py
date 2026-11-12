"""Read-only job feeds. The existing collector still qualifies and deduplicates jobs."""

from datetime import timedelta
import html
import json
import re
from urllib.parse import urlencode, urlsplit

from personal_rules import _date_interval, _engineering_role, _freshness, hidden_advert_title
from public_research import PageParser


def https_url(value):
    if not isinstance(value, str):
        return ""
    url = urlsplit(value)
    return value if url.scheme == "https" and url.hostname and not url.username and not url.password else ""


def text(value):
    return PageParser(value).text if isinstance(value, str) else ""


def date_evidence(field, value, source, url):
    return [{"field": field, "value": value, "source": source, "url": url}] if value else []


def matches_role(context, title):
    if hidden_advert_title(title):
        return False
    pattern = context.config.get("titlePattern")
    if pattern:
        return bool(re.search(pattern, title, re.I))
    roles = context.policy.get("roles")
    if roles:
        from local_profile import role_pattern
        return any(re.search(role_pattern(role), title, re.I) for role in roles)
    return _engineering_role(title)


def fresh_candidate(job, now, policy):
    # Date-only values can still be corroborated by the exact public listing.
    try:
        search_date = _date_interval(job.get("feed_search_date"))
    except ValueError:
        search_date = None
    cutoff = now - timedelta(hours=policy.get("max_age_hours", 24))
    if search_date and search_date[1] < cutoff:
        return False
    return _freshness(job, now, policy)[0] != "reject"


class EmployerFeed:
    page_size = 100

    def __init__(self, source, label, domain, default_boards):
        self.source, self.label, self.domain = source, label, domain
        self.default_boards = default_boards

    def plans(self, searches, config):
        boards = config.get("employerFeeds", {}).get(self.source, self.default_boards)
        if not isinstance(boards, list) or not boards or any(
                not isinstance(board, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", board)
                for board in boards):
            raise ValueError(f"employerFeeds.{self.source} must contain one or more employer board IDs")
        for board in dict.fromkeys(boards):
            yield {"source": self.source, "board": board, "params": {"keywords": board},
                   "url": self.page_url(board, 0)}

    def page_url(self, board, offset):
        if self.source == "greenhouse":
            return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
        return f"https://api.smartrecruiters.com/v1/companies/{board}/postings?" + urlencode(
            {"limit": self.page_size, "offset": offset, "country": "gb"})

    def page(self, search, offset, get):
        board = search["board"]
        url = self.page_url(board, offset)
        body = json.loads(get(url))
        if self.source == "greenhouse":
            rows = body["jobs"]
            return [self.greenhouse_job(row, board, url) for row in rows], len(rows), False
        rows = body["content"]
        cards = [self.smartrecruiters_job(row, board, url) for row in rows]
        return cards, len(rows), offset + len(rows) < body["totalFound"]

    def greenhouse_job(self, row, board, feed_url):
        return {
            "id": f"greenhouse:{board}:{row['id']}", "title": row["title"],
            "company": row.get("company_name") or "", "location": (row.get("location") or {}).get("name", ""),
            "url": https_url(row.get("absolute_url")), "description": text(html.unescape(row.get("content") or "")),
            "structured_dates": date_evidence("first_published", row.get("first_published"), "greenhouse_employer_feed", feed_url),
        }

    def smartrecruiters_job(self, row, board, feed_url):
        location = row.get("location") or {}
        workplace = "hybrid" if location.get("hybrid") is True else "remote" if location.get("remote") is True else ""
        posting_id = str(row["id"])
        if not re.fullmatch(r"[A-Za-z0-9-]+", posting_id):
            raise ValueError("Invalid SmartRecruiters posting ID")
        return {
            "id": f"smartrecruiters:{board}:{posting_id}", "title": row["name"],
            "company": (row.get("company") or {}).get("name", ""),
            "location": location.get("fullLocation") or location.get("city") or "",
            "job_location_country": location.get("country") or "",
            "listing_work_model": {"value": workplace, "source": "smartrecruiters_employer_feed", "url": feed_url},
            "url": f"https://jobs.smartrecruiters.com/{board}/{posting_id}",
            "detail_url": f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{posting_id}",
            "structured_dates": date_evidence("releasedDate", row.get("releasedDate"), "smartrecruiters_employer_feed", feed_url),
        }

    def detail(self, job, search, get):
        if self.source == "greenhouse":
            url = f"https://boards-api.greenhouse.io/v1/boards/{search['board']}"
            board = json.loads(get(url))
            if str(board.get("name", "")).casefold() == job["company"].casefold():
                job["employer_evidence"] = {"name": job["company"], "description": text(board.get("content")), "url": url}
            return
        url = job.pop("detail_url")
        detail = json.loads(get(url))
        if job["id"].rsplit(":", 1)[-1] != str(detail.get("id")):
            raise ValueError("SmartRecruiters detail did not match the requested job")
        if detail.get("active") is False:
            job["listing_closed"] = True
        sections = (detail.get("jobAd") or {}).get("sections") or {}
        company_description = text((sections.get("companyDescription") or {}).get("text"))
        job["description"] = "\n".join(text((sections.get(name) or {}).get("text"))
                                        for name in ("jobDescription", "qualifications", "additionalInformation"))
        job["url"] = https_url(detail.get("postingUrl")) or job["url"]
        company = (detail.get("company") or {}).get("name", "")
        if company and company.casefold() == job["company"].casefold():
            job["employer_evidence"] = {"name": company, "description": company_description, "url": url}
        job["apply_url"] = https_url(detail.get("applyUrl"))
        job["apply_url_verified"] = bool(job["apply_url"])
        job["structured_dates"] = date_evidence("releasedDate", detail.get("releasedDate"), "smartrecruiters_employer_detail", url) or job["structured_dates"]


FEEDS = {
    "greenhouse": EmployerFeed("greenhouse", "Greenhouse", "greenhouse.io", ["monzo"]),
    "smartrecruiters": EmployerFeed("smartrecruiters", "SmartRecruiters", "smartrecruiters.com", ["Version1"]),
}
