from __future__ import annotations

from http.cookiejar import Cookie, MozillaCookieJar
from pathlib import Path
from urllib.parse import urlparse

import requests


def load_cookiejar(path: Path) -> MozillaCookieJar:
    cookiejar = MozillaCookieJar(str(path))
    cookiejar.load(ignore_discard=True, ignore_expires=True)
    return cookiejar


def build_requests_session(cookiejar: MozillaCookieJar) -> requests.Session:
    session = requests.Session()
    for cookie in cookiejar:
        session.cookies.set(
            cookie.name,
            cookie.value,
            domain=cookie.domain,
            path=cookie.path,
        )
    return session


def _domain_matches(hostname: str, cookie_domain: str) -> bool:
    domain = cookie_domain.lstrip(".").lower()
    host = hostname.lower()
    return host == domain or host.endswith(f".{domain}")


def _path_matches(request_path: str, cookie_path: str) -> bool:
    if not request_path.startswith("/"):
        request_path = f"/{request_path}"
    if not cookie_path:
        return True
    if request_path == cookie_path:
        return True
    return request_path.startswith(cookie_path.rstrip("/") + "/") or request_path.startswith(
        cookie_path
    )


def _matching_cookies(
    cookiejar: MozillaCookieJar,
    url: str,
    *,
    name: str | None = None,
) -> list[Cookie]:
    parsed = urlparse(url)
    scheme = parsed.scheme
    path = parsed.path or "/"
    matched: list[Cookie] = []
    for cookie in cookiejar:
        if name and cookie.name != name:
            continue
        if cookie.is_expired():
            continue
        if cookie.secure and scheme not in {"https", "wss"}:
            continue
        if not _domain_matches(parsed.hostname or "", cookie.domain):
            continue
        if not _path_matches(path, cookie.path or "/"):
            continue
        matched.append(cookie)
    return matched


def find_xsrf_token(cookiejar: MozillaCookieJar, base_url: str) -> str | None:
    url = f"{base_url.rstrip('/')}/api/terminals"
    candidates = _matching_cookies(cookiejar, url, name="_xsrf")
    if not candidates:
        return None
    candidates.sort(key=lambda item: len(item.path or ""), reverse=True)
    return candidates[0].value


def build_cookie_header(cookiejar: MozillaCookieJar, url: str) -> str:
    pairs = [f"{cookie.name}={cookie.value}" for cookie in _matching_cookies(cookiejar, url)]
    return "; ".join(pairs)
