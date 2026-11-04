"""Optional local renderer client. No credentials, paid proxies or remote APIs."""
import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def needs_render(url, body):
    import job_boards as boards
    from public_research import PageParser
    parts = urlsplit(url)
    api_paths = {
        "boards-api.greenhouse.io": "/v1/boards/",
        "api.smartrecruiters.com": "/v1/companies/",
    }
    # Feed adapters validate JSON. Rendering an API response as HTML destroys its structure.
    api_path = api_paths.get(parts.hostname)
    if api_path and parts.path.startswith(api_path):
        return False
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
            raise ResearchBlocked('Local browser: ' + str(result['error'])[:400])
        validate_url(result['url'])
        if not isinstance(result.get('html'), str) or len(result['html'].encode()) > 4_000_000:
            raise ValueError('Invalid browser HTML response')
        if not isinstance(result.get('status'), int):
            raise ValueError('Invalid browser status')
        return result
    except (OSError, ValueError) as error:
        raise ResearchBlocked(f'Local browser unavailable or invalid response: {error}') from error
