from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from actions import Actor, Channel
from jarvis.intent import parse, resolve_clarification
from jarvis.session import JarvisSession
from services import sites_service
from services.config_validation import require_valid_config, validate_data_dir
from tests.conftest import URLS_YAML
from utils.yaml_loader import ConfigError

LOCAL = Actor(Channel.LOCAL_VOICE)


@pytest.fixture
def opened(monkeypatch):
    urls: list[str] = []
    monkeypatch.setattr(sites_service, "_launch", urls.append)
    return urls


def write_urls(data_dir: Path, text: str) -> None:
    (data_dir / "urls.yaml").write_text(text, encoding="utf-8")


# ---- the action --------------------------------------------------------------------


def test_open_site_opens_only_the_configured_url(service, opened):
    result = service.execute("open_site", {"site_id": "youtube"}, LOCAL)
    assert result.status == "ok" and opened == ["https://www.youtube.com/"]


@pytest.mark.parametrize("site_id", ["evil", "https://evil.com", "youtube; calc", "../x", "", None, 5])
def test_site_id_must_be_a_registered_id_not_a_url(service, opened, site_id):
    assert service.execute("open_site", {"site_id": site_id}, LOCAL).status == "error"
    assert opened == []


def test_search_query_is_only_percent_encoded_into_a_fixed_url(service, opened):
    nasty = "котики&redirect=https://evil.com#x /../ %00 \n; calc.exe"
    assert service.execute("search_site", {"site_id": "youtube", "query": nasty}, LOCAL).status == "ok"
    parsed = urlparse(opened[0])
    assert (parsed.scheme, parsed.hostname, parsed.path) == ("https", "www.youtube.com", "/results")
    assert list(parse_qs(parsed.query)) == ["search_query"]  # nothing could add parameters
    assert "evil.com" not in parsed.netloc and "\n" not in opened[0]


@pytest.mark.parametrize("query", ["", "   ", "x" * 101, None, 5, ["котики"]])
def test_bad_queries_are_rejected(service, opened, query):
    assert service.execute("search_site", {"site_id": "youtube", "query": query}, LOCAL).status == "error"
    assert opened == []


def test_search_needs_a_configured_search_url(service, opened):
    result = service.execute("search_site", {"site_id": "spotify_web", "query": "rock"}, LOCAL)
    assert result.status == "error" and "поиск не настроен" in result.message and opened == []


def test_extra_parameters_are_rejected(service, opened):
    assert service.execute("open_site", {"site_id": "youtube", "url": "https://evil.com"}, LOCAL).status == "error"
    assert opened == []


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://www.youtube.com/watch?v=1", True),
        ("http://www.youtube.com/", False),
        ("https://evil.com/", False),
        ("https://www.youtube.com.evil.com/", False),
        ("https://user:pass@www.youtube.com/", False),
        ("file:///C:/Windows/System32/cmd.exe", False),
        ("javascript:alert(1)", False),
        ("steam://rungameid/730", False),
        ("not a url", False),
    ],
)
def test_url_allowlist(url, ok):
    assert sites_service.url_is_allowed(url, {"www.youtube.com"}) is ok


def test_runtime_check_blocks_a_site_whose_config_became_unsafe(service, data_dir, opened):
    write_urls(data_dir, URLS_YAML.replace('url: "https://www.youtube.com/"', 'url: "https://evil.com/"'))
    assert service.execute("open_site", {"site_id": "youtube"}, LOCAL).status == "error"
    assert opened == []


def test_dry_run_does_not_open_anything(dry_service, opened):
    assert dry_service.execute("open_site", {"site_id": "youtube"}, LOCAL).code == "dry_run"
    assert opened == []


# ---- config validation -------------------------------------------------------------


def test_bundled_sites_are_valid():
    from utils.paths import PROJECT_ROOT

    assert [i for i in validate_data_dir(PROJECT_ROOT / "data") if i.level == "error"] == []


@pytest.mark.parametrize("bad", ['url: "http://www.youtube.com/"', 'url: "https://evil.com/"', 'url: "file:///C:/x"'])
def test_validation_rejects_unsafe_site_urls(data_dir, bad):
    write_urls(data_dir, URLS_YAML.replace('url: "https://www.youtube.com/"', bad, 1))
    with pytest.raises(ConfigError, match="allowed_domains"):
        require_valid_config(data_dir)


def test_validation_rejects_search_url_without_placeholder_or_on_foreign_host(data_dir):
    write_urls(data_dir, URLS_YAML.replace("search_query={query}", "search_query=fixed"))
    with pytest.raises(ConfigError, match="query"):
        require_valid_config(data_dir)
    write_urls(data_dir, URLS_YAML.replace("https://www.google.com/search?q={query}", "https://evil.com/?q={query}"))
    with pytest.raises(ConfigError, match="query"):
        require_valid_config(data_dir)


# ---- phrases -----------------------------------------------------------------------


def intent_of(service, text):
    parsed = parse(text, service.catalog())
    assert parsed.intent is not None, f"{text!r}: {parsed}"
    return parsed.intent.action_id, parsed.intent.params


@pytest.mark.parametrize(
    "text,expected",
    [
        ("зайди на ютуб", ("open_site", {"site_id": "youtube"})),
        ("Открой YouTube", ("open_site", {"site_id": "youtube"})),
        ("відкрий ютуб", ("open_site", {"site_id": "youtube"})),
        ("зайдевью на YouTube", ("open_site", {"site_id": "youtube"})),  # garbled by the recogniser, seen live
        ("найди в ютубе котиков", ("search_site", {"site_id": "youtube", "query": "котиков"})),
        ("поищи на youtube музыку для работы", ("search_site", {"site_id": "youtube", "query": "музыку для работы"})),
        ("найди рецепт борща", ("search_site", {"site_id": "google", "query": "рецепт борща"})),
        ("знайди в гуглі погоду у києві", ("search_site", {"site_id": "google", "query": "погоду у києві"})),
    ],
)
def test_site_phrases(service, text, expected):
    assert intent_of(service, text) == expected


def test_search_without_a_query_asks_for_it(service):
    parsed = parse("найди на ютубе", service.catalog())
    assert parsed.clarification is not None and parsed.clarification.kind == "query"
    follow = resolve_clarification("смешные коты", parsed.clarification)
    assert follow.intent.action_id == "search_site"
    assert follow.intent.params == {"site_id": "youtube", "query": "смешные коты"}


def test_the_live_failure_is_fixed_end_to_end(service, opened):
    reply = JarvisSession(service).handle("зайди на YouTube", Channel.LOCAL_VOICE)
    assert reply.result.status == "ok" and opened == ["https://www.youtube.com/"]
    assert reply.text == "Открываю YouTube."


def test_search_phrase_end_to_end(service, opened):
    JarvisSession(service).handle("найди в ютубе котиков", Channel.LOCAL_VOICE)
    assert parse_qs(urlparse(opened[0]).query) == {"search_query": ["котиков"]}


# ---- friendly errors instead of technical ones -------------------------------------


class MissingParamLLM:
    """What the screenshot showed: the model picked open_app but gave no app_id."""

    def understand(self, text, catalog, specs, history=None, language="ru"):
        from jarvis.intent import Intent
        from jarvis.llm import Understanding

        return Understanding(intent=Intent("open_app", {}, source="llm"))


def test_missing_parameter_from_the_llm_gives_a_human_reply(service, forbid_processes):
    reply = JarvisSession(service, llm=MissingParamLLM()).handle("сделай что-нибудь интересное", Channel.LOCAL_VOICE)
    assert reply.result.status == "error"
    assert reply.text == "Не понял, что именно открыть или сделать. Назови точнее."
    assert "app_id" not in reply.text and reply.detail == ""
