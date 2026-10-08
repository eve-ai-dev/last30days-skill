"""Tests for browser-backed Reddit/X fallback retrieval."""

import json
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "last30days" / "scripts"))

from lib import browser_fallback


def test_available_requires_camofox_token_and_user(monkeypatch):
    monkeypatch.delenv("CAMOFOX_ACCESS_KEY", raising=False)
    monkeypatch.delenv("CAMOFOX_USER_ID", raising=False)
    assert browser_fallback.available({}) is False

    assert browser_fallback.available({"CAMOFOX_ACCESS_KEY": "token", "CAMOFOX_USER_ID": "user"}) is True


def test_search_reddit_via_browser_normalizes_dom_results(monkeypatch):
    monkeypatch.setattr(browser_fallback, "available", lambda config=None: True)

    evaluated = [
        {
            "title": "The Hermes Agent desktop app looks fantastic.",
            "url": "https://www.reddit.com/r/hermesagent/comments/1tuwe69/the_hermes_agent_desktop_app_looks_fantastic/",
            "subreddit": "hermesagent",
            "scoreText": "128 votes",
            "commentsText": "24 comments",
            "excerpt": "A recent Reddit post about Hermes Agent.",
        },
        {
            "title": "bad non-comment link",
            "url": "https://www.reddit.com/r/hermesagent/",
            "subreddit": "hermesagent",
            "scoreText": "",
            "commentsText": "",
            "excerpt": "",
        },
    ]

    with mock.patch.object(browser_fallback.CamofoxClient, "__enter__", return_value=mock.Mock(tab_id="tab")) as enter:
        client = enter.return_value
        client.navigate.return_value = None
        client.evaluate.return_value = evaluated
        client.__exit__ = mock.Mock(return_value=False)

        results = browser_fallback.search_reddit("Hermes Agent", "2026-05-01", "2026-06-01", depth="quick")

    assert len(results) == 1
    first = results[0]
    assert first["id"] == "RB1"
    assert first["title"].startswith("The Hermes Agent")
    assert first["subreddit"] == "hermesagent"
    assert first["engagement"]["score"] == 128
    assert first["engagement"]["num_comments"] == 24
    assert first["why_relevant"] == "Reddit browser fallback"


def test_search_x_via_browser_normalizes_articles(monkeypatch):
    monkeypatch.setattr(browser_fallback, "available", lambda config=None: True)

    evaluated = [
        {
            "text": "Hermes Agent browser tooling is working better now",
            "url": "https://x.com/someone/status/123",
            "author": "someone",
            "date": "2026-06-01",
            "likesText": "15",
            "repostsText": "2",
            "repliesText": "3",
        },
        {"text": "no status url", "url": "https://x.com/search?q=Hermes", "author": "", "date": "", "likesText": "", "repostsText": "", "repliesText": ""},
    ]

    with mock.patch.object(browser_fallback.CamofoxClient, "__enter__", return_value=mock.Mock(tab_id="tab")) as enter:
        client = enter.return_value
        client.navigate.return_value = None
        client.evaluate.return_value = evaluated
        client.__exit__ = mock.Mock(return_value=False)

        results = browser_fallback.search_x("Hermes Agent", "2026-05-01", "2026-06-01", depth="quick")

    assert len(results) == 1
    first = results[0]
    assert first["id"] == "XB1"
    assert first["author_handle"] == "someone"
    assert first["engagement"]["likes"] == 15
    assert first["why_relevant"] == "X browser fallback"


def test_camofox_client_sends_authorized_requests(monkeypatch):
    calls = []

    class FakeResponse:
        status = 200
        headers = {"Content-Type": "application/json"}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return json.dumps({"tabId": "tab-1", "result": "ok"}).encode()

    def fake_urlopen(req, timeout=0):
        calls.append(req)
        return FakeResponse()

    monkeypatch.setattr(browser_fallback.urllib.request, "urlopen", fake_urlopen)
    client = browser_fallback.CamofoxClient({"CAMOFOX_ACCESS_KEY": "secret", "CAMOFOX_USER_ID": "u", "CAMOFOX_SESSION_KEY": "s"})
    client.__enter__()
    client.evaluate("document.title")
    client.__exit__(None, None, None)

    assert calls[0].headers.get("Authorization") == "Bearer secret"
    assert any("/evaluate" in call.full_url for call in calls)
    assert any(call.get_method() == "DELETE" for call in calls)
