"""Local, read-only public job-board adapters. No Apify calls or login cookies."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from collector import Document
from personal_rules import canonical_url
from public_research import PageParser, ResearchBlocked, job_evidence, objects

SOURCES = {
    'linkedin': 'LinkedIn', 'totaljobs': 'Totaljobs', 'google':'Google',
}
# Kept only to recognize historical URLs and avoid rediscovering removed boards.
REMOVED_SOURCES = {'indeed', 'glassdoor', 'welcome'}
LEGACY_SOURCE_NAMES = {'indeed':'Indeed','glassdoor':'Glassdoor','welcome':'Welcome to the Jungle'}
DOMAINS = {'indeed': 'indeed.com', 'totaljobs': 'totaljobs.com',
           'glassdoor': 'glassdoor.co.uk', 'welcome': 'welcometothejungle.com', 'google':'google.com'}


class DiscoveryIncomplete(ValueError):
    pass


def enabled_sources(config):
    values = config.get('jobSources', ['linkedin'])
    if (not isinstance(values, list) or not values or
            any(not isinstance(value, str) or value not in SOURCES for value in values)):
        raise ValueError('jobSources must be a nonempty list of supported source IDs')
    return list(dict.fromkeys(values))


def owns(source, url):
    host = (urlsplit(url).hostname or '').lower()
    if source=='google' and (host=='google.co.uk' or host.endswith('.google.co.uk')): return True
    domain = DOMAINS.get(source, 'linkedin.com')
    return host == domain or host.endswith('.' + domain)


def search_url(source, role, bucket, page=0, config=None):
    if source not in SOURCES:
        raise ValueError('Source is no longer available for searches: ' + str(source))
    config = config or {}
    location = config.get("searchLocations", {}).get(bucket, "United Kingdom")
    hours = config.get("personalPolicy", {}).get("max_age_hours", 24)
    if source == 'google':
        query = f'"{role}" "{location.split(",")[0]}" {bucket} (job OR jobs OR careers OR vacancy)'
        return 'https://www.google.com/search?' + urlencode({'q': query, 'hl': 'en', 'gl': 'gb', 'tbs': 'qdr:d' if hours == 24 else 'qdr:w', 'udm': 'web', 'start': page * 10})
    # Where a board lacks a hybrid/onsite filter, discover broadly and verify the
    # arrangement on the exact listing. A query/filter never becomes evidence.
    if source == 'totaljobs':
        query = {'postedWithin': 1 if hours == 24 else 7, 'sort': 2}
        # The live site rejects page=1 (including HTTP/2 INTERNAL_ERROR).
        if page: query['page'] = page + 1
        if bucket == 'remote': query['wfh'] = '1'
        slug = re.sub(r'[^a-z0-9]+', '-', role.lower()).strip('-')
        area = re.sub(r'[^a-z0-9]+', '-', location.split(',')[0].lower()).strip('-')
        return f'https://www.totaljobs.com/jobs/{slug}/in-{area}?' + urlencode(query)
    raise ValueError('Unknown board')


def expand_plan(linkedin_plan, config):
    sources = enabled_sources(config)
    expanded = []
    for original in linkedin_plan:
        for source in sources:
            item = {**original, 'source': source}
            bucket = next((key for key, value in {'remote': '2', 'hybrid': '3', 'on-site': '1'}.items()
                           if value == item['params'].get('f_WT')), 'unknown')
            if source != 'linkedin':
                item['url'] = search_url(source, item['params']['keywords'], bucket, config=config)
            expanded.append(item)
    return expanded


def job_url(source, value, base):
    if source=='google':
        from google_discovery import listing_url
        return listing_url(value,base) if value else ''
    url = urljoin(base, value)
    if not owns(source, url) or urlsplit(url).scheme != 'https': return ''
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    if source == 'indeed':
        key = (query.get('jk') or query.get('vjk') or [''])[0]
        return 'https://uk.indeed.com/viewjob?' + urlencode({'jk': key}) if re.fullmatch(r'[a-zA-Z0-9_-]+', key) else ''
    if source == 'totaljobs' and re.search(r'/job/[^/]+/[^/]*job\d+', parts.path):
        return canonical_url('https://www.totaljobs.com' + parts.path)
    if source == 'glassdoor':
        key = (query.get('jl') or query.get('jobListingId') or [''])[0]
        if key.isdigit(): return 'https://www.glassdoor.co.uk/partner/jobListing.htm?' + urlencode({'jobListingId': key})
    if source == 'welcome' and re.search(r'/(?:en|fr)/companies/[^/]+/jobs/[^/]+', parts.path):
        return canonical_url(url.split('?', 1)[0])
    return ''


def identity(source, url):
    if source == 'indeed': key = parse_qs(urlsplit(url).query)['jk'][0]
    elif source == 'glassdoor': key = parse_qs(urlsplit(url).query)['jobListingId'][0]
    elif source == 'totaljobs': key = re.search(r'job(\d+)', urlsplit(url).path)[1]
    else: key = hashlib.sha256(url.encode()).hexdigest()[:24]
    return source + ':' + key


def check_wall(source):
    text = PageParser(source).text[:6000]
    if re.search(r'verify (?:you are|you.re) human|access denied|checking your browser|enable javascript and cookies to continue|unusual traffic|just a moment|security verification|pardon our interruption', text, re.I):
        raise ResearchBlocked('Public job board returned a challenge/access wall')


def parse_search(source, body, url):
    if source=='google':
        from google_discovery import parse_results
        return parse_results(body,url)
    check_wall(body)
    root = Document(body).root
    results = {}
    for link in root.all(tag='a'):
        target = job_url(source, link.attrs.get('href', ''), url)
        if target:
            results.setdefault(target, {'id': identity(source, target), 'url': target,
                                       'title': link.text().strip(), 'company': '', 'location': ''})
    for posting in objects(PageParser(body).json_blocks, 'JobPosting'):
        target = job_url(source, posting.get('url', ''), url)
        if target:
            results.setdefault(target, {'id': identity(source, target), 'url': target, 'title': posting.get('title', ''), 'company': '', 'location': ''})
    if results: return list(results.values())
    # Only an explicit empty-result message proves zero. CSR/login/marketing
    # shells and unrecognized markup always remain incomplete.
    text = PageParser(body).text
    if re.search(r'\b(?:no (?:matching |search )?(?:jobs|results)(?: found| available)?|0 jobs found|we couldn.t find any jobs)\b', text, re.I):
        return []
    raise DiscoveryIncomplete('No recognizable public job links; page may require JavaScript/login or its format changed')


def public_discovery(source, role, bucket, research, location="United Kingdom"):
    """Indexed links are discovery only; snippets/dates never enter job evidence."""
    query = f'site:{DOMAINS[source]}/en/companies/ "{role}" {location} {bucket if bucket != "on-site" else ""}'
    rows = research.search(query)
    results = {}
    for row in rows:
        url = job_url(source, row.get('url',''), 'https://www.'+DOMAINS[source])
        if url: results.setdefault(url,{'id':identity(source,url),'url':url,'title':'','company':'','location':''})
    if not results: raise DiscoveryIncomplete('Public discovery found no usable exact listing links; this does not establish zero current jobs')
    return list(results.values())


def _country(value):
    return value.get('name', value.get('@id', '')) if isinstance(value, dict) else str(value or '')


def totaljobs_metadata(body, job, title, company):
    """Read a JSON literal from the exact listing's public header; never eval JS."""
    matched = []
    expected_id = re.search(r'job(\d+)', urlsplit(job['url']).path)
    if not expected_id:
        return {}
    for script in Document(body).root.all(tag='script'):
        text = script.text()
        marker = re.search(r'JobAdContent\s*=\s*\{\s*props:\s*(\{)', text)
        if not marker:
            continue
        try:
            props = json.JSONDecoder().raw_decode(text, marker.start(1))[0]
            header = props['listingHeader']
            listing = header['listingData']
            if (str(listing['id']) == expected_id[1] and
                    listing['title'].strip().casefold() == str(title).strip().casefold() and
                    header['companyData']['name'].strip().casefold() == str(company).strip().casefold()):
                matched.append(props)
        except (ValueError, KeyError, TypeError, AttributeError):
            continue
    return matched[0] if len(matched) == 1 else {}


def parse_detail(source, body, job):
    check_wall(body)
    page = PageParser(body)
    postings = list(objects(page.json_blocks, 'JobPosting'))
    selected = [item for item in postings if job_url(source, item.get('url', ''), job['url']) == job['url']]
    if not selected and len(postings) == 1 and not postings[0].get('url'):
        # Missing JSON-LD URL requires matching the actual visible title.
        item = postings[0]
        if str(item.get('title', '')).strip().casefold() in {value.strip().casefold() for value in page.h1}:
            selected = [item]
    if len(selected) != 1:
        raise ValueError('Exact listing has no uniquely matching JobPosting; recommendations are not listing evidence')
    item = selected[0]
    organization = item.get('hiringOrganization') or {}
    if not isinstance(organization, dict): organization = {}
    locations = item.get('jobLocation') or []
    locations = locations if isinstance(locations, list) else [locations]
    addresses = [value.get('address', {}) for value in locations if isinstance(value, dict)]
    addresses = [value for value in addresses if isinstance(value, dict)]
    location = '; '.join(', '.join(dict.fromkeys(str(value) for value in [address.get('addressLocality'), address.get('addressRegion'), _country(address.get('addressCountry'))] if value)) for address in addresses)
    requirements = item.get('applicantLocationRequirements') or []
    requirements = requirements if isinstance(requirements, list) else [requirements]
    countries = [value.get('name', '') for value in requirements if isinstance(value, dict) and value.get('name')]
    remote = item.get('jobLocationType') == 'TELECOMMUTE'
    if not location and remote: location = ', '.join(countries)
    description = PageParser(item.get('description') or '').text
    updates = {'title': item.get('title', ''), 'company': organization.get('name', ''),
               'description': description, 'location': location, 'job_location_country': _country(addresses[0].get('addressCountry')) if len(addresses)==1 else '',
               'structured_dates': [{'field': key, 'value': item[key], 'url': job['url'], 'source': 'exact_board_listing'}
                                    for key in ('datePosted', 'datePublished', 'postedOn') if item.get(key)],
               'listing_work_model': 'remote' if remote else '', 'applicant_locations': countries,
               'company_url': '', 'salary': '', 'apply_url': '', 'named_contact_candidates': [],
               'listing_closed': False}
    company_url = organization.get('url') or organization.get('sameAs') or ''
    if isinstance(company_url, list): company_url = next((value for value in company_url if isinstance(value, str)), '')
    if isinstance(company_url, str) and company_url.startswith('https://'): updates['company_url'] = company_url
    if organization.get('description'):
        updates['employer_evidence'] = {'name': organization.get('name', ''), 'description': PageParser(organization['description']).text, 'url': job['url']}
    if source == 'totaljobs':
        props = totaljobs_metadata(body, job, updates['title'], updates['company'])
        header = props.get('listingHeader', {})
        metadata = header.get('listingData', {}).get('metaData', {})
        labels = '; '.join(str(metadata.get(key) or '') for key in ('contractType', 'workType'))
        models = set(re.findall(r'\b(?:On-site|Hybrid|Remote)\b', labels, re.I))
        if len(models) == 1:
            updates['criteria'] = {'Workplace': next(iter(models))}
        card = props.get('companyCard') or {}
        if isinstance(card,dict) and str(card.get('name','')).strip().casefold() == str(updates['company']).strip().casefold():
            sectors = [row['name'] for row in card.get('sectors', []) if isinstance(row,dict) and isinstance(row.get('name'),str)]
            if sectors:
                evidence = updates.setdefault('employer_evidence', {'name':updates['company'],'url':job['url'],'description':''})
                evidence['sectors'] = sectors
    salary = item.get('baseSalary') or {}
    if isinstance(salary, dict):
        value = salary.get('value', {})
        if isinstance(value, dict):
            amount = value.get('value') or ' – '.join(str(value[key]) for key in ('minValue', 'maxValue') if value.get(key) is not None)
            if amount: updates['salary'] = f"{salary.get('currency', '')} {amount} {value.get('unitText', '')}".strip()
    for link in Document(body).root.all(tag='a'):
        href = urljoin(job['url'], link.attrs.get('href', ''))
        label = link.text().strip()
        if re.search(r'\bapply\b', label, re.I) and href.startswith('https://') and not owns(source, href):
            updates['apply_url'] = href
            break
    expires = item.get('validThrough')
    if expires:
        try:
            end = datetime.fromisoformat(str(expires).replace('Z', '+00:00'))
            if end.tzinfo and end < datetime.now(timezone.utc): updates['listing_closed'] = True
        except ValueError: pass
    if re.search(r'job (?:is |has been )?closed|vacancy (?:has been filled|is closed)|no longer accepting applications', description, re.I):
        updates['listing_closed'] = True
    return updates


def actor_inputs(config):
    """Schema-checked reference presets only. This function never runs an Actor."""
    output = {key: {'actor': actor, 'searches': []} for key, actor in {
        'totaljobs': 'blackfalcondata/totaljobs-scraper'}.items()}
    for role in config.get('roleVariants', []):
        for bucket, location in config.get("searchLocations", {"remote": "United Kingdom", "hybrid": "United Kingdom"}).items():
            values = {
                'totaljobs': {'query': role.lower().replace(' ', '-'), 'location': location,
                             'geo': 'TOTALJOBS', 'age': 1, 'sort': 'date', 'remote': bucket == 'remote', 'includeDetails': True,
                             'maxResults': 100, 'maxPages': 10, 'compact': False, 'incrementalMode': False, 'emitExpired': False},
            }
            for source, value in values.items():
                output[source]['searches'].append({'role': role, 'bucket': bucket, 'input': value})
    return output
