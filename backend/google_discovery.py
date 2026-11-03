"""Google result links discover candidates; exact pages supply all job evidence."""
import re
import ipaddress
from urllib.parse import parse_qs,urljoin,urlsplit

from collector import Document
from personal_rules import canonical_url
from public_research import PageParser,ResearchBlocked


def google_host(host):
    host=(host or '').lower()
    return any(host==domain or host.endswith('.'+domain) for domain in ('google.com','google.co.uk','gstatic.com','googleusercontent.com','googleadservices.com'))


def listing_url(value,base):
    url=urljoin(base,value)
    parsed=urlsplit(url)
    if google_host(parsed.hostname):
        if parsed.path!='/url': return ''
        query=parse_qs(parsed.query)
        target=(query.get('q') or query.get('url') or [''])[0]
        if not target: return ''
        url=target
        parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or google_host(parsed.hostname): return ''
    if parsed.hostname in {'localhost','metadata.google.internal'} or parsed.hostname.endswith(('.local','.internal')): return ''
    try:
        if not ipaddress.ip_address(parsed.hostname).is_global: return ''
    except ValueError: pass
    if parsed.path.rstrip('/').lower() in {'','/jobs','/careers','/career','/vacancies','/search'} and not parsed.query: return ''
    # Normalize familiar board URLs so discoveries deduplicate with native hits.
    import job_boards as boards
    if any(boards.owns(source,url) for source in boards.REMOVED_SOURCES) or (parsed.hostname or '').endswith(('.glassdoor.com','.indeed.co.uk')) or parsed.hostname in {'glassdoor.com','indeed.co.uk'}:
        return ''
    for source in ('indeed','totaljobs','glassdoor','welcome'):
        if boards.owns(source,url): return boards.job_url(source,url,url)
    return canonical_url(url)


def parse_results(body,url):
    from job_boards import DiscoveryIncomplete,identity
    page=PageParser(body)
    if re.search(r'unusual traffic|automated queries|verify (?:you are|you.re) human|captcha|access denied',page.text[:7000],re.I):
        raise ResearchBlocked('Google returned a traffic/challenge wall')
    results={}
    for link in Document(body).root.all(tag='a'):
        heading=next(iter(link.all(tag='h3')),None)
        if heading is None: continue
        target=listing_url(link.attrs.get('href',''),url)
        if target:
            results.setdefault(target,{'id':identity('google',target),'url':target,'title':heading.text(),'company':'','location':''})
    if results: return list(results.values())
    if re.search(r'did not match any documents|no results found|no results containing',page.text,re.I):
        raise DiscoveryIncomplete('Google returned no indexed results for this query; this does not establish zero qualifying vacancies')
    raise DiscoveryIncomplete('Google did not expose readable result links (JavaScript/consent or changed markup). Open the Google search link; coverage is incomplete')


def employer_association(job,company_links=(),company_page=''):
    """A generic search result must be an employer page or an associated ATS.

    Recognized native boards retain their existing verification policy. A
    different host needs a public company-page link to the exact vacancy or its
    company-specific ATS board. A search snippet is never association evidence.
    """
    import job_boards as boards
    target=canonical_url(job.get('url',''))
    page=urlsplit(target)
    if any(boards.owns(source,target) for source in ('linkedin','indeed','totaljobs','glassdoor','welcome')): return True
    company=urlsplit(job.get('company_url',''))
    host=(company.hostname or '').removeprefix('www.')
    if host and company.path.rstrip('/') in {'','/about','/about-us'} and (page.hostname==host or (page.hostname or '').endswith('.'+host)):
        return True
    ats={'jobs.lever.co','boards.greenhouse.io','job-boards.greenhouse.io','apply.workable.com','jobs.ashbyhq.com','jobs.smartrecruiters.com'}
    for value in company_links:
        link=canonical_url(urljoin(company_page,value))
        if link==target: return True
        parsed=urlsplit(link)
        path=parsed.path.rstrip('/')
        if parsed.hostname in ats and parsed.hostname==page.hostname and path and path.split('/')[1].lower() not in {'jobs','careers','search','apply','en','j'} and page.path.startswith(path+'/'):
            return True
    return False
