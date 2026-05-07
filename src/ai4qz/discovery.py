from __future__ import annotations

import re
from collections import Counter
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from urllib.parse import urlparse, urlunparse


UUID_RE = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def extract_notebook_id(text: str | None) -> str | None:
    if not text:
        return None
    patterns = [
        re.compile(rf"[?&]notebook_id=({UUID_RE})"),
        re.compile(rf"/jupyter/({UUID_RE})/"),
        re.compile(rf"/notebook/(?:lab|code)/({UUID_RE})/?"),
        re.compile(rf"/(?:lab|code)/({UUID_RE})/?"),
    ]
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def normalize_base_url(url: str) -> str:
    raw = url.strip().strip("'\"")
    parsed = urlparse(raw)
    scheme = parsed.scheme.replace("wss", "https").replace("ws", "http")
    path = parsed.path.rstrip("/")
    path = re.sub(r"/terminals/websocket/[^/]+$", "", path)
    path = re.sub(r"/api/(?:terminals|contents).*$", "", path)
    path = re.sub(r"/(?:lab|tree)$", "", path)
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def _score_cookie_candidate(cookie: Cookie, url: str) -> tuple[int, str]:
    score = len(url)
    domain = cookie.domain.lstrip(".")
    if cookie.name == "_xsrf":
        score += 1_000
    if "ai-notebook" in domain:
        score += 300
    elif "nat2-notebook" in domain:
        score += 200
    elif "notebook" in domain:
        score += 100
    if not cookie.is_expired():
        score += 10
    return score, url


def discover_base_urls_from_cookiejar(
    cookiejar: CookieJar,
    notebook_id: str,
) -> list[str]:
    pattern = re.compile(
        rf"(.*/jupyter/{re.escape(notebook_id)}/[^/]+)/?$",
        re.IGNORECASE,
    )
    candidates: list[tuple[int, str]] = []
    for cookie in cookiejar:
        path = cookie.path or ""
        match = pattern.search(path)
        if not match:
            continue
        candidate = normalize_base_url(
            f"https://{cookie.domain.lstrip('.')}{match.group(1)}"
        )
        candidates.append(_score_cookie_candidate(cookie, candidate))
    if not candidates:
        return []
    candidates.sort(reverse=True)
    seen: set[str] = set()
    ordered: list[str] = []
    for _, candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    return ordered


def discover_base_urls_from_har(har_file: Path, notebook_id: str) -> list[str]:
    text = har_file.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(
        rf"(?:https|wss)://[^\"'\s]+?/jupyter/{re.escape(notebook_id)}/[^/\"'\s?]+",
        re.IGNORECASE,
    )
    counter: Counter[str] = Counter()
    for match in pattern.findall(text):
        counter[normalize_base_url(match)] += 1
    if not counter:
        return []
    return [item[0] for item in counter.most_common()]
