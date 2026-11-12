"""Conservative hiring-contact discovery using public professional profiles.

Search results are leads only. A contact is retained only after reading their
public LinkedIn profile and finding a current employer and role there. Network
access, request limits and access restrictions are owned by the caller.
"""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import re
from rate_control import RateLimited
from urllib.parse import unquote, urlsplit


_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_RESTRICTED = re.compile(r"\b(?:blocked|forbidden|captcha|authwall|access denied|access restricted|rate limit)\b|HTTP\s*(?:401|403|429|999)", re.I)


class _Node:
    def __init__(self, tag="root", attrs=None):
        self.tag, self.attrs, self.children = tag, dict(attrs or []), []

    def all(self, *, cls=None, tag=None):
        result = []
        for child in self.children:
            if not isinstance(child, _Node):
                continue
            if (tag is None or child.tag == tag) and (cls is None or cls in (child.attrs.get("class") or "").split()):
                result.append(child)
            result.extend(child.all(cls=cls, tag=tag))
        return result

    def text(self, raw=False):
        parts = []
        for child in self.children:
            if isinstance(child, str):
                parts.append(child)
            elif raw or child.tag not in {"script", "style"}:
                parts.append(" " + child.text(raw=raw))
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def first_text(self, classes=(), tags=()):
        for cls in classes:
            nodes = self.all(cls=cls)
            if nodes and nodes[0].text():
                return nodes[0].text()
        for tag in tags:
            nodes = self.all(tag=tag)
            if nodes and nodes[0].text():
                return nodes[0].text()
        return ""


class _Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.root, self.stack = _Node(), []
        self.stack.append(self.root)
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        self.stack[-1].children.append(value)


def _company(value):
    """Exact normalized identity; never use substring company matching."""
    value = re.sub(r"[^\w\s]", " ", str(value or "").casefold().replace("&", " and "))
    tokens = value.split()
    while tokens and tokens[-1] in {"limited", "ltd", "inc", "incorporated", "llc", "plc", "corp", "corporation"}:
        tokens.pop()
    return " ".join(tokens)


def _profile_url(value):
    try:
        parsed = urlsplit(str(value or ""))
        host = (parsed.hostname or "").casefold()
        if parsed.scheme != "https" or parsed.username or parsed.port not in {None, 443}:
            return ""
        if host != "linkedin.com" and not host.endswith(".linkedin.com"):
            return ""
        match = re.fullmatch(r"/in/([^/]+)/?", parsed.path)
        if not match or not re.fullmatch(r"[A-Za-z0-9%_.~-]+", match[1]):
            return ""
        return "https://www.linkedin.com/in/" + match[1] + "/"
    except (ValueError, TypeError):
        return ""


def _walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _as_list(value):
    return value if isinstance(value, list) else [value]


def _string(value):
    return value.strip() if isinstance(value, str) else ""


def _uk_location(location):
    text = location.casefold()
    if re.search(r"\b(?:united states|usa|alabama|canada|australia)\b", text):
        return False
    return bool(re.search(r"\b(?:united kingdom|uk|gb|england|scotland|wales|northern ireland|birmingham|london)\b", text))


def _priority(title):
    text = title.casefold()
    if re.search(r"\b(?:talent acquisition|recruit(?:er|ing|ment)|resourcer)\b", text):
        return 1
    if re.search(r"\b(?:engineering|devops|platform|cloud|infrastructure|site reliability|sre)\b", text) and re.search(r"\b(?:manager|hiring manager)\b", text):
        return 2
    if re.search(r"\b(?:head|director|vp|vice president)\b", text) and re.search(r"\b(?:devops|platform|cloud|infrastructure|site reliability|sre)\b", text):
        return 3
    return 9


def _person_objects(root):
    for script in root.all(tag="script"):
        if (script.attrs.get("type") or "").casefold() != "application/ld+json":
            continue
        try:
            value = json.loads(script.text(raw=True))
        except (ValueError, TypeError):
            continue
        for item in _walk(value):
            types = _as_list(item.get("@type", ""))
            if any(str(t).rstrip("/").split("/")[-1] == "Person" for t in types):
                yield item


def _experience_records(root):
    """Read explicit current experience cards, never a headline or snippet."""
    cards = root.all(cls="experience-item")
    for card in root.all(cls="profile-section-card"):
        if card.all(cls="experience-item__duration") or card.all(cls="experience-item__date-range"):
            cards.append(card)
    for section in root.all(cls="experience") + root.all(cls="experience-section"):
        cards.extend(section.all(cls="profile-section-card"))
    seen = set()
    for card in cards:
        if id(card) in seen:
            continue
        seen.add(id(card))
        duration = card.first_text(classes=("date-range", "experience-item__duration", "experience-item__date-range"))
        if not duration or not re.search(r"\b(?:present|current)\b", duration, re.I):
            continue
        title = card.first_text(classes=("profile-section-card__title", "experience-item__title"), tags=("h3",))
        company = card.first_text(classes=("profile-section-card__subtitle", "experience-item__subtitle"), tags=("h4",))
        location = card.first_text(classes=("experience-item__location", "profile-section-card__location"))
        if title and company:
            yield {"title": title, "company": company, "location": location, "duration": duration}


def verify_profile(source, url, company, expected_name=""):
    """Return a verified public-profile record or an explicit non-verification."""
    url = _profile_url(url)
    if not url:
        return {"status": "unverified", "reason": "Not an HTTPS public LinkedIn /in/ profile URL"}
    root = _Document(source).root
    expected_company = _company(company)
    if not expected_company:
        return {"status": "unverified", "reason": "Vacancy employer identity is missing"}
    records = []
    displayed_name = root.first_text(classes=("top-card-layout__title", "topcard__title"), tags=("h1",))
    normalize_name = lambda value: re.sub(r"[^\w]", "", value.casefold())
    for person in _person_objects(root):
        name = _string(person.get("name")) or " ".join(filter(None, [_string(person.get("givenName")), _string(person.get("familyName"))]))
        identities = [_profile_url(value) for field in ("url", "@id", "sameAs") for value in _as_list(person.get(field, [])) if isinstance(value, str)]
        identities = [value for value in identities if value]
        if identities and url not in identities:
            continue
        if displayed_name and normalize_name(displayed_name) != normalize_name(name):
            continue
        title = _string(person.get("jobTitle"))
        if not title:
            continue
        location = person.get("address", "")
        if isinstance(location, dict):
            location = ", ".join(_string(location.get(key)) for key in ("addressLocality", "addressRegion", "addressCountry") if _string(location.get(key)))
        elif not isinstance(location, str):
            location = ""
        for employer in _as_list(person.get("worksFor", [])):
            org = employer.get("name", "") if isinstance(employer, dict) else employer
            if _company(org) != expected_company:
                continue
            # worksFor + jobTitle denotes current employment, but an explicitly
            # former/ex role cannot establish a current employee relationship.
            if re.search(r"\b(?:former|previous|previously|retired|ex[- ])\b", title, re.I):
                continue
            records.append({"name": name, "title": title, "company": _string(org), "location": location,
                            "basis": "Public profile Person.worksFor and Person.jobTitle", "quote": f"{name} | {title} | {org}"})
    name = displayed_name
    for experience in _experience_records(root):
        if _company(experience["company"]) == expected_company and not re.search(r"\b(?:former|retired|ex[- ])\b", experience["title"], re.I):
            records.append({"name": name, **experience, "basis": "Public profile experience explicitly marked Present/current",
                            "quote": f"{name} | {experience['title']} | {experience['company']} | {experience['duration']}"})
    for record in records:
        if not record.get("name") or not record.get("title"):
            continue
        if expected_name:
            normalize = lambda text: re.sub(r"[^\w]", "", text.casefold())
            if normalize(record["name"]) != normalize(expected_name):
                continue
        record.update({"status": "verified", "url": url, "uk_location": _uk_location(record.get("location", ""))})
        return record
    title = root.first_text(tags=("title",)).casefold()
    visible = root.text().casefold()
    blocked = bool(re.search(r"(?:sign in|login|authwall|security verification|checkpoint)", title)) or (len(visible) < 1500 and bool(re.search(r"(?:security verification|access denied|sign in to view|join linkedin to view)", visible)))
    return {"status": "blocked" if blocked else "unverified", "reason": "Public profile access is restricted" if blocked else "No matching name plus current employer and role could be established from the public profile"}


def _vacancy_linked(candidate, job):
    source = str(candidate.get("source_url", ""))
    if not source:
        return False
    job_id = str(job.get("id", ""))
    parsed_source = urlsplit(source)
    linkedin = parsed_source.hostname == "linkedin.com" or (parsed_source.hostname or "").endswith(".linkedin.com")
    if linkedin and parsed_source.scheme == "https" and job_id and re.search(r"(?<!\d)" + re.escape(job_id) + r"(?!\d)", parsed_source.path):
        return True
    strip = lambda value: urlsplit(str(value))._replace(query="", fragment="").geturl().rstrip("/")
    return strip(source) in {strip(job.get("url", "")), strip(job.get("apply_url", ""))} - {""}


def _limit(options, key, default, hard_max):
    value = options.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return min(value, hard_max)


def discover_and_verify_contact(job, fetch, search, options=None):
    """Attempt one relevant hiring contact, with bounded read-only discovery.

    `fetch(url)` must return public HTML; `search(query)` returns dictionaries
    containing actual discovered `url` values. Neither search titles nor snippets
    can verify a person's employment. The caller may cache both functions.
    """
    options = options or {}
    result = {"contact": "Not found", "contact_status": "not_found", "contact_evidence": [], "contact_attempts": [], "verified_email": "", "email_status": "not_researched"}
    max_searches = _limit(options, "maxSearchesPerCompany", 2, 2)
    max_profiles = _limit(options, "maxProfilesPerJob", 3, 3)
    if not options.get("enabled", True) or not max_profiles:
        result["contact_status"] = "incomplete"
        result["contact_attempts"].append({"stage": "configuration", "status": "incomplete", "reason": "Contact research disabled or profile budget is zero"})
        return result
    company = str(job.get("company", "")).strip()
    if not company:
        result["contact_status"] = "incomplete"
        result["contact_attempts"].append({"stage": "identity", "status": "incomplete", "reason": "Vacancy employer missing"})
        return result
    agencies = {_company(value) for value in options.get("allowed_agencies", [])}
    is_agency = _company(company) in agencies or job.get("employer_quality") in {"allowed_agency", "recruitment_agency"}
    attempts, verified, seen, used = result["contact_attempts"], [], set(), 0
    blocked, incomplete = False, False

    def attempt(candidate, named):
        nonlocal used, blocked, incomplete
        url = _profile_url(candidate.get("url", ""))
        if not url or url in seen:
            return
        if used >= max_profiles:
            incomplete = True
            return
        seen.add(url)
        if is_agency and (not named or not _vacancy_linked(candidate, job)):
            attempts.append({"stage": "profile", "url": url, "status": "unverified", "reason": "Agency consultant is not explicitly linked to this vacancy"})
            return
        used += 1
        try:
            source = fetch(url)
        except RateLimited:
            raise
        except Exception as error:
            status = "blocked" if getattr(error, "code", None) in {401, 403, 429, 999} or _RESTRICTED.search(str(error)) else "incomplete"
            attempts.append({"stage": "profile", "url": url, "status": status, "reason": str(error)})
            blocked |= status == "blocked"
            incomplete |= status == "incomplete"
            return
        record = verify_profile(source, url, company, candidate.get("name", "") if named else "")
        if record["status"] == "verified" and not named and _priority(record["title"]) == 9:
            record = {"status": "unverified", "reason": "Current role is not demonstrably relevant to hiring for this vacancy"}
        attempts.append({"stage": "profile", "url": url, "status": record["status"], "reason": record.get("reason", record.get("basis", ""))})
        if record["status"] == "verified":
            record["priority"] = 0 if named else _priority(record["title"])
            record["named_on_vacancy"] = bool(named)
            record["lead_source_url"] = candidate.get("source_url", "")
            verified.append(record)
        elif record["status"] == "blocked":
            blocked = True

    for candidate in job.get("named_contact_candidates", []):
        attempt(candidate, named=True)
        if verified:
            break
    if is_agency and not verified:
        incomplete = True
        attempts.append({"stage": "vacancy_leads", "status": "incomplete", "reason": "No current consultant explicitly associated with this vacancy could be verified from the supplied public-vacancy leads"})
    if not verified and not is_agency:
        queries = [
            f'site:linkedin.com/in/ "{company}" "Talent Acquisition" OR "Technical Recruiter" UK',
            f'site:linkedin.com/in/ "{company}" "Engineering Manager" OR "Head of DevOps" OR "Head of Platform" OR "Head of Cloud" OR "Head of Infrastructure" UK',
        ]
        if not max_searches:
            incomplete = True
        for query in queries[:max_searches]:
            if used >= max_profiles:
                incomplete = True
                break
            try:
                candidates = search(query)
                if not isinstance(candidates, list):
                    raise ValueError("Search returned no structured list; discovery could not be checked")
            except RateLimited:
                raise
            except Exception as error:
                status = "blocked" if getattr(error, "code", None) in {401, 403, 429, 999} or _RESTRICTED.search(str(error)) else "incomplete"
                attempts.append({"stage": "search", "query": query, "status": status, "reason": str(error)})
                blocked |= status == "blocked"
                incomplete |= status == "incomplete"
                continue
            attempts.append({"stage": "search", "query": query, "status": "completed", "leads": len(candidates)})
            for candidate in candidates:
                if isinstance(candidate, dict):
                    attempt(candidate, named=False)
            if verified:
                break
    if verified:
        chosen = min(verified, key=lambda person: (person["priority"], not person["uk_location"]))
        result["contact"] = f"{chosen['name']} | {chosen['title']} | {chosen['url']}"
        result["contact_status"] = "verified"
        result["contact_evidence"] = [{key: value for key, value in chosen.items() if key not in {"status", "priority"}}]
        result["contact_evidence"][0]["checked_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    elif blocked:
        result["contact_status"] = "blocked"
    elif incomplete:
        result["contact_status"] = "incomplete"
    return result
