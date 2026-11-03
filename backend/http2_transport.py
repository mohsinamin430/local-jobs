"""HTTP/2 transport with the same public-URL, redirect and body limits."""
from email.message import Message
from io import BytesIO
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin


class Response(BytesIO):
    def __init__(self, body, url, headers, version):
        super().__init__(body)
        self.url, self.headers, self.http_version = url, headers, version


def open_public(request, timeout=20):
    import httpx
    from public_research import validate_url
    url = request.full_url
    try:
        with httpx.Client(http2=True, follow_redirects=False, timeout=timeout) as client:
            for _ in range(6):
                url = validate_url(url)
                with client.stream('GET', url, headers=dict(request.header_items())) as response:
                    headers = Message()
                    for key, value in response.headers.items(): headers[key] = value
                    if response.status_code in {301,302,303,307,308}:
                        if not headers.get('location'): raise ValueError('Redirect omitted destination')
                        url = urljoin(url, headers['location'])
                        continue
                    if response.status_code >= 400:
                        raise HTTPError(url, response.status_code, response.reason_phrase, headers, None)
                    chunks, length = [], 0
                    for chunk in response.iter_bytes():
                        length += len(chunk)
                        if length > 4_000_000: raise ValueError('Public page exceeds size limit')
                        chunks.append(chunk)
                    return Response(b''.join(chunks), str(response.url), headers, response.http_version)
        raise ValueError('Too many public-page redirects')
    except httpx.TransportError as error:
        raise URLError(str(error) or type(error).__name__) from error
