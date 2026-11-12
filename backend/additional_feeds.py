"""Read-only discovery feeds with independent exact-advert verification."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
import os
import re
from urllib.parse import urlencode, urlsplit
import xml.etree.ElementTree as ET

from job_feeds import date_evidence, https_url, text
from personal_rules import _company_identity, _date_interval, _norm

from qais_feed import QaisFeed

WORKPLACE = {"hybrid": "hybrid", "remote": "remote", "onsite": "on-site", "on-site": "on-site"}


def exact_advert(job, research):
    from rate_control import RateLimited
    from vacancy_resolver import VacancyUnavailable, resolve_vacancy

    def resolve(url):
        try:
            return resolve_vacancy(url, research)
        except VacancyUnavailable as error:
            cause = error.__cause__
            if isinstance(cause, RateLimited):
                raise cause
            raise ValueError(str(cause or error)) from None

    resolved = resolve(job["url"])
    if _norm(job["title"]) != _norm(resolved["title"]):
        raise ValueError("The exact advert title did not match the discovered listing")
    if job.get("company") and _company_identity(job["company"]) != _company_identity(resolved["company"]):
        raise ValueError("The exact advert employer did not match the discovered listing")

    posting = resolved.get("_posting", {})
    evidence_url = resolved["_evidence_url"]
    job["source_urls"] = list(dict.fromkeys([job["url"], resolved["url"], evidence_url]))
    job.update(title=resolved["title"], company=resolved["company"], description=resolved["description"],
               url=resolved["url"], location=resolved.get("location") or job.get("location", ""),
               structured_dates=[item for field in ("datePosted", "datePublished", "postedOn", "firstPublished")
                                 for item in date_evidence(field, posting.get(field), "exact_listing", evidence_url)])
    if resolved.get("workModel"):
        job["listing_work_model"] = {"value": resolved["workModel"], "source": "exact_listing", "url": evidence_url}
    if posting.get("applicantLocationRequirements"):
        job["applicant_locations"] = posting["applicantLocationRequirements"]
    organization = posting.get("hiringOrganization")
    if isinstance(organization, dict) and _company_identity(organization.get("name")) == _company_identity(job["company"]):
        website = https_url(organization.get("sameAs")) or https_url(organization.get("url"))
        if website:
            job["company_url"] = website
        if isinstance(organization.get("description"), str):
            job["employer_evidence"] = {"name": job["company"], "description": text(organization["description"]),
                                        "url": evidence_url}
    from application_assistant import annual_salary, format_salary
    salary = annual_salary(posting)
    if salary:
        job["salary"] = format_salary(salary)
    closing = posting.get("validThrough")
    if isinstance(closing, str):
        try:
            interval = _date_interval(closing)
        except ValueError:
            interval = None
        if interval and interval[1] < datetime.now(timezone.utc):
            job["listing_closed"] = True


class EmployerWatchlist:

    def __init__(self, source, label, domain, default_boards):
        self.source, self.label, self.domain = source, label, domain
        self.default_boards = default_boards

    def plans(self, searches, config):
        env = os.environ.get(self.source.upper() + "_BOARDS", "")
        defaults = [value.strip() for value in env.split(",") if value.strip()] or self.default_boards
        boards = config.get("employerFeeds", {}).get(self.source, defaults)
        pattern = r"(?:eu:)?[A-Za-z0-9_-]{1,100}" if self.source == "lever" else r"[A-Za-z0-9_-]{1,100}"
        if not isinstance(boards, list) or not boards or any(
                not isinstance(board, str) or not re.fullmatch(pattern, board) for board in boards):
            raise ValueError(f"Invalid {self.label} employer board IDs")
        for board in dict.fromkeys(boards):
            yield {"source": self.source, "board": board, "params": {"keywords": board}, "url": self.page_url(board, 0)}

    def detail(self, job, search, research):
        exact_advert(job, research)


class DirectBoardFeed(EmployerWatchlist):
    page_size = 100

    def page_url(self, board, offset):
        if self.source == "ashby":
            return f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
        host = "api.eu.lever.co" if board.startswith("eu:") else "api.lever.co"
        return f"https://{host}/v0/postings/{board.removeprefix('eu:')}?" + urlencode(
            {"mode": "json", "skip": offset, "limit": self.page_size})

    def page(self, search, offset, get):
        board = search["board"]
        url = self.page_url(board, offset)
        body = json.loads(get(url))
        if self.source == "lever":
            rows, more = body, len(body) == self.page_size
        else:
            rows, more = body["jobs"], False
        cards = []
        for row in rows:
            if self.source == "lever":
                title = row["text"]
                location = (row.get("categories") or {}).get("location", "")
                country = row.get("country", "")
                listing = https_url(row.get("hostedUrl"))
                expected_host = "jobs.eu.lever.co" if board.startswith("eu:") else "jobs.lever.co"
            else:
                if row.get("isListed") is False:
                    continue
                title = row["title"]
                location = row.get("location", "")
                country = ((row.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")
                listing = https_url(row.get("jobUrl"))
                expected_host = "jobs.ashbyhq.com"
            parts = urlsplit(listing)
            path = parts.path.strip("/").split("/")
            if (parts.hostname != expected_host or len(path) != 2
                    or path[0].casefold() != board.removeprefix("eu:").casefold()
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", path[1])):
                continue
            model = WORKPLACE.get(str(row.get("workplaceType", "")).casefold(), "")
            cards.append({"id": f"{self.source}:{board}:{path[1]}", "title": title, "company": "",
                          "url": listing, "location": location or "", "job_location_country": country or "",
                          "listing_work_model": {"value": model, "source": f"{self.source}_employer_feed", "url": listing}})
        # Lever creation time and Ashby last-published time are not original posting evidence.
        return cards, len(rows), more

class JobicyFeed:
    label, domain = "Jobicy", "jobicy.com"

    def plans(self, searches, config):
        yield {"source": "jobicy", "params": {"keywords": "UK-eligible remote roles", "f_WT": "2"},
               "url": self.page_url(0), "max_age_hours": config.get("personalPolicy", {}).get("max_age_hours", 24)}

    def page_url(self, cursor):
        query = {"count": 100, "geo": "uk"}
        if cursor:
            query["cursor"] = cursor
        return "https://jobicy.com/api/jobs?" + urlencode(query)

    def page(self, search, cursor, get):
        body = json.loads(get(self.page_url(cursor)))
        rows = body["jobs"]
        next_cursor = body.get("nextCursor")
        if next_cursor and (not isinstance(next_cursor, str) or next_cursor == cursor):
            raise ValueError("Jobicy returned an invalid or repeated pagination cursor")
        cards = []
        for row in rows:
            listing = https_url(row.get("url"))
            if urlsplit(listing).hostname not in {"jobicy.com", "www.jobicy.com"}:
                continue
            eligible = [location.strip() for location in (row.get("jobGeo") or "").split(",") if location.strip()]
            cards.append({"id": f"jobicy:{row['id']}", "title": row["jobTitle"], "company": row.get("companyName") or "",
                          "url": listing, "location": "", "feed_search_date": row.get("pubDate"),
                          "applicant_locations": eligible,
                          "listing_work_model": {"value": "remote", "source": "jobicy_listing_feed", "url": listing}})
        if next_cursor and rows:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=search["max_age_hours"])
            try:
                oldest = _date_interval(rows[-1].get("pubDate"))
            except ValueError:
                oldest = None
            if oldest and oldest[1] < cutoff:
                next_cursor = None
        return cards, len(rows), next_cursor

    def detail(self, job, search, research):
        exact_advert(job, research)



def employer_location(locations):
    if not locations:
        return "", ""
    selected = next((row for row in locations if _norm(row.get("countryCode") or row.get("country"))
                     in {"gb", "gbr", "uk", "united kingdom"}), locations[0])
    city, country = selected.get("city"), selected.get("country")
    label = ", ".join(dict.fromkeys(value for value in (city, country) if value))
    return label, selected.get("countryCode") or country or ""


class TeamtailorFeed(EmployerWatchlist):
    page_size = 100
    locations_namespace = "{https://teamtailor.com/locations}"

    def page_url(self, board, offset):
        url = f"https://{board}.teamtailor.com/jobs.rss"
        return url + "?" + urlencode({"offset": offset}) if offset else url

    def page(self, search, offset, get):
        board = search["board"]
        url = self.page_url(board, offset)
        try:
            channel = ET.fromstring(get(url)).find("channel")
        except ET.ParseError as error:
            raise ValueError("Teamtailor did not return a readable RSS feed") from error
        if channel is None or not channel.findtext("title"):
            raise ValueError("Teamtailor RSS did not identify the employer board")
        employer = channel.findtext("title").strip()
        about = text(channel.findtext("description"))
        rows = channel.findall("item")
        cards = []
        ns = self.locations_namespace
        for row in rows:
            listing = https_url(row.findtext("link"))
            parts = urlsplit(listing)
            job_id = re.fullmatch(r"/jobs/(\d+)(?:-[^/]+)?/?", parts.path)
            title = row.findtext("title", "").strip()
            if parts.hostname != f"{board.lower()}.teamtailor.com" or not job_id or not title:
                continue
            try:
                stamp = parsedate_to_datetime(row.findtext("pubDate", "")).isoformat()
            except (TypeError, ValueError):
                stamp = ""
            locations = [{"city": location.findtext(ns + "city") or location.findtext(ns + "name"),
                          "country": location.findtext(ns + "country")}
                         for location in row.findall(ns + "locations/" + ns + "location")]
            label, country = employer_location(locations)
            workplace = {"fully": "remote", "hybrid": "hybrid", "none": "on-site"}.get(
                row.findtext("remoteStatus", "").casefold(), "")
            cards.append({"id": f"teamtailor:{board}:{job_id[1]}", "title": title, "company": employer,
                          "url": listing, "location": label, "job_location_country": country,
                          "feed_search_date": stamp,
                          "employer_evidence": {"name": employer, "description": about, "url": url},
                          "listing_work_model": {"value": workplace, "source": "teamtailor_employer_feed", "url": listing}})
        return cards, len(rows), len(rows) == self.page_size


class WorkableFeed(EmployerWatchlist):
    def page_url(self, board, offset):
        return f"https://www.workable.com/api/accounts/{board}?details=true"

    def page(self, search, offset, get):
        board = search["board"]
        url = self.page_url(board, offset)
        body = json.loads(get(url))
        rows, employer = body["jobs"], body["name"]
        about = text(body.get("description"))
        cards = []
        for row in rows:
            listing = https_url(row.get("url"))
            parts = urlsplit(listing)
            job_id = re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)?j/([A-Za-z0-9]+)/?", parts.path)
            if (parts.hostname != "apply.workable.com" or not job_id
                    or job_id[1].casefold() != str(row.get("shortcode", "")).casefold()):
                continue
            locations = [location for location in row.get("locations", []) if not location.get("hidden")]
            label, country = employer_location(locations)
            cards.append({"id": f"workable:{board}:{job_id[1]}", "title": row["title"], "company": employer,
                          "url": listing, "location": label, "job_location_country": country,
                          "feed_search_date": row.get("published_on"),
                          "employer_evidence": {"name": employer, "description": about, "url": url}})
        return cards, len(rows), False


class RemoteOKFeed:
    label, domain = "Remote OK", "remoteok.com"

    def plans(self, searches, config):
        yield {"source": "remoteok", "params": {"keywords": "DevOps", "f_WT": "2"},
               "url": "https://remoteok.com/api?tag=devops"}

    def page(self, search, offset, get):
        body = json.loads(get(search["url"]))
        if not isinstance(body, list):
            raise ValueError("Remote OK returned an invalid feed")
        rows = [row for row in body if row.get("id")]
        cards = []
        for row in rows:
            listing = https_url(row.get("url"))
            parts = urlsplit(listing)
            job_id = str(row["id"])
            if (not job_id.isdigit() or parts.hostname not in {"remoteok.com", "www.remoteok.com"}
                    or not re.fullmatch(r"/remote-jobs/[^/]+-" + job_id + r"/?", parts.path)):
                continue
            eligible = [part.strip() for part in re.split(r"[,;]", row.get("location") or "") if part.strip()]
            cards.append({"id": f"remoteok:{job_id}", "title": row["position"], "company": row.get("company") or "",
                          "url": listing, "location": "", "feed_search_date": row.get("date"),
                          "applicant_locations": eligible,
                          "listing_work_model": {"value": "remote", "source": "remoteok_listing_feed", "url": listing}})
        return cards, len(rows), False

    def detail(self, job, search, research):
        exact_advert(job, research)


FEEDS = {
    "jobsbyqais": QaisFeed(),
    "jobicy": JobicyFeed(),
    "lever": DirectBoardFeed("lever", "Lever", "lever.co", ["zopa"]),
    "ashby": DirectBoardFeed("ashby", "Ashby", "ashbyhq.com", ["incident"]),
    "teamtailor": TeamtailorFeed("teamtailor", "Teamtailor", "teamtailor.com", ["ensek", "clearroute"]),
    "workable": WorkableFeed("workable", "Workable", "workable.com", ["longshot-systems-ltd"]),
    "remoteok": RemoteOKFeed(),
}
