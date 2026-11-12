"""Optional local renderer client. No credentials, paid proxies or remote APIs."""
import json
import re
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class BrowserTransportError(URLError):
    """Chromium did not receive a usable response from the requested page."""


def needs_render(url, body):
    import job_boards as boards
    from public_research import PageParser
    parts = urlsplit(url)
    api_paths = {
        "boards-api.greenhouse.io": "/v1/boards/",
        "api.smartrecruiters.com": "/v1/companies/",
        "api.lever.co": "/v0/postings/",
        "api.eu.lever.co": "/v0/postings/",
        "api.ashbyhq.com": "/posting-api/job-board/",
        "jobicy.com": "/api/jobs",
        "www.workable.com": "/api/accounts/",
        "remoteok.com": "/api",
    }
    # Feed adapters validate JSON. Rendering an API response as HTML destroys its structure.
    api_path = api_paths.get(parts.hostname)
    if api_path and parts.path.startswith(api_path):
        return False
    if boards.owns("teamtailor", url) and parts.path == "/jobs.rss":
        return False
    if boards.owns("jobsbyqais", url) and parts.hostname == "jobsbyqais.coderco.io":
        try:
            from qais_feed import board_data
            board_data(body)
            return False
        except ValueError:
            return True
    source = next((key for key in boards.DOMAINS if boards.owns(key, url)), None)
    if not source or source not in boards.SOURCES:
        return False
    if not body:
        return True
    try:
        # Known discovery pages must expose recognizable links or explicit zero.
        path = parts.path
        is_search = (source == 'google' or
                     source == 'indeed' and path == '/jobs' or
                     source == 'totaljobs' and path.startswith('/jobs/') or
                     source == 'glassdoor' and path.startswith('/Job/') or
                     source == 'welcome' and path.rstrip('/') == '/en/jobs')
        if is_search:
            boards.parse_search(source, body, url)
        else:
            boards.check_wall(body)
            from public_research import objects
            if not list(objects(PageParser(body).json_blocks, 'JobPosting')):
                return True
        return False
    except (ValueError, RuntimeError):
        return True


def render_page(endpoint, url):
    from public_research import validate_url, ResearchBlocked
    # The internal broker is configured by the operator, never from a listing.
    request = Request(endpoint, data=json.dumps({'url': url}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=75) as response:
            raw = response.read(4_100_001)
        if len(raw) > 4_100_000:
            raise ValueError('Browser response exceeds size limit')
        result = json.loads(raw)
        if result.get('error'):
            message = str(result['error']).splitlines()[0][:400]
            if re.search(r'\bnet::ERR_(?:HTTP2_PROTOCOL_ERROR|CONNECTION_RESET|CONNECTION_CLOSED|EMPTY_RESPONSE|TIMED_OUT)\b', message):
                raise BrowserTransportError('Browser navigation failed: ' + message)
            raise ResearchBlocked('Local browser: ' + message)
        validate_url(result['url'])
        if not isinstance(result.get('html'), str) or len(result['html'].encode()) > 4_000_000:
            raise ValueError('Invalid browser HTML response')
        if not isinstance(result.get('status'), int):
            raise ValueError('Invalid browser status')
        return result
    except BrowserTransportError:
        raise
    except (OSError, ValueError) as error:
        raise ResearchBlocked(f'Local browser unavailable or invalid response: {error}') from error
