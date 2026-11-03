"""Bounded source checks, separate from digest history and its job database.

Run: docker compose exec dashboard python3 diagnose_sources.py
One search and at most one exact detail per source; no applications or messages.
"""
from concurrent.futures import ThreadPoolExecutor
import json
from datetime import datetime, timezone
import os

import job_boards as boards
from public_research import PublicResearch
from rate_control import BoardControl, RateLimited


def check(source):
    research = PublicResearch(max_requests=8, delay=0)
    research.source_controls = {source: BoardControl(os.environ.get('DATA_DIR', '/data'), source)}
    url = boards.search_url(source, 'DevOps Engineer', 'remote')
    result = {'source': source, 'url': url, 'status': 'failed'}
    try:
        body = research.get(url)
        cards = boards.parse_search(source, body, url)
        result.update(status='search_readable', links=len(cards))
        if cards:
            job = cards[0]
            try:
                details = boards.parse_detail(source, research.get(job['url']), job)
                result['detail'] = {key: details.get(key) for key in ('title', 'company', 'location', 'structured_dates', 'listing_work_model')}
                result['detail']['url'] = job['url']
            except Exception as error:
                result['detail_error'] = str(error)
    except RateLimited as error:
        result.update(status='rate_limited', error=str(error))
    except Exception as error:
        result.update(status='blocked_or_incomplete', error=str(error))
    result['requests'] = research.records
    return result


if __name__ == '__main__':
    sources = [source for source in boards.SOURCES if source != 'linkedin']
    with ThreadPoolExecutor(max_workers=4) as pool:
        checks = list(pool.map(check, sources))
    print(json.dumps({'executed_at': datetime.now(timezone.utc).isoformat(), 'checks': checks}, indent=2))
