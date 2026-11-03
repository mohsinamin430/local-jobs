"""Bounded, read-only public-page research. No paid API keys or browser cookies."""
from __future__ import annotations

from html.parser import HTMLParser
import ipaddress
import json
import os
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
import xml.etree.ElementTree as ET
from rate_control import RateLimited, linkedin


class ResearchBlocked(RuntimeError):
    pass


class ResearchLimit(RuntimeError):
    pass


def validate_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
        raise ValueError("Only public HTTPS URLs on port 443 are supported")
    if parsed.hostname.lower() in {"localhost", "localhost.localdomain", "metadata.google.internal"}:
        raise ValueError("Local/metadata hosts are not public research sources")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise URLError(f"DNS lookup failed: {parsed.hostname}") from error
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Private/reserved network destinations are not allowed")
    return urlunsplit(("https", parsed.netloc, parsed.path or "/", parsed.query, ""))


class SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, validate_url(newurl))


class PageParser(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.json_blocks, self.parts, self.meta, self.links = [], [], {}, []
        self.h1, self.in_h1 = [], False
        self.script = None
        self.ignored = 0
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h1":
            self.in_h1 = True
            self.h1.append("")
        if tag == "script":
            self.script = [] if attrs.get("type") == "application/ld+json" else None
            self.ignored += 1
        elif tag == "style":
            self.ignored += 1
        elif tag == "meta":
            self.meta[attrs.get("property") or attrs.get("name") or ""] = attrs.get("content", "")
        elif tag == "link" and attrs.get("rel") == "canonical":
            self.meta["canonical"] = attrs.get("href", "")
        elif tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])
        if tag in {"p", "li", "div", "br", "h1", "h2", "h3", "section"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag == "h1":
            self.in_h1 = False
        if tag == "script":
            if self.script is not None:
                try:
                    self.json_blocks.append(json.loads("".join(self.script)))
                except (ValueError, TypeError):
                    pass
            self.script = None
            self.ignored = max(0, self.ignored - 1)
        elif tag == "style":
            self.ignored = max(0, self.ignored - 1)

    def handle_data(self, text):
        if self.in_h1 and not self.ignored:
            self.h1[-1] += text
        if self.script is not None:
            self.script.append(text)
        if not self.ignored:
            self.parts.append(text)

    @property
    def text(self):
        return re.sub(r"\n[ \t\n]+", "\n", re.sub(r"[ \t]+", " ", "".join(self.parts))).strip()


def objects(value, schema_type):
    if isinstance(value, dict):
        types = value.get("@type", [])
        if schema_type in ([types] if isinstance(types, str) else types):
            yield value
        for child in value.values():
            yield from objects(child, schema_type)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child, schema_type)


def _normal(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()


def _matches_posting(item, target):
    from personal_rules import canonical_url
    urls = {canonical_url(target.get(field)) for field in ("url", "apply_url", "canonical_apply_url") if target.get(field)}
    if canonical_url(item.get("url")) in urls and canonical_url(item.get("url")):
        return True
    if item.get("url"):
        return False
    title = _normal(item.get("title"))
    company = item.get("hiringOrganization", {})
    if not isinstance(company, dict):
        return False
    return bool(title and title == _normal(target.get("title")) and _normal(company.get("name")) == _normal(target.get("company")))


def job_evidence(source, url, source_kind="listing", target=None):
    page = PageParser(source)
    postings = list(objects(page.json_blocks, "JobPosting"))
    if target:
        postings = [item for item in postings if _matches_posting(item, target)]
    elif len(postings) > 1:
        # A listing page can contain recommended jobs; no identity means no safe attribution.
        postings = []
    dates, work_models, applicants = [], [], []
    for item in postings:
        for field in ("datePosted", "datePublished", "postedOn"):
            if item.get(field):
                dates.append({"field": field, "value": item[field], "url": url, "source": source_kind})
        if item.get("jobLocationType") == "TELECOMMUTE":
            work_models.append("remote")
        requirements = item.get("applicantLocationRequirements", [])
        for requirement in requirements if isinstance(requirements, list) else [requirements]:
            if isinstance(requirement, dict) and requirement.get("name"):
                applicants.append(requirement["name"])
    # Meta dateModified / article:modified_time are deliberately not publication evidence.
    return {"structured_dates": dates, "listing_work_model": work_models[0] if len(set(work_models)) == 1 else "",
            "applicant_locations": applicants, "page_text": page.text, "canonical_url": page.meta.get("canonical", ""),
            "job_postings": postings}


class PublicResearch:
    def __init__(self, max_requests=300, delay=2, limiter=None, checkpoint=None):
        self.max_requests, self.delay, self.requests = max_requests, delay, 0
        self.source_controls = {}
        self.budget = None
        self.other_limiter = None
        self.last = 0.0
        self.cache, self.records = {}, []
        self.opener = build_opener(SafeRedirects())
        self.limiter, self.checkpoint = limiter, checkpoint

    def get(self, url):
        """Retry transient transport errors, then render known board shells.

        A renderer never turns a challenge into an empty/checked result. HTTP
        429 still leaves this action with the concurrent source scheduler.
        """
        from browser_client import needs_render, render_page
        endpoint = os.environ.get('BROWSER_RENDER_URL', '')
        for attempt in range(3):
            try:
                body = self._get_http(url)
                if not endpoint or not needs_render(url, body):
                    return body
                break
            except (HTTPError, URLError, TimeoutError, OSError) as failure:
                if isinstance(failure, HTTPError) and failure.code not in {408, 500, 502, 503, 504}:
                    raise
                self.cache.pop(url.split('#', 1)[0], None)
                if attempt == 2:
                    if endpoint and needs_render(url, ''):
                        break
                    raise
                time.sleep(1 + attempt)
            except ResearchBlocked as failure:
                if (str(failure).startswith(('HTTP 401', 'HTTP 429', 'HTTP 999'))
                        or not endpoint or not needs_render(url, '')):
                    raise
                break
        if self.requests >= self.max_requests:
            raise ResearchLimit('Public research request budget reached before browser fallback')
        control = next((value for value in self.source_controls.values() if value.accepts(url)), None)
        if control: control.before(url)
        if self.budget: self.budget.claim()
        self.requests += 1
        record = {'url': url, 'transport': 'browser', 'status': 'failed'}
        self.records.append(record)
        try:
            result = render_page(endpoint, validate_url(url))
            if result['status'] == 429 and control:
                raise control.limited(url, result.get('headers', {}))
            if result['status'] >= 400:
                raise ResearchBlocked(f"Browser HTTP {result['status']}; public page remains restricted")
            body = result['html']
            # Do not cache a shell/challenge as successful research.
            if needs_render(url, body):
                title = re.search(r'<title[^>]*>(.*?)</title>',body,re.I|re.S)
                hint = PageParser(title[1]).text[:100] if title else 'untitled page'
                raise ResearchBlocked(f'Browser returned no usable public job data ({hint}); challenge, consent or JavaScript shell remains')
            record.update(status='ok', final_url=result['url'])
            if control: control.success(url)
            self.cache[url.split('#', 1)[0]] = body
            if self.checkpoint: self.checkpoint.put(url, body)
            return body
        except RateLimited:
            record['status'] = 'rate_limited'
            raise
        except Exception as failure:
            record.update(status='blocked' if isinstance(failure, ResearchBlocked) else 'failed', error=str(failure))
            raise

    def _get_http(self, url):
        parts = urlsplit(url)
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))
        if url in self.cache:
            result = self.cache[url]
            if isinstance(result, Exception):
                raise result
            return result
        if self.checkpoint:
            cached = self.checkpoint.get(url)
            if cached is not None:
                self.records.append({'url':url,'status':'ok','cached':True,'observed_at':self.checkpoint.observed_at(url)})
                return cached
        if self.requests >= self.max_requests:
            raise ResearchLimit("Public research request budget reached; remaining verification is incomplete")
        record = {"url": url, "status": "failed"}
        self.records.append(record)
        control = self.limiter if linkedin(url) else next((value for value in self.source_controls.values() if value.accepts(url)), None)
        if control is None and self.other_limiter: control = self.other_limiter(url)
        try:
            url = validate_url(url)
            if control:
                control.before(url)
            else:
                time.sleep(max(0, self.delay - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            if self.budget:
                self.budget.claim()
            self.requests += 1
            req = Request(url, headers={"User-Agent": "LocalJobsCollector/0.2", "Accept-Language": "en-GB,en;q=0.9"})
            open_request = self.opener.open
            if os.environ.get('PUBLIC_HTTP2') == '1' and (parts.hostname or '').endswith('.totaljobs.com'):
                from http2_transport import open_public
                open_request = open_public
            with open_request(req, timeout=20) as response:
                path = urlsplit(response.url).path
                if path.startswith(("/authwall", "/checkpoint", "/login", "/uas/", "/challenge")):
                    raise ResearchBlocked("Public page redirected to a login/challenge")
                content_type = response.headers.get_content_type()
                if content_type not in {"text/html", "text/plain", "application/json", "application/xml", "text/xml", "application/rss+xml"}:
                    raise ValueError(f"Unsupported public-page content type: {content_type}")
                body = response.read(4_000_001)
                if len(body) > 4_000_000:
                    raise ValueError("Public page exceeds size limit")
                source = body.decode("utf-8", errors="replace")
                page = PageParser(source)
                text = page.text
                if (re.search(r"verify (?:you are|you're) human|access denied|checking your browser|enable javascript and cookies to continue", text[:3000], re.I)
                        or ("linkedin.com" in (urlsplit(url).hostname or "") and "/in/" in path and len(text) < 150 and not page.json_blocks)):
                    raise ResearchBlocked("Public page exposed an access/challenge wall")
                record.update(status="ok", final_url=response.url)
                if isinstance(getattr(response,'http_version',None), str): record['http_version'] = response.http_version
                if control:
                    control.success(url)
                if self.checkpoint:
                    self.checkpoint.put(url,source)
                self.cache[url] = source
                return source
        except HTTPError as error:
            if error.code == 429 and control:
                record.update(status='rate_limited',error='HTTP 429')
                raise control.limited(url,error.headers) from error
            result = ResearchBlocked(f"HTTP {error.code}; public-page access restricted") if error.code in {401, 403, 429, 999} else error
            record.update(status="blocked" if isinstance(result, ResearchBlocked) else "failed", error=str(result))
            self.cache[url] = result
            raise result
        except RateLimited:
            record.update(status='rate_limited')
            raise
        except Exception as error:
            record.update(status="blocked" if isinstance(error, ResearchBlocked) else "incomplete" if isinstance(error, ResearchLimit) else "failed", error=str(error))
            self.cache[url] = error
            raise

    def search(self, query):
        url = "https://www.bing.com/search?" + urlencode({"q": query, "format": "rss"})
        source = self.get(url)
        try:
            root = ET.fromstring(source)
        except ET.ParseError as error:
            raise ValueError("Public search returned no usable RSS results; discovery incomplete") from error
        if root.tag != "rss" or root.find("channel") is None:
            raise ValueError("Public search did not return an RSS channel; discovery incomplete")
        return [{"title": item.findtext("title", ""), "url": item.findtext("link", ""), "snippet": item.findtext("description", "")} for item in root.findall(".//item")]


def enrich_job(job, research, policy):
    """Verify exact listings and employer identity, retaining a source-by-source audit."""
    job.setdefault("verification_sources", [])
    job.setdefault("structured_dates", [])
    job.setdefault("named_contact_candidates", [])
    options = policy.get("verification", {})
    maximum = options.get("maxPagesPerJob", 3)
    urls = [(job["url"], "listing")]
    if job.get("apply_url") and "linkedin.com" not in (urlsplit(job["apply_url"]).hostname or ""):
        urls.append((job["apply_url"], "employer"))
    if job.get("company_url"):
        from personal_rules import canonical_url
        company_url = canonical_url(job["company_url"])
        urls.append((company_url, "company"))
    checked = set()
    for url, kind in urls[:maximum]:
        if url in checked:
            continue
        checked.add(url)
        report = {"url": url, "kind": kind, "status": "incomplete"}
        job["verification_sources"].append(report)
        try:
            source = research.get(url)
            evidence = job_evidence(source, url, "employer" if kind == "employer" else "listing", target=job)
            report["status"] = "ok"
            if kind == "company":
                page = PageParser(source)
                organisations = list(objects(page.json_blocks, "Organization"))
                matching = [item for item in organisations if _normal(item.get("name")) == _normal(job.get("company"))]
                description = next((item.get("description", "") for item in matching if item.get("description")), "")
                description = description or page.meta.get("description", "")
                actual_name = next((item.get("name", "") for item in matching), "")
                if not actual_name:
                    actual_name = page.meta.get("og:title", "").split("|")[0].strip()
                if _normal(actual_name) != _normal(job.get('company')) or not description:
                    report.update(status='incomplete',error='Company page did not establish matching employer/about evidence')
                    continue
                # Preserve exact-listing sectors and other previously verified
                # fields; a generic company/search page cannot erase them.
                previous = job.get('employer_evidence') or {}
                job["employer_evidence"] = {**(previous if _normal(previous.get('name')) == _normal(actual_name) else {}),
                                            "name": actual_name, "description": description, "url": url}
                if job.get('source')=='google' and _normal(actual_name)==_normal(job.get('company')):
                    from google_discovery import employer_association
                    if employer_association(job,page.links,url):
                        job['google_employer_association_verified']=True
                        job['google_employer_association_url']=url
            else:
                # External pages must describe the same vacancy, not a generic careers landing page.
                if kind == "employer" and not evidence["job_postings"]:
                    page = PageParser(source)
                    exact_heading = _normal(job.get("title")) in {_normal(value) for value in page.h1}
                    specific_path = urlsplit(url).path.rstrip("/").lower() not in {"", "/jobs", "/careers", "/career", "/vacancies", "/search", "/opportunities"}
                    if not (exact_heading and specific_path and _normal(job.get("company")) in _normal(evidence["page_text"])):
                        report.update(status="incomplete", error="Application URL did not expose this exact vacancy")
                        continue
                job["structured_dates"].extend(evidence["structured_dates"])
                if evidence["listing_work_model"]:
                    job["listing_work_model"] = evidence["listing_work_model"]
                if evidence["applicant_locations"]:
                    job["applicant_locations"] = evidence["applicant_locations"]
                if kind == "employer":
                    descriptions = [PageParser(item["description"]).text for item in evidence["job_postings"] if isinstance(item.get("description"), str)]
                    # Don't use recommendations, company updates or footer text as vacancy workplace evidence.
                    job["employer_listing_text"] = "\n".join(descriptions)[:80000]
                    job["employer_listing_url"] = url
                    job["apply_url_verified"] = True
                    if re.search(r"no longer accepting applications|no longer available|job (?:is |has been )?closed|position (?:has been |is )filled", evidence["page_text"], re.I):
                        job["listing_closed"] = True
                if evidence["canonical_url"] and kind == "employer":
                    job["canonical_apply_url"] = evidence["canonical_url"]
                job.setdefault("source_urls", []).append(url)
        except RateLimited:
            raise
        except Exception as error:
            report.update(status="blocked" if isinstance(error, ResearchBlocked) else "incomplete" if isinstance(error, ResearchLimit) else "failed", error=str(error))
    return job
