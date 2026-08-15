"""PyPI integration: package search, version lookup, and dependency linking.

Talks to the public PyPI JSON API (https://pypi.org/pypi/<name>/json) using only
the standard library so the package stays dependency-free for non-web use.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

PYPI_JSON_URL = "https://pypi.org/pypi/{name}/json"
PYPI_SIMPLE_URL = "https://pypi.org/simple/{name}/"

_USER_AGENT = "ZuZhuang/0.1 (+https://github.com/CodeOfMe/ZuZhuang)"
_TIMEOUT = 15


def _fetch_json(url: str) -> Any:
    """Fetch JSON from a URL with a sane user-agent and timeout."""
    return _fetch_url_json(url, timeout=_TIMEOUT)


def _fetch_url_json(url: str, timeout: int, accept: str = "application/json") -> Any:
    """Fetch JSON from a URL with a custom timeout."""
    req = Request(url, headers={"User-Agent": _USER_AGENT, "Accept": accept})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


@dataclass
class PackageInfo:
    """Resolved metadata for a single PyPI package."""

    name: str
    version: str
    summary: str = ""
    requires_python: str = ""
    project_urls: dict = field(default_factory=dict)
    requires_dist: list[str] = field(default_factory=list)
    available_versions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "summary": self.summary,
            "requires_python": self.requires_python,
            "project_urls": self.project_urls,
            "requires_dist": self.requires_dist,
            "available_versions": self.available_versions,
        }


def _normalize(name: str) -> str:
    """Normalize a package name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def lookup_package(name: str) -> PackageInfo | None:
    """Look up a package on PyPI. Returns None if not found."""
    norm = _normalize(name)
    url = PYPI_JSON_URL.format(name=quote(norm))
    try:
        data = _fetch_json(url)
    except OSError as e:
        logger.warning("PyPI lookup failed for %s: %s", name, e)
        return None

    info = data.get("info", {})
    releases = data.get("releases", {})
    versions = sorted(releases.keys(), key=_version_key, reverse=True)

    return PackageInfo(
        name=info.get("name", name),
        version=info.get("version", ""),
        summary=info.get("summary", ""),
        requires_python=info.get("requires_python", ""),
        project_urls=info.get("project_urls", {}) or {},
        requires_dist=info.get("requires_dist", []) or [],
        available_versions=versions,
    )


def search_packages(query: str, limit: int = 20) -> list[dict]:
    """Search PyPI for packages matching the query.

    PyPI deprecated the XML-RPC search endpoint, so we load the full project
    name index (the "simple" API, ~40MB JSON) once, cache it on disk, and filter
    client-side by substring. This is reliable and dependency-free.
    """
    norm = query.strip().lower()
    if not norm:
        return []

    names = _load_name_index()
    if names is None:
        return []

    out: list[dict] = []
    # Exact match first, then prefix matches, then substring matches.
    exact = [n for n in names if n.lower() == norm]
    prefix = [n for n in names if n.lower().startswith(norm) and n.lower() != norm]
    substr = [n for n in names if norm in n.lower() and not n.lower().startswith(norm)]

    for n in exact + prefix + substr:
        out.append({"name": n, "version": "", "summary": ""})
        if len(out) >= limit:
            break
    return out


# --- full name index (cached) ---
_NAME_INDEX: list[str] | None = None
_NAME_INDEX_CACHE = (
    Path(os.environ.get("ZUZHUANG_CACHE", str(Path.home() / ".cache" / "zuzhuang")))
    / "pypi_names.json"
)


def _load_name_index() -> list[str] | None:
    """Load the PyPI project-name index, using a disk cache (24h TTL)."""
    global _NAME_INDEX
    if _NAME_INDEX is not None:
        return _NAME_INDEX

    # Try cache first
    if _NAME_INDEX_CACHE.exists():
        try:
            age = time.time() - _NAME_INDEX_CACHE.stat().st_mtime
            if age < 86400:
                with _NAME_INDEX_CACHE.open() as f:
                    _NAME_INDEX = json.load(f)
                return _NAME_INDEX
        except (OSError, ValueError):
            pass

    # Download the full simple index (JSON variant)
    try:
        data = _fetch_url_json(
            "https://pypi.org/simple/",
            timeout=120,
            accept="application/vnd.pypi.simple.v1+json",
        )
    except OSError as e:
        logger.warning("failed to load PyPI name index: %s", e)
        return None

    projects = data.get("projects", []) if isinstance(data, dict) else []
    names = [p.get("name", "") for p in projects if p.get("name")]
    _NAME_INDEX = names

    # Persist cache
    try:
        _NAME_INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with _NAME_INDEX_CACHE.open("w") as f:
            json.dump(names, f)
    except OSError:
        pass

    return names


def resolve_version(name: str, spec: str | None = None) -> str | None:
    """Resolve a package name + optional version spec to a concrete version.

    spec can be None (latest), a concrete version ("1.2.3"), or a simple
    comparator like ">=1.0", "==1.2.3", "~=1.2".
    """
    info = lookup_package(name)
    if info is None:
        return None

    if not spec or spec.strip() in ("", "latest"):
        return info.version

    spec = spec.strip()

    # Exact version pinned
    if spec in info.available_versions:
        return spec

    # ==1.2.3 form
    m = re.match(r"==\s*([\w.]+)", spec)
    if m and m.group(1) in info.available_versions:
        return m.group(1)

    # >=, ~=
    m = re.match(r"(>=|~=|>)\s*([\d.]+)", spec)
    if m:
        op, base = m.group(1), m.group(2)
        base_tuple = _version_key(base)
        for v in info.available_versions:
            vt = _version_key(v)
            if op == ">=" and vt >= base_tuple:
                return v
            if op == ">" and vt > base_tuple:
                return v
            if op == "~=" and vt[: len(base_tuple) - 1] == base_tuple[: len(base_tuple) - 1]:
                return v

    # Fall back to latest
    return info.version


def _version_key(v: str) -> tuple:
    """Turn a version string into a sortable tuple of ints."""
    parts = []
    for chunk in re.split(r"[.\-+]", v):
        m = re.match(r"(\d+)", chunk)
        parts.append(int(m.group(1)) if m else 0)
    return tuple(parts)


def resolve_packages(
    packages: list[str],
) -> tuple[list[dict], list[dict]]:
    """Resolve a list of package specifiers into pip-installable form.

    Each entry may be:
      - "name"
      - "name==1.2.3"
      - "name>=1.0"
      - "name[extra1,extra2]"

    Returns (resolved, failed) where resolved items have the form
    {"spec": "name==1.2.3", "name": "name", "version": "1.2.3", "info": {...}}.
    """
    resolved: list[dict] = []
    failed: list[dict] = []

    for raw in packages:
        raw = raw.strip()
        if not raw:
            continue

        # Split off extras: name[extra] -> name, extras
        m = re.match(r"^([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(.*)$", raw)
        if not m:
            failed.append({"spec": raw, "error": "invalid specifier"})
            continue

        name = m.group(1)
        extras = m.group(2) or ""
        rest = m.group(3).strip()

        info = lookup_package(name)
        if info is None:
            failed.append({"spec": raw, "error": "package not found on PyPI"})
            continue

        version = resolve_version(name, rest or None)
        if version is None:
            failed.append({"spec": raw, "error": "could not resolve version"})
            continue

        spec = f"{info.name}{extras}=={version}"
        resolved.append(
            {
                "spec": spec,
                "name": info.name,
                "version": version,
                "info": info.to_dict(),
            }
        )

    return resolved, failed
