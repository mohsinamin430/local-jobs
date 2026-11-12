"""Internal anonymous Chromium renderer for public job pages.

Normal JavaScript only: no stealth, logins, CAPTCHA solving or challenge clicks.
Destinations and redirects are checked before navigation; no local-file access.
"""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeout
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib.parse import urljoin, urlsplit

from public_research import validate_url


class Renderer:
    async def start(self):
        from playwright.async_api import async_playwright
        self.driver = await async_playwright().start()
        self.browser = await self.driver.chromium.launch(
            executable_path="/opt/job-desk-browser/chrome-headless-shell-linux64/chrome-headless-shell",
            chromium_sandbox=True,
        )
        self.slots = asyncio.Semaphore(2)

    async def route(self, route):
        request = route.request
        try:
            if request.resource_type in {'image', 'media', 'font'}:
                return await route.abort()
            await asyncio.to_thread(validate_url, request.url)
        except (ValueError, OSError):
            return await route.abort()
        await route.continue_()

    async def render(self, url):
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        await asyncio.to_thread(validate_url, url)
        async with self.slots:
            context = await self.browser.new_context(locale='en-GB', accept_downloads=False,
                                                     service_workers='block')
            await context.route('**/*', self.route)
            await context.route_web_socket('**/*', lambda socket: socket.close())
            page = await context.new_page()
            try:
                response = await page.goto(url, wait_until='domcontentloaded', timeout=35000)
                status = response.status if response else 502
                headers = await response.all_headers() if response else {}
                if status == 429:
                    return {'status': status, 'url': page.url, 'html': '', 'headers': headers}
                # Reject optional tracking; never click security checks or login.
                for name in ('Reject all', 'Reject All', 'Just Necessary'):
                    button = page.get_by_role('button', name=name, exact=True)
                    if await button.count() == 1 and await button.is_visible():
                        try:
                            await button.click(timeout=2000)
                        except PlaywrightTimeout:
                            # A slow consent action can still leave usable public job HTML.
                            pass
                        break
                try:
                    await page.wait_for_function('''() => document.querySelector(
                        'a[data-jk], a[href*="jobListingId"], a[href*="?jl="], a h3, a[href*="/job/"], script[type="application/ld+json"]'
                    ) || /verify you|access denied|humans only|no jobs found|no jobs? matching your search/i.test(document.body.innerText)''', timeout=7000)
                except Exception:
                    pass
                await asyncio.to_thread(validate_url, page.url)
                body = await page.content()
                if (urlsplit(url).hostname or '').endswith('google.com'):
                    body += await self.google_links(page, context)
                if len(body.encode()) > 4_000_000:
                    raise ValueError('Rendered page exceeds size limit')
                return {'url': page.url, 'status': status, 'html': body, 'headers': headers}
            finally:
                await context.close()

    async def google_links(self, page, context):
        """Resolve opaque Google redirect links; never manufacture URLs from cites.

        Stop at the first public non-Google destination without fetching it.
        These links remain discovery only, with no date/employer evidence.
        """
        from google_discovery import google_host
        links = await page.locator('a:has(h3)').evaluate_all(
            '(nodes) => nodes.slice(0,10).map(a=>({url:a.href,title:a.querySelector("h3").textContent}))')
        slots = asyncio.Semaphore(2)

        async def resolve(link):
            target = link['url']
            if not google_host(urlsplit(target).hostname) or urlsplit(target).path not in {'/goto', '/url'}:
                return ''
            async with slots:
                try:
                    for _ in range(4):
                        await asyncio.to_thread(validate_url, target)
                        if not google_host(urlsplit(target).hostname):
                            return '<a href="'+escape(target, quote=True)+'"><h3>'+escape(link['title'])+'</h3></a>'
                        response = await context.request.get(target, max_redirects=0, timeout=4000)
                        status, location = response.status, response.headers.get('location')
                        await response.dispose()
                        if status not in {301,302,303,307,308} or not location:
                            return ''
                        target = urljoin(target, location)
                except Exception:
                    return ''
            return ''
        try:
            return ''.join(await asyncio.wait_for(asyncio.gather(*(resolve(link) for link in links)), timeout=12000/1000))
        except asyncio.TimeoutError:
            return ''


renderer = Renderer()
loop = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Do not log query URLs or cookies.

    def do_GET(self):
        self.send_json({'ready': True} if self.path == '/health' else {'error': 'Not found'},
                       200 if self.path == '/health' else 404)

    def do_POST(self):
        if self.path != '/render':
            return self.send_json({'error': 'Not found'}, 404)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 12000:
                raise ValueError('Invalid request size')
            url = json.loads(self.rfile.read(length))['url']
            future = asyncio.run_coroutine_threadsafe(renderer.render(url), loop)
            try:
                result = future.result(timeout=65)
            except FutureTimeout:
                future.cancel()
                raise ValueError('Browser rendering timed out')
            self.send_json(result)
        except Exception as error:
            self.send_json({'error': str(error)[:400]}, 200)

    def send_json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    loop.run_until_complete(renderer.start())
    threading.Thread(target=loop.run_forever, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', 8090), Handler).serve_forever()
