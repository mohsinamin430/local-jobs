"""Pure, conservative qualification rules for Job Desk searches.

Network retrieval and persistence belong to the collector. Evidence passed here
must come from the actual job, employer, or recruiter page, never search snippets.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import html
import re
import unicodedata
from urllib.parse import parse_qsl, parse_qs, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

UTC = timezone.utc
LONDON = ZoneInfo("Europe/London")
ALIASES = {
    "AWS": ["AWS", "Amazon Web Services"],
    "Terraform": ["Terraform", "OpenTofu"],
    "Docker": ["Docker"],
    "Linux": ["Linux", "Ubuntu", "Red Hat", "RHEL"],
    "CI/CD": ["CI/CD", "CI CD", "continuous integration", "GitHub Actions"],
    "ECS": ["ECS", "Fargate", "Elastic Container Service"],
    "Networking": ["networking", "VPC", "DNS", "Route 53", "TCP/IP", "NAT", "ALB"],
    "Kubernetes": ["Kubernetes", "K8s", "EKS", "Elastic Kubernetes Service"],
    "Helm": ["Helm"],
    "Ansible": ["Ansible"],
    "Azure": ["Azure"],
    "GCP": ["GCP", "Google Cloud"],
    "Python": ["Python"],
    "PowerShell": ["PowerShell"],
}
CORE_GROUPS = ("AWS", "Terraform", "Docker", "Linux", "CI/CD", "ECS", "Networking")
GAP_GROUPS = ("Kubernetes", "Helm", "Ansible", "Azure", "GCP", "Python", "PowerShell")
ROLE_PATTERN = re.compile(
    r"\b(?:dev\s*ops|devsecops|site\s+reliability|sre|infrastructure|cloud|platform)\b"
    r"|\b(?:build|release)(?:\s*(?:&|and)\s*release)?\s+engineer\b", re.I
)
NON_ENGINEERING_ROLE = re.compile(
    r"\b(?:sales|account\s+(?:executive|manager|director)|product\s+(?:manager|management|owner)|"
    r"recruiter|recruitment\s+(?:consultant|manager)|talent\s+acquisition|marketing|"
    r"business\s+development|customer\s+success|technical\s+writer|developer\s+advocate)\b", re.I
)
NON_VACANCY = re.compile(
    r"\b(?:fellowship|traineeship|training\s+(?:course|programme|program)|bootcamp|"
    r"founder\s+(?:opportunity|fellowship|programme|program)|co[- ]founder|talent\s+(?:pool|community))\b", re.I
)


def _iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds")


def _plain(value):
    return re.sub(r"\s+", " ", html.unescape(str(value or ""))).strip()


def _norm(value):
    value = unicodedata.normalize("NFKC", _plain(value)).casefold()
    return re.sub(r"[^\w]+", " ", value).strip()


def _company_identity(value):
    return re.sub(r"\s+(?:limited|ltd|plc|inc|incorporated)$", "", _norm(value))


def _has(text, terms):
    return any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", text, re.I) for term in terms)


def _engineering_role(title):
    if not ROLE_PATTERN.search(title) or NON_ENGINEERING_ROLE.search(title):
        return False
    if re.search(r"\b(?:engineer(?:ing)?|sre)\b", title, re.I):
        return True
    # Functional engineering leadership titles may omit the word Engineer.
    functions = r"(?:dev\s*ops|devsecops|platform|cloud|infrastructure|site\s+reliability)"
    return bool(re.search(
        r"\b(?:head|lead|principal|staff|director)\s+(?:of\s+)?" + functions + r"\b"
        r"|\b" + functions + r"(?:\s+engineering)?\s+(?:lead|head|director)\b", title, re.I
    ))


def _relative(value, observed):
    value = _plain(value).lower()
    if "reposted" in value:
        return None
    if value in {"just now", "moments ago"}:
        return observed - timedelta(minutes=1), observed
    match = re.fullmatch(r"(\d+)\s*(second|minute|hour|day|week|month|year)s?\s+ago", value)
    if not match:
        return None
    unit = {"second": 1, "minute": 60, "hour": 3600, "day": 86400,
            "week": 604800, "month": 2678400, "year": 31622400}[match[2]]
    return observed - timedelta(seconds=(int(match[1]) + 1) * unit), observed - timedelta(seconds=int(match[1]) * unit)


def _date_interval(value):
    """Exact timestamps need a timezone. Dates cover a complete London day."""
    text = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        day = date.fromisoformat(text)
        start = datetime.combine(day, time.min, LONDON)
        end = datetime.combine(day + timedelta(days=1), time.min, LONDON)
        return start.astimezone(UTC), end.astimezone(UTC)
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(UTC), stamp.astimezone(UTC)


def _freshness(job, now, policy):
    cutoff = now - timedelta(hours=policy.get("max_age_hours", 24))
    accepted_fields = {"dateposted", "datepublished", "postedon", "publishedat", "postedat", "postingdate", "publicationdate", "firstpublishedat", "firstpublished", "releaseddate"}
    forbidden_sources = re.compile(r"snippet|search|crawl|updated|modified|verified.live", re.I)
    evidence, reasons = [], []
    exact_intervals = []
    employer_evidence = False
    for item in job.get("structured_dates", []) or []:
        field = re.sub(r"[^a-z]", "", str(item.get("field", "")).lower())
        source = str(item.get("source", ""))
        if field not in accepted_fields or forbidden_sources.search(source):
            continue
        # Structured date evidence without an exact source URL is not auditable.
        if not item.get("url"):
            continue
        try:
            interval = _date_interval(item.get("value"))
        except ValueError:
            interval = None
        if interval:
            entry = {"field": item["field"], "value": item.get("value"), "source": source,
                     "url": item["url"], "earliest": _iso(interval[0]), "latest": _iso(interval[1])}
            evidence.append(entry)
            exact_intervals.append(interval)
            employer_evidence |= bool(re.search(r"employer|recruiter|careers", source, re.I))
    detail_observed = datetime.fromisoformat(job.get('detail_observed_at',now.isoformat()))
    search_observed = datetime.fromisoformat(job.get('search_observed_at',now.isoformat()))
    relative = _relative(job.get("detail_posted_text", ""), detail_observed)
    if relative:
        evidence.append({"field": "detail_posted_text", "value": job["detail_posted_text"], "source": "exact_listing",
                         "url": job.get("url", ""), "earliest": _iso(relative[0]), "latest": _iso(relative[1])})
    intervals = exact_intervals + ([relative] if relative else [])
    if not intervals:
        return "unverified", ["No usable posting time from the exact listing or employer; search-only ages and update dates do not establish freshness"], evidence, ""
    # A stale actual source must not be overridden by a fresh LinkedIn relative label.
    if any(end < cutoff for start, end in intervals):
        return "reject", ["Actual listing/employer posting evidence is older than 24 hours; conflicting newer labels cannot establish a new vacancy"], evidence, ""
    # A date-only value may corroborate a precise timestamp/relative label when
    # the intervals overlap. It must not broaden a known six-hour age to a day.
    precise = [value for value in exact_intervals if value[0] == value[1]] + ([relative] if relative else [])
    bounds = precise or intervals
    if any(end > now + timedelta(minutes=1) for start, end in bounds):
        reasons.append("Posting evidence includes future or unresolved date-only time")
    if any(start < cutoff for start, end in bounds):
        reasons.append("The complete posting-time interval does not fit within the preceding 24 hours")
    # Differences within a displayed hour's rounding interval are acceptable.
    newest_start, oldest_end = max(v[0] for v in intervals), min(v[1] for v in intervals)
    if newest_start > oldest_end + timedelta(minutes=5):
        reasons.append("Actual listing and employer posting times conflict")
    search_relative = _relative(job.get("search_posted_text", ""), search_observed)
    if search_relative and not employer_evidence:
        if search_relative[1] + timedelta(minutes=5) < min(v[0] for v in intervals) or search_relative[0] > max(v[1] for v in intervals) + timedelta(minutes=5):
            reasons.append("Search and exact-listing posting ages conflict; employer-source resolution is required")
    if reasons:
        return "unverified", reasons, evidence, ""
    # Return exact publication time only when it was actually exposed as exact.
    exact = next((v for v in exact_intervals if v[0] == v[1]), None)
    return "retain", [], evidence, _iso(exact[0]) if exact else ""


def _work_model(job):
    evidence = []
    models = set()
    remote_ambiguous, remote_disallowed = False, False
    structured = job.get("listing_work_model")
    if isinstance(structured, dict):
        value = structured.get("value", "")
        source = structured.get("source", "actual_listing_structured")
        url = structured.get("url", job.get("url", ""))
    else:
        value, source, url = structured or "", "actual_listing_structured", job.get("url", "")
    normalized = _norm(value)
    mapped = {"remote": "remote", "fully remote": "remote", "telecommute": "remote", "telecommuting": "remote",
              "hybrid": "hybrid", "on site": "on-site", "onsite": "on-site", "on location": "on-site"}.get(normalized)
    if mapped:
        models.add(mapped)
        evidence.append({"source": source, "text": str(value), "url": url})
    for key, value in (job.get("criteria") or {}).items():
        if re.search(r"workplace|work arrangement|work model|work type|location type", key, re.I):
            normalized = _norm(value)
            mapped = {"remote": "remote", "hybrid": "hybrid", "on site": "on-site", "onsite": "on-site"}.get(normalized)
            if mapped:
                models.add(mapped)
                evidence.append({"source": f"listing_criteria:{key}", "text": value, "url": job.get("url", "")})
    description = job.get("description", "")
    employer_text = job.get("employer_listing_text", "")
    if employer_text:
        description += "\n" + employer_text
    title = job.get("title", "")
    patterns = {
        "hybrid": [r"\bhybrid\s+(?:working|work\b|role|position|model|arrangement|schedule)",
                   r"\b(?:work(?:ing)?|role|position|model|arrangement)\s*[:\-]?\s*hybrid\b",
                   r"\b[1234]\s+days?\s+(?:a |per |each )?week\s+(?:in|at)\s+(?:the |our )?office\b"],
        "remote": [r"\b(?:fully|100%|UK[- ]based|UK)\s+remote\b", r"\bremote\s+(?:work(?:ing)?|role|position|opportunity|job)\b",
                   r"\bwork\s+(?:fully |entirely )?from\s+home\b", r"\b(?:location|workplace)\s*:\s*remote\b"],
        "on-site": [r"\b(?:on[- ]site|onsite|office[- ]based)\s+(?:work(?:ing)?|role|position|job)\b",
                    r"\b(?:work(?:ing)?|role|position|location|workplace)\s*[:\-]?\s*(?:on[- ]site|onsite)\b",
                    r"\b(?:five|5)\s+days?\s+(?:a |per |each )?week\s+(?:in|at)\s+(?:the |our )?office\b"],
    }
    for model, expressions in patterns.items():
        for expression in expressions:
            match = re.search(expression, description, re.I)
            if not match:
                continue
            # "not a remote role" and "no remote working" are not remote evidence.
            prefix = description[max(0, match.start() - 22):match.start()]
            suffix = description[match.end():match.end() + 65]
            if model == "remote":
                if re.search(r"\b(?:not|no|isn't|is not)(?:\s+a)?\s*$", prefix, re.I) or re.match(r"\s*(?:work(?:ing)?\s+)?(?:(?:is|are|will be)\s+)?(?:not\b|unavailable\b|unsupported\b)", suffix, re.I):
                    remote_disallowed = True
                    continue
                # An optional/limited home-working benefit does not establish
                # that the vacancy is a remote job (it can still be on-site).
                sentence_start = max(description.rfind(".", 0, match.start()), description.rfind("\n", 0, match.start())) + 1
                sentence_end = re.search(r"[.\n]", description[match.end():])
                sentence_end = match.end() + sentence_end.start() if sentence_end else len(description)
                sentence = description[sentence_start:sentence_end]
                partial_home = re.search(r"\b(?:up to\s+)?(?:[1-4]|one|two|three|four)\s+days?\b|\b(?:optional|occasionally|some days|when possible)\b", sentence, re.I)
                optional_home = re.search(r"\b(?:may|can|option to|opportunity to|flexibility to)\s+(?:occasionally\s+)?work\s+from\s+home\b|\bremote\s+work(?:ing)?\s+(?:options?|opportunities|flexibility)\b", sentence, re.I)
                if partial_home or optional_home:
                    remote_ambiguous = True
                    continue
            models.add(model)
            from_employer = bool(employer_text) and match.start() > len(job.get("description", ""))
            evidence.append({"source": "employer_listing" if from_employer else "job_description", "text": match[0],
                             "url": job.get("employer_listing_url", job.get("url", "")) if from_employer else job.get("url", "")})
            break
    for model, expression in (("remote", r"\bremote\b"), ("hybrid", r"\bhybrid\b"), ("on-site", r"\bon[- ]?site\b")):
        match = re.search(expression, title, re.I)
        if match:
            if model == "hybrid" and re.match(r"\s+cloud\b", title[match.end():], re.I):
                continue
            if model == "remote" and re.match(r"\s+(?:sensing|desktop|access|control)\b", title[match.end():], re.I):
                continue
            models.add(model)
            evidence.append({"source": "job_title", "text": match[0], "url": job.get("url", "")})
    if "remote" in models and (remote_disallowed or (remote_ambiguous and "hybrid" not in models)):
        return "unknown", evidence, ["Remote label conflicts with prohibited, optional or limited home-working language in the actual listing"]
    if "hybrid" in models and models <= {"hybrid", "remote"}:
        return "hybrid", evidence, []
    if len(models) == 1:
        return next(iter(models)), evidence, []
    if len(models) > 1:
        return "unknown", evidence, ["Actual listing has conflicting work-arrangement evidence"]
    return "unknown", evidence, ["Actual listing does not establish remote, hybrid or on-site work; requested filters are not evidence"]


def _geography(job, work_model, onsite_city=""):
    location = _plain(job.get("location", ""))
    description = job.get("description", "") + "\n" + job.get("employer_listing_text", "")
    country = _plain(job.get("job_location_country", job.get("country", "")))
    if re.fullmatch(r'[A-Za-z]{2,3}',country) and country.lower() not in {'gb','gbr','uk'}:
        return 'reject', ['Listing country code is outside the UK: ' + country]
    combined_location = location + " " + country
    if re.search(r"\b(?:Alabama|Birmingham,?\s+AL(?:\s|$)|United States|USA|Canada|Germany|France|India|Australia|Netherlands|Poland|Spain|Portugal|Republic of Ireland)\b|(?<!Northern )\bIreland\b", combined_location, re.I):
        return "reject", ["Listing location is outside the UK"]
    uk_names = {"united kingdom", "uk", "u k", "england", "scotland", "wales", "northern ireland", "gb", "gbr", "great britain"}
    applicant_locations = job.get("applicant_locations") or []
    if isinstance(applicant_locations, (str, dict)):
        applicant_locations = [applicant_locations]
    countries = []
    for item in applicant_locations:
        if isinstance(item, dict):
            item = item.get("name", item.get("addressCountry", item.get("country", item.get("value", ""))))
            if isinstance(item, dict):
                item = item.get("name", "")
        if item:
            countries.append(_norm(item))
    global_names = {"worldwide", "global", "anywhere", "europe", "emea"}
    applicant_uk = any(value in uk_names or value in global_names for value in countries)
    if work_model == "remote" and countries and not applicant_uk:
        return "reject", ["Structured applicant-location restrictions do not include UK applicants: " + ", ".join(countries)]
    uk = bool(re.search(r"\b(?:United Kingdom|UK|U\.K\.|England|Scotland|Wales|Northern Ireland|GB|GBR)\b", combined_location, re.I)) or (work_model == "remote" and applicant_uk)
    onsite_match = bool(re.search(r"(?<!\w)" + re.escape(onsite_city) + r"(?!\w)", location, re.I)) and uk
    # Explicit eligibility restrictions override a UK location label.
    restricted = re.search(
        r"\b(?:must|need to|required to)\s+(?:be\s+)?(?:based|located|resident|reside|live)\s+(?:only\s+)?in\s+(?:the\s+)?"
        r"(?:United States|USA|US\b|Canada|Germany|France|India|Australia|Netherlands|Poland|Spain|Portugal|Republic of Ireland)"
        r"|\b(?:US|USA|United States|Canada|Germany|France|India|Australia|Netherlands|Poland|Spain|Portugal)[ -]only\b"
        r"|\bremote\s*[:(\-]?\s*(?:US|USA|United States|Canada|Germany|France|India|Australia)\s+only\b", job.get("title", "") + "\n" + description, re.I
    )
    if restricted:
        return "reject", ["Actual description restricts applicants to a non-UK country: " + restricted[0]]
    if not uk:
        return "unverified", ["Exact listing does not establish a UK location/UK applicant eligibility"]
    if work_model == "on-site" and onsite_city and not onsite_match:
        return "reject", [f"On-site role is outside {onsite_city}"]
    if work_model == "unknown":
        return "unverified", []
    return "retain", []


def _employer_quality(job, policy):
    company = _plain(job.get("company", ""))
    description = job.get("description", "")
    allowed = {_company_identity(name) for name in policy.get("allowed_agencies", [])}
    excluded = {_norm(name) for name in policy.get("excluded_employers", [])}
    name = _norm(company)
    if name in excluded or any(re.search(r"(?<!\w)" + re.escape(item) + r"(?!\w)", name) for item in excluded if item):
        return "excluded_aggregator", "reject", ["Employer is an excluded aggregator/reposter"]
    if _company_identity(company) in allowed:
        return "allowed_agency", "retain", []
    if not company or name in {"confidential", "confidential company", "private company", "stealth", "unknown"}:
        return "unverified", "unverified", ["Named real employer could not be established"]
    evidence = job.get("employer_evidence") or {}
    employer_text = _plain(evidence.get("description", ""))
    identity_matches = bool(evidence.get("name")) and _company_identity(evidence["name"]) == _company_identity(company)
    agency_sector = bool(identity_matches and evidence.get('url') and any(
        re.search(r'\b(?:recruitment|recruiting|staffing)\b', str(sector), re.I)
        for sector in evidence.get('sectors', [])))
    agency_name = re.search(r"\b(?:recruitment|recruiting|staffing|headhunt(?:ing|ers)?|talent\s+(?:solutions|acquisition|partners))\b", company, re.I)
    agency_page = re.search(r"\b(?:recruitment|recruiting|staffing)\s+(?:agency|agencies|firm|company|consultancy|specialists|group|business|services|solutions)\b|\b(?:we|our team)\s+(?:are|is)\s+(?:a\s+)?(?:specialist\s+)?(?:recruitment|staffing)\b|\b(?:specialist|technology|technical|professional|international)\s+(?:recruitment|recruiting|staffing)\b", employer_text, re.I)
    agency_listing = re.search(r"\b(?:our client (?:is (?:seeking|looking|hiring|recruiting)|seeks)|on behalf of (?:our|a) client|recruiting for (?:our|a) client|client is (?:seeking|looking))\b", description, re.I)
    if agency_name or agency_page or agency_listing or agency_sector:
        return "excluded_agency", "reject", ["Recruitment agency/client-representation evidence; agency is not an allowed exception"]
    if evidence.get("url") and identity_matches and len(employer_text) >= 60:
        return "direct_employer", "retain", []
    overrides = policy.get("employer_overrides") or {}
    override = overrides.get(company) or overrides.get(name)
    if isinstance(override, dict) and override.get("verified") is True and override.get("source_url") and override.get("type") == "direct_employer":
        return "direct_employer", "retain", []
    return "unverified", "unverified", ["No sufficient public company/about evidence to distinguish a direct employer from an agency or reposter"]


def _fit(job, policy, now):
    candidate = policy.get("candidate") or {}
    skills = candidate.get("skills", [])
    skill_text = "\n".join(str(skill) for skill in skills)
    content = job.get("title", "") + "\n" + job.get("description", "") + "\n" + job.get("employer_listing_text", "")
    if policy.get("roles"):
        matches = [skill for skill in skills if re.search(r"(?<!\w)" + re.escape(skill) + r"(?!\w)", content, re.I)]
        senior = bool(re.search(r"\b(?:senior|lead|principal|staff|head|director)\b", job.get("title", ""), re.I))
        rating = "Stretch" if senior or not matches else "Strong" if len(matches) >= 3 else "Possible"
        evidence = ", ".join(matches) if matches else "no explicit overlap found"
        return rating, f"Saved skills mentioned in the listing: {evidence}." + (" Senior scope requires review." if senior else "")
    supported = {name for name, aliases in ALIASES.items() if _has(skill_text, aliases)}
    mentioned = {name for name, aliases in ALIASES.items() if _has(content, aliases)}
    matches = [name for name in CORE_GROUPS if name in supported & mentioned]
    gaps = [name for name in GAP_GROUPS if name in mentioned - supported]
    since = (candidate.get("experience") or {}).get("devops_since")
    try:
        start = date.fromisoformat(since)
        years = max(0, (now.date() - start).days / 365.25)
    except (ValueError, TypeError):
        years = 0
    requirements = []
    for match in re.finditer(r"\b(\d{1,2})(?:\s*(?:-|–|to)\s*\d{1,2})?\s*\+?\s*years?\s+(?:of\s+)?(?:[\w/-]+\s+){0,4}experience\b", content, re.I):
        context = content[max(0, match.start() - 40):match.start()]
        if not re.search(r"\b(?:we have|company has|our company|our team has)\b", context, re.I):
            requirements.append(int(match[1]))
    minimum_years = max(requirements, default=None)
    senior = bool(re.search(r"\b(?:senior|sr\.?|lead|principal|staff|head|director|manager)\b", job.get("title", ""), re.I))
    high_experience = minimum_years is not None and minimum_years >= max(3, years + 1.5)
    overlap = "Matched demonstrated skills: " + (", ".join(matches) if matches else "limited overlap with the candidate's demonstrated core stack") + "."
    facts = []
    if senior:
        facts.append("The title carries senior/leadership scope")
    if minimum_years is not None:
        facts.append(f"The description requests {minimum_years}+ years of experience")
    if gaps:
        facts.append("Description tooling outside the supplied skills: " + ", ".join(gaps))
    if senior or high_experience or len(gaps) >= 3 or len(matches) < 2:
        rating = "Stretch"
    elif len(matches) >= 4 and {"AWS", "Terraform"}.issubset(matches) and not gaps and (minimum_years is None or minimum_years <= max(2, years + .75)):
        rating = "Strong"
        if minimum_years is None:
            facts.append("No minimum experience duration was stated; seniority requirements remain unconfirmed")
    else:
        rating = "Possible"
    return rating, " ".join([overlap, *[fact + "." for fact in facts]])


def evaluate_job(job: dict, policy: dict, now: datetime) -> dict:
    """Return an auditable qualification decision; never exclude only for fit."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    reasons, states = [], []
    title = _plain(job.get("title", ""))
    if job.get("listing_closed") is True or re.search(r"\b(?:no longer accepting applications|this job is (?:closed|no longer available)|this vacancy (?:has been filled|is closed))\b", job.get("description", "") + "\n" + job.get("employer_listing_text", ""), re.I):
        reasons.append("Actual listing/employer source confirms the vacancy is closed")
        states.append("reject")
    roles = policy.get("roles")
    if roles:
        from local_profile import role_pattern
        covered = any(re.search(role_pattern(role), title, re.I) for role in roles)
    else:
        covered = _engineering_role(title)
    if not covered:
        reasons.append("Title is outside the requested engineering role coverage" if not roles else "Title is outside the requested role coverage")
        states.append("reject")
    if NON_VACANCY.search(title) or re.search(r"\b(?:this is not a job|(?:this is|enrol in|enroll in|apply for our) (?:a |an )?(?:training course|unpaid fellowship|founder opportunity))\b", job.get("description", ""), re.I):
        reasons.append("Listing is a course, fellowship, traineeship, founder opportunity or generic talent advertisement")
        states.append("reject")
    if len(_plain(job.get("description", ""))) < 80:
        reasons.append("Insufficient actual job description to verify a genuine in-scope vacancy")
        states.append("unverified")
    freshness_state, freshness_reasons, freshness_evidence, posted_at = _freshness(job, now, policy)
    states.append(freshness_state)
    reasons.extend(freshness_reasons)
    work_model, work_evidence, work_reasons = _work_model(job)
    reasons.extend(work_reasons)
    if work_model == "unknown":
        states.append("unverified")
    if work_model != "unknown" and work_model not in policy.get("work_models", ("remote", "hybrid", "on-site")):
        states.append("reject")
        reasons.append("Work arrangement is outside the selected preferences")
    geo_state, geo_reasons = _geography(job, work_model, policy.get("onsite_city", ""))
    states.append(geo_state)
    reasons.extend(geo_reasons)
    quality, quality_state, quality_reasons = _employer_quality(job, policy)
    states.append(quality_state)
    reasons.extend(quality_reasons)
    fit, explanation = _fit(job, policy, now)
    decision = "reject" if "reject" in states else "unverified" if "unverified" in states else "retain"
    return {"decision": decision, "reasons": list(dict.fromkeys(reasons)), "work_model": work_model,
            "work_model_evidence": work_evidence, "fit": fit, "fit_explanation": explanation,
            "freshness_evidence": freshness_evidence, "posted_at": posted_at, "employer_quality": quality}


def canonical_url(url):
    """Normalize job URLs; preserve functional requisition/application parameters."""
    try:
        parsed = urlsplit(html.unescape(str(url or "")))
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"https", "http"} or not parsed.hostname or parsed.username:
        return ""
    host = parsed.hostname.lower()
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        if "/jobs/view/externalApply/" in parsed.path:
            target = parse_qs(parsed.query).get("url", [""])[0]
            return canonical_url(target) if target and target != url else ""
        match = re.search(r"/jobs/view/(?:[^/]*?-)?(\d{7,})(?:/|$)", parsed.path)
        if match:
            return f"https://www.linkedin.com/jobs/view/{match[1]}"
    tracking = {"trk", "trackingid", "refid", "position", "pagenum", "source", "sourceid", "lever-source", "gh_src", "ref", "referrer", "referral", "src"}
    params = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
              if key.lower() not in tracking and not key.lower().startswith("utm_")]
    netloc = host
    try:
        if parsed.port and parsed.port not in {80, 443}:
            netloc += f":{parsed.port}"
    except ValueError:
        return ""
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path.rstrip("/") or "/", urlencode(sorted(params)), ""))


def _job_specific_url(value):
    normalized = canonical_url(value)
    if not normalized:
        return ""
    parsed = urlsplit(normalized)
    generic = {"", "/", "/jobs", "/careers", "/career", "/vacancies", "/opportunities", "/openings"}
    if parsed.path.lower() in generic and not parsed.query:
        return ""
    return normalized


def dedup_keys(job):
    """Ordered ID, exact application URL, then company/title/location keys.

    Multi-city openings merge only by explicit shared job/application identity;
    city names are never dropped from the fallback key.
    """
    keys = []
    identifier = str(job.get("id", ""))
    if re.fullmatch(r"\d{7,}", identifier):
        keys.append("linkedin:" + identifier)
    elif re.fullmatch(r'(?:indeed|totaljobs|glassdoor|welcome|google):[A-Za-z0-9_-]+',identifier):
        keys.append('board:' + identifier)
    for field in ("canonical_apply_url", "preferred_apply_url", "apply_url", "canonical_url", "url"):
        normalized = _job_specific_url(job.get(field))
        if normalized and "url:" + normalized not in keys:
            keys.append("url:" + normalized)
    company, title, location = (_norm(job.get(field)) for field in ("company", "title", "location"))
    if company and title and location:
        keys.append("ctl:" + "|".join((company, title, location)))
    return keys
