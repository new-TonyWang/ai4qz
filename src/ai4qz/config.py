from __future__ import annotations

import os
from pathlib import Path

import requests
import yaml

from .cookies import build_requests_session, load_cookiejar
from .discovery import (
    discover_base_urls_from_cookiejar,
    discover_base_urls_from_har,
    extract_notebook_id,
    normalize_base_url,
)
from .models import Defaults, NotebookTarget, ProjectConfig, ResolvedTarget


def _expand_path(value: str | None, base_dir: Path) -> Path | None:
    if not value:
        return None
    expanded = os.path.expandvars(os.path.expanduser(value))
    path = Path(expanded)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def load_config(path: str | Path) -> ProjectConfig:
    config_path = Path(path).expanduser().resolve()
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    base_dir = config_path.parent
    defaults_raw = data.get("defaults", {})
    defaults = Defaults(
        cookies_file=_expand_path(defaults_raw.get("cookies_file"), base_dir),
        har_file=_expand_path(defaults_raw.get("har_file"), base_dir),
        rows=int(defaults_raw.get("rows", 24)),
        cols=int(defaults_raw.get("cols", 80)),
        connect_timeout_sec=int(defaults_raw.get("connect_timeout_sec", 10)),
        command_timeout_sec=int(defaults_raw.get("command_timeout_sec", 60)),
        concurrency=int(defaults_raw.get("concurrency", 4)),
    )

    notebooks: list[NotebookTarget] = []
    for raw in data.get("notebooks", []):
        notebooks.append(
            NotebookTarget(
                name=raw["name"],
                tags=list(raw.get("tags", [])),
                base_url=raw.get("base_url"),
                entry_url=raw.get("entry_url"),
                notebook_id=raw.get("notebook_id"),
                cookies_file=_expand_path(raw.get("cookies_file"), base_dir),
                har_file=_expand_path(raw.get("har_file"), base_dir),
                notes=raw.get("notes"),
            )
        )

    return ProjectConfig(path=config_path, defaults=defaults, notebooks=notebooks)


def _base_url_is_usable(base_url: str, cookies_file: Path) -> bool:
    cookiejar = load_cookiejar(cookies_file)
    session = build_requests_session(cookiejar)
    origin = _origin_from_base_url(base_url)
    headers = {
        "Origin": origin,
        "Referer": f"{base_url.rstrip('/')}/lab",
    }
    url = f"{normalize_base_url(base_url)}/api/contents"
    try:
        response = session.get(url, headers=headers, params={"content": 0}, timeout=8)
        if response.status_code != 200:
            return False
        payload = response.json()
    except (requests.RequestException, ValueError):
        return False
    return payload.get("type") == "directory"


def _origin_from_base_url(base_url: str) -> str:
    normalized = normalize_base_url(base_url)
    if normalized.startswith("https://"):
        host = normalized.split("/", 3)[:3]
        return "/".join(host)
    if normalized.startswith("http://"):
        host = normalized.split("/", 3)[:3]
        return "/".join(host)
    raise ValueError(f"unsupported base_url scheme: {base_url}")


def resolve_target(target: NotebookTarget, defaults: Defaults) -> ResolvedTarget:
    cookies_file = target.cookies_file or defaults.cookies_file
    if not cookies_file:
        raise ValueError(f"{target.name}: missing cookies_file")
    notebook_id = (
        target.notebook_id
        or extract_notebook_id(target.entry_url)
        or extract_notebook_id(target.base_url)
    )
    base_url = target.base_url
    resolved_from = "config"

    if not base_url and notebook_id:
        cookiejar = load_cookiejar(cookies_file)
        cookie_candidates = discover_base_urls_from_cookiejar(cookiejar, notebook_id)
    else:
        cookie_candidates = []

    har_file = target.har_file or defaults.har_file
    if not base_url and notebook_id and har_file:
        har_candidates = discover_base_urls_from_har(har_file, notebook_id)
    else:
        har_candidates = []

    if not base_url:
        seen: set[str] = set()
        candidates: list[tuple[str, str]] = []
        for candidate in har_candidates:
            normalized = normalize_base_url(candidate)
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append((normalized, "har"))
        for candidate in cookie_candidates:
            normalized = normalize_base_url(candidate)
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append((normalized, "cookies"))

        for candidate, source in candidates:
            if _base_url_is_usable(candidate, cookies_file):
                base_url = candidate
                resolved_from = source
                break

        if not base_url and candidates:
            base_url, resolved_from = candidates[0]

    if not base_url:
        raise ValueError(
            f"{target.name}: unable to resolve base_url, provide base_url or a usable har_file/cookies_file"
        )

    return ResolvedTarget(
        name=target.name,
        base_url=normalize_base_url(base_url),
        cookies_file=cookies_file,
        notebook_id=notebook_id,
        resolved_from=resolved_from,
        tags=list(target.tags),
        notes=target.notes,
    )
