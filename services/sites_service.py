from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from utils.yaml_loader import load_yaml

MAX_QUERY_CHARS = 100
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(slots=True)
class Site:
    key: str
    name: str
    url: str
    search_url: str
    aliases: list[str] = field(default_factory=list)


def _urls_block(data_dir: Path) -> dict[str, Any]:
    block = load_yaml(data_dir / "urls.yaml").get("urls")
    return block if isinstance(block, dict) else {}


def allowed_hosts(data_dir: Path) -> set[str]:
    domains = _urls_block(data_dir).get("allowed_domains") or []
    return {str(d).lower() for d in domains if isinstance(d, str)}


def url_is_allowed(url: str, hosts: set[str]) -> bool:
    """https only, no credentials, host exactly one of the allowed domains."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.username is None
        and parsed.password is None
        and (parsed.hostname or "").lower() in hosts
    )


def load_sites(data_dir: Path) -> dict[str, Site]:
    sites: dict[str, Site] = {}
    for key, value in (_urls_block(data_dir).get("sites") or {}).items():
        if not isinstance(value, dict):
            continue
        aliases = value.get("aliases") or []
        sites[str(key).lower()] = Site(
            key=str(key).lower(),
            name=str(value.get("name") or key),
            url=str(value.get("url") or ""),
            search_url=str(value.get("search_url") or ""),
            aliases=[str(a) for a in aliases] if isinstance(aliases, list) else [],
        )
    return sites


def clean_query(query: str) -> str:
    return " ".join(_CONTROL.sub(" ", query).split())


def _launch(url: str) -> None:
    os.startfile(url)  # noqa: S606 - https URL on an allowed host, opened by the default browser


def open_site(data_dir: Path, site_key: str) -> tuple[bool, str]:
    site = load_sites(data_dir).get(site_key)
    if site is None:
        return False, f"Сайт «{site_key}» не зарегистрирован в urls.yaml."
    if not url_is_allowed(site.url, allowed_hosts(data_dir)):
        return False, f"{site.name}: адрес не разрешён (urls.yaml: нужен https и домен из allowed_domains)."
    try:
        _launch(site.url)
    except OSError as exc:
        return False, f"Не удалось открыть {site.name}: {exc}"
    return True, f"{site.name} открыт в браузере."


def search_site(data_dir: Path, site_key: str, query: str) -> tuple[bool, str]:
    site = load_sites(data_dir).get(site_key)
    if site is None:
        return False, f"Сайт «{site_key}» не зарегистрирован в urls.yaml."
    if "{query}" not in site.search_url:
        return False, f"{site.name}: поиск не настроен (search_url в urls.yaml)."
    query = clean_query(query)
    if not query or len(query) > MAX_QUERY_CHARS:
        return False, f"Запрос должен быть от 1 до {MAX_QUERY_CHARS} символов."
    # The query is only ever percent-encoded into the query string of a fixed, allow-listed URL.
    url = site.search_url.replace("{query}", quote(query, safe=""))
    if not url_is_allowed(url, allowed_hosts(data_dir)):
        return False, f"{site.name}: адрес поиска не разрешён."
    try:
        _launch(url)
    except OSError as exc:
        return False, f"Не удалось открыть поиск {site.name}: {exc}"
    return True, f"Ищу «{query}» на {site.name}."
