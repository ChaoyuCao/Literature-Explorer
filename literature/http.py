"""Small rate-limited public metadata HTTP client."""
import json
import hashlib
import sqlite3
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from . import __version__


class APIError(RuntimeError):
    pass


def https_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Use an HTTPS URL without embedded credentials.")
    return url


class HTTP:
    def __init__(self, timeout=30, retries=2, cache_dir=None, cache_ttl=86400, offline=False,
                 email='', ncbi_api_key='', progress=None):
        self.timeout, self.retries = timeout, retries
        self.last_request = {}
        self.rate_intervals = {}
        self.cache_ttl, self.offline = cache_ttl, offline
        self.email, self.ncbi_api_key, self.progress = email, ncbi_api_key, progress
        self.cache = None
        self.response_info = {}
        self.stats = {'network_requests': 0, 'cache_hits': 0, 'retries': 0}
        if cache_dir:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            self.cache = sqlite3.connect(Path(cache_dir) / 'metadata-cache.sqlite3')
            self.cache.execute('CREATE TABLE IF NOT EXISTS responses(key TEXT PRIMARY KEY, fetched REAL, body BLOB)')

    def close(self):
        if self.cache:
            self.cache.close()

    def cache_info(self, clear=False):
        if not self.cache:
            return {'entries': 0}
        if clear:
            with self.cache:
                self.cache.execute('DELETE FROM responses')
        row = self.cache.execute('SELECT COUNT(*),COALESCE(SUM(LENGTH(body)),0) FROM responses').fetchone()
        return {'entries': row[0], 'bytes': row[1], 'ttl_seconds': self.cache_ttl}

    def get(self, url, params=None):
        https_url(url)
        params = dict(params or {})
        if urlsplit(url).hostname == 'eutils.ncbi.nlm.nih.gov':
            if self.email:
                params['email'] = self.email
            if self.ncbi_api_key:
                params['api_key'] = self.ncbi_api_key
        elif urlsplit(url).hostname == 'api.crossref.org' and self.email:
            params['mailto'] = self.email
        target = url + (("&" if '?' in url else "?") + urlencode(sorted(params.items())) if params else "")
        key = hashlib.sha256(target.encode()).hexdigest()
        cached = self.cache.execute('SELECT fetched,body FROM responses WHERE key=?', (key,)).fetchone() if self.cache else None
        if cached and (self.offline or time.time() - cached[0] <= self.cache_ttl):
            self.stats['cache_hits'] += 1
            self.response_info = {'cached': True, 'fetched_at': datetime.fromtimestamp(cached[0], timezone.utc).isoformat()}
            return bytes(cached[1])
        if self.offline:
            raise APIError('Offline mode: this metadata request is not in the cache.')
        host = urlsplit(url).hostname
        request_kind = 'list' if host == 'api.crossref.org' and not re.search(r'/works/[^/]+', urlsplit(url).path) else 'single'
        rate_key = (host, request_kind)
        # Crossref's public list/search endpoints allow one request per second.
        base_interval = 1.05 if host == 'api.crossref.org' and request_kind == 'list' and not self.email else 0.36
        for attempt in range(self.retries + 1):
            # Under the unauthenticated NCBI limit of three requests per second.
            time.sleep(max(0, max(base_interval, self.rate_intervals.get(rate_key, 0)) - (time.monotonic() - self.last_request.get(host, 0))))
            self.last_request[host] = time.monotonic()
            try:
                request = Request(target, headers={"User-Agent": "mechanosensor-literature/" + __version__})
                self.stats['network_requests'] += 1
                with urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                    headers = getattr(response, 'headers', {})
                    try:
                        limit = float(headers.get('x-rate-limit-limit', 0))
                        interval = float(str(headers.get('x-rate-limit-interval', '')).removesuffix('s'))
                        if limit > 0 and interval > 0:
                            self.rate_intervals[rate_key] = interval / limit * 1.05
                    except (ValueError, TypeError):
                        pass
                fetched = time.time()
                if self.cache:
                    with self.cache:
                        self.cache.execute('INSERT OR REPLACE INTO responses VALUES(?,?,?)', (key, fetched, body))
                self.response_info = {'cached': False, 'fetched_at': datetime.fromtimestamp(fetched, timezone.utc).isoformat()}
                return body
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == self.retries:
                    raise APIError(f"HTTP {exc.code} from {host}") from None
                retry = exc.headers.get("Retry-After", "")
                delay = 2 ** attempt
                if retry.isdigit():
                    delay = min(float(retry), 30)
                elif retry:
                    try:
                        delay = min(max(0, parsedate_to_datetime(retry).timestamp() - time.time()), 30)
                    except (ValueError, TypeError):
                        pass
                self.stats['retries'] += 1
                if self.progress:
                    self.progress(f'Retrying metadata request to {host} after HTTP {exc.code}.')
                time.sleep(delay)
            except (URLError, TimeoutError):
                if attempt == self.retries:
                    raise APIError(f"Connection failed or timed out at {host}") from None
                self.stats['retries'] += 1
                if self.progress:
                    self.progress(f'Retrying metadata connection to {host}.')
                time.sleep(2 ** attempt)

    def json(self, url, params=None):
        try:
            return json.loads(self.get(url, params))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise APIError(f'Invalid JSON metadata response from {urlsplit(url).hostname}.') from None

