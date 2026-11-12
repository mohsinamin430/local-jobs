"""Resolve a submitted vacancy URL from exact public listing evidence."""
from datetime import datetime
import html
import json
import re
from urllib.parse import urlsplit

from collector import Document, Node, parse_detail
from job_feeds import FEEDS
from personal_rules import canonical_url
from public_research import PageParser, ResearchBlocked, objects


class VacancyUnavailable(ValueError):
    pass


def posting_fields(posting):
    company = posting.get("hiringOrganization") or {}
    if not isinstance(company, dict):
        company = {}
    locations = posting.get("jobLocation") or []
    locations = locations if isinstance(locations, list) else [locations]
    labels = []
    for location in locations:
        address = location.get("address") if isinstance(location, dict) else None
        if not isinstance(address, dict):
            continue
        parts = [address.get("addressLocality") or address.get("streetAddress"), address.get("addressRegion"), address.get("addressCountry")]
        parts = [part.get("name") if isinstance(part, dict) else part for part in parts]
        labels.append(", ".join(dict.fromkeys(str(part) for part in parts if part)))
    return {
        "title": str(posting.get("title") or "").strip(),
        "company": str(company.get("name") or "").strip(),
        "location": "; ".join(labels),
        "description": PageParser(html.unescape(str(posting.get("description") or ""))).text,
        "workModel": "remote" if posting.get("jobLocationType") == "TELECOMMUTE" else "",
        "_posting": posting,
    }


def microdata_postings(body):
    """Read schema.org JobPosting HTML scopes, including SuccessFactors fields."""
    def properties(node, name):
        found = []
        for child in node.children:
            if not isinstance(child, Node):
                continue
            if name in (child.attrs.get("itemprop") or "").split():
                found.append(child)
            # Nested scopes own their properties, not the parent vacancy.
            if "itemscope" not in child.attrs:
                found.extend(properties(child, name))
        return found

    def value(node):
        for attribute in ("content", "datetime", "href"):
            if node.attrs.get(attribute):
                return node.attrs[attribute].strip()
        return node.text().strip()

    def first_value(node, name):
        matches = properties(node, name)
        return value(matches[0]) if len(matches) == 1 else ""

    result = []
    for scope in Document(body).root.all():
        if not any(item.rstrip("/") in {"https://schema.org/JobPosting", "http://schema.org/JobPosting"}
                   for item in (scope.attrs.get("itemtype") or "").split()):
            continue
        title = first_value(scope, "title")
        employer = first_value(scope, "hiringOrganization")
        organizations = properties(scope, "hiringOrganization")
        if len(organizations) == 1 and "itemscope" in organizations[0].attrs:
            employer = first_value(organizations[0], "name")
        descriptions = properties(scope, "description")
        if not title or not employer or len(descriptions) != 1:
            continue
        posting = {
            "@type": "JobPosting", "title": title, "hiringOrganization": {"name": employer},
            "description": html.escape(value(descriptions[0])).replace("\n", "<br>"),
        }
        for field in ("url", "datePosted", "validThrough", "employmentType", "jobLocationType", "identifier"):
            field_value = first_value(scope, field)
            if field_value:
                posting[field] = field_value
        # SuccessFactors midnight dates represent the displayed calendar date.
        posted = posting.get("datePosted", "")
        if posted:
            try:
                posting["datePosted"] = datetime.strptime(posted, "%a %b %d %H:%M:%S UTC %Y").date().isoformat()
            except ValueError:
                pass

        locations = []
        for place in properties(scope, "jobLocation"):
            for address in properties(place, "address"):
                fields = {}
                for field in ("streetAddress", "addressLocality", "addressRegion", "addressCountry"):
                    content = first_value(address, field)
                    if content:
                        fields[field] = content
                if fields:
                    locations.append({"@type": "Place", "address": fields})

        for token in scope.all("joblayouttoken"):
            label_node = token.first("joblayouttoken-label")
            label = re.sub(r"[^a-z]", "", label_node.text().casefold())
            content = token.text().removeprefix(label_node.text()).strip()
            field = {"refcode": "identifier", "jobreference": "identifier", "contracttype": "employmentType"}.get(label)
            if field and content:
                posting.setdefault(field, content)
            if label == "location" and content:
                # Prefer the visible list when it gives fuller location labels.
                locations = [{"@type": "Place", "address": {"streetAddress": line.strip()}}
                             for line in content.splitlines() if line.strip()]
        if locations:
            posting["jobLocation"] = locations
        result.append(posting)
    return result


def exact_posting(body, url):
    page = PageParser(body)
    postings = list(objects(page.json_blocks, "JobPosting")) or microdata_postings(body)
    identity = canonical_url(url)
    exact = [posting for posting in postings if posting.get("url") and canonical_url(posting["url"]) == identity]
    if len(exact) == 1:
        return posting_fields(exact[0])
    # A single posting must agree with the page heading. Multiple recommendations are ambiguous.
    if len(postings) == 1 and urlsplit(url).path.rstrip("/").lower() not in {
            "", "/jobs", "/careers", "/career", "/vacancies", "/search"}:
        title = str(postings[0].get("title") or "").strip().casefold()
        headings = {heading.strip().casefold() for heading in page.h1}
        parts = urlsplit(url)
        if parts.hostname in {"remoteok.com", "www.remoteok.com"} and parts.path.startswith("/remote-jobs/"):
            headings.update(heading.removeprefix("remote ") for heading in tuple(headings))
        if title and title in headings:
            return posting_fields(postings[0])
    raise VacancyUnavailable("The page did not identify one exact vacancy.")


def current_vacancies_fields(body, url, job):
    """Supplement an exact Current Vacancies posting from its visible advert."""
    parts = urlsplit(url)
    match = re.fullmatch(r"/Jobs/Advert/(\d+)/?", parts.path, re.I)
    host = parts.hostname or ""
    if not match or not (host == "current-vacancies.com" or host.endswith(".current-vacancies.com")):
        return job
    identifier = match[1]
    root = Document(body).root
    advert_ids = {node.attrs.get("value") for node in root.all(tag="input")
                  if node.attrs.get("id") == "PubInfoID"}
    if not advert_ids:
        return job
    if advert_ids != {identifier}:
        raise VacancyUnavailable("The advert did not confirm the requested vacancy ID.")

    sections = {}
    for parent in root.all():
        children = [child for child in parent.children if isinstance(child, Node) and child.tag == "div"]
        for heading, content in zip(children, children[1:]):
            section = heading.attrs.get("id")
            if section in {"GlobalContent_HeaderTitle1", "GlobalContent_HeaderTitle2"}:
                sections.setdefault(section, []).append(content.text())
    descriptions = sections.get("GlobalContent_HeaderTitle1", [])
    if len(job.get("description", "")) < 100 and len(descriptions) == 1:
        job["description"] = descriptions[0]

    company_sections = sections.get("GlobalContent_HeaderTitle2", [])
    if len(company_sections) == 1:
        name = re.match(r"^([\w&.'-]+(?: [\w&.'-]+){0,3}) (?:is|are)\b", company_sections[0])
        if name:
            brand = re.sub(r"[^a-z0-9]", "", name[1].casefold())
            for link in root.all("navbar-brand", tag="a"):
                homepage = urlsplit(link.attrs.get("href") or "")
                site = (homepage.hostname or "").removeprefix("www.").split(".")[0]
                if homepage.scheme == "https" and brand and brand == site:
                    job["company"] = name[1]
                    break

    for field in root.first("advertDetail").all("theMerge"):
        lines = field.text().splitlines()
        if len(lines) != 2:
            continue
        label, value = (line.strip() for line in lines)
        if label.rstrip(":").casefold() == "location":
            job["location"] = value
        if label.rstrip(":").casefold() == "salary" and "£" in value:
            job["salary"] = value

    posting = {**job.get("_posting", {}), "identifier": identifier}
    employment = re.search(r"\bThis is (?:an? )?([a-z ,/-]{3,70}) vacancy\b", root.text(), re.I)
    if employment:
        posting["employmentType"] = employment[1].strip()
    job["_posting"] = posting
    return job


def linked_in(url, research):
    parts = urlsplit(url)
    match = re.search(r"/jobs/view/(?:[^/?]*-)?(\d+)/?$", parts.path)
    if not match or not (parts.hostname == "linkedin.com" or (parts.hostname or "").endswith(".linkedin.com")):
        return None
    endpoint = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/" + match[1]
    body = research.get(endpoint)
    root = Document(body).root
    details = parse_detail(body)
    details.update(
        title=root.first("top-card-layout__title").text(),
        company=root.first("topcard__org-name-link").text(),
        location=root.first("topcard__flavor--bullet").text(),
        url="https://www.linkedin.com/jobs/view/" + match[1] + "/",
        _evidence_url=endpoint,
    )
    return details


def employer_api(url, research):
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"}:
        match = re.fullmatch(r"/([A-Za-z0-9_-]+)/jobs/(\d+)/?", parts.path)
        if match:
            board, identifier = match.groups()
            endpoint = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{identifier}"
            row = json.loads(research.get(endpoint))
            if str(row.get("id")) != identifier:
                raise VacancyUnavailable("The Greenhouse response did not match this vacancy.")
            job = FEEDS["greenhouse"].greenhouse_job(row, board, endpoint)
            job.update(_evidence_url=endpoint, _posting={"datePosted": row.get("first_published"),
                                                       "validThrough": row.get("application_deadline"),
                                                       "identifier": row.get("requisition_id")})
            return job
    if host in {"jobs.smartrecruiters.com", "www.smartrecruiters.com"}:
        match = re.fullmatch(r"/([A-Za-z0-9_-]+)/(\d+)(?:-[^/]*)?/?", parts.path)
        if match:
            board, identifier = match.groups()
            endpoint = f"https://api.smartrecruiters.com/v1/companies/{board}/postings/{identifier}"
            row = json.loads(research.get(endpoint))
            if str(row.get("id")) != identifier:
                raise VacancyUnavailable("The SmartRecruiters response did not match this vacancy.")
            feed = FEEDS["smartrecruiters"]
            job = feed.smartrecruiters_job(row, board, endpoint)
            feed.detail(job, {"board": board}, research.get)
            workplace = job.get("listing_work_model") or {}
            job.update(workModel=workplace.get("value", ""), _evidence_url=endpoint,
                       _posting={"datePublished": row.get("releasedDate"), "identifier": row.get("refNumber"),
                                 "employmentType": (row.get("typeOfEmployment") or {}).get("label")})
            return job
    return None


def resolve_vacancy(url, research):
    """Return discovered fields only when a public source establishes the exact job."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in {None, 443}:
        raise VacancyUnavailable("Enter a public HTTPS vacancy URL.")
    # PublicResearch validates destinations and redirects before making requests.
    try:
        resolved = employer_api(url, research) or linked_in(url, research)
        if resolved is None:
            body = research.get(url)
            final_url = next((record.get("final_url") for record in reversed(research.records)
                              if record.get("url") == url and record.get("final_url")), url)
            if final_url != url:
                resolved = employer_api(final_url, research) or linked_in(final_url, research)
            if resolved is None:
                try:
                    resolved = exact_posting(body, final_url)
                except VacancyUnavailable:
                    final_host = urlsplit(final_url).hostname or ""
                    if final_host == "totaljobs.com" or final_host.endswith(".totaljobs.com"):
                        from job_boards import parse_detail as parse_board_detail
                        resolved = parse_board_detail("totaljobs", body, {"url": final_url})
                    else:
                        raise
            resolved = current_vacancies_fields(body, final_url, resolved)
            resolved.setdefault("url", final_url)
            resolved["_evidence_url"] = final_url
        if not resolved.get("title") or not resolved.get("company") or len(resolved.get("description", "")) < 100:
            raise VacancyUnavailable("The exact title, employer or full description could not be confirmed.")
        if max(len(resolved["title"]), len(resolved["company"])) > 180:
            raise VacancyUnavailable("The page did not expose a usable vacancy title and employer.")
        return resolved
    except (ValueError, KeyError, TypeError, RuntimeError, OSError) as error:
        raise VacancyUnavailable(
            "The exact vacancy could not be retrieved from this URL. Open the text fallback and paste "
            "the advert, job title and employer, then evaluate again."
        ) from error
