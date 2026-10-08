"""Browser-backed Reddit/X fallback via the Camofox REST API.

Usage: imported by last30days pipeline when JSON/API search returns no items.
Example: set CAMOFOX_ACCESS_KEY and CAMOFOX_USER_ID, then run last30days with reddit/x sources.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from typing import Any

from .relevance import token_overlap_relevance

BASE_URL = os.environ.get("CAMOFOX_BASE_URL", "http://camofox:9377").rstrip("/")
DEPTH_LIMITS = {"quick": 8, "default": 15, "deep": 25}


def _log(message: str) -> None:
    sys.stderr.write(f"[BrowserFallback] {message}\n")
    sys.stderr.flush()


def _first(config: dict[str, Any] | None, key: str, default: str = "") -> str:
    if config and config.get(key):
        return str(config[key])
    return os.environ.get(key, default)


def available(config: dict[str, Any] | None = None) -> bool:
    return bool(_first(config, "CAMOFOX_ACCESS_KEY") and _first(config, "CAMOFOX_USER_ID"))


def _int_from_text(value: str | None) -> int | None:
    if value is None:
        return None
    text = str(value).replace(",", "").strip().lower()
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*([km])?", text)
    if not match:
        return None
    number = float(match.group(1))
    suffix = match.group(2)
    if suffix == "k":
        number *= 1_000
    elif suffix == "m":
        number *= 1_000_000
    return int(number)


class CamofoxClient:
    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.base_url = _first(self.config, "CAMOFOX_BASE_URL", BASE_URL).rstrip("/")
        self.token = _first(self.config, "CAMOFOX_ACCESS_KEY")
        self.user_id = _first(self.config, "CAMOFOX_USER_ID")
        self.session_key = _first(self.config, "CAMOFOX_SESSION_KEY") or "last30days-browser-fallback"
        self.tab_id: str | None = None

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: int = 30) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(f"{self.base_url}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw

    def __enter__(self) -> "CamofoxClient":
        # Some Camofox sessions reject about:blank during tab creation; create
        # the tab first, then navigate explicitly in the search functions.
        payload = {"userId": self.user_id, "sessionKey": self.session_key}
        result = self._request("POST", "/tabs", payload)
        self.tab_id = result.get("tabId") or result.get("id")
        if not self.tab_id:
            raise RuntimeError("Camofox tab creation returned no tabId")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self.tab_id:
            return
        try:
            user = urllib.parse.quote(self.user_id)
            self._request("DELETE", f"/tabs/{self.tab_id}?userId={user}", timeout=10)
        except Exception:
            pass

    def navigate(self, url: str, timeout: int = 45) -> Any:
        if not self.tab_id:
            raise RuntimeError("No Camofox tab available")
        return self._request("POST", f"/tabs/{self.tab_id}/navigate", {"userId": self.user_id, "url": url}, timeout=timeout)

    def evaluate(self, expression: str, timeout: int = 30) -> Any:
        if not self.tab_id:
            raise RuntimeError("No Camofox tab available")
        result = self._request("POST", f"/tabs/{self.tab_id}/evaluate", {"userId": self.user_id, "expression": expression}, timeout=timeout)
        if isinstance(result, dict) and "result" in result:
            return result["result"]
        return result


def search_reddit(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    subreddits: list[str] | None = None,
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not available(config):
        return []
    limit = DEPTH_LIMITS.get(depth, DEPTH_LIMITS["default"])
    queries = []
    if subreddits:
        for sub in subreddits[:3]:
            q = urllib.parse.quote_plus(topic)
            queries.append(f"https://www.reddit.com/r/{sub.lstrip('r/')}/search/?q={q}&restrict_sr=1&type=link&t=month")
    q = urllib.parse.quote_plus(topic)
    queries.append(f"https://www.reddit.com/search/?q={q}&type=link&t=month")

    script = r"""
(() => Array.from(document.querySelectorAll('main a[href*="/comments/"]'))
  .map(a => {
    const root = a.closest('article, shreddit-post, div') || a;
    const text = (root.innerText || a.innerText || '').trim();
    const href = a.href || a.getAttribute('href') || '';
    const m = href.match(/\/r\/([^/]+)\/comments\//i);
    const vote = text.match(/([\d,.]+\s*[kKmM]?)\s*(?:votes?|upvotes?)/i);
    const comments = text.match(/([\d,.]+\s*[kKmM]?)\s*comments?/i);
    return {title: (a.innerText || '').trim(), url: href, subreddit: m ? m[1] : '', scoreText: vote ? vote[1] : '', commentsText: comments ? comments[1] : '', excerpt: text.slice(0, 500)};
  })
  .filter(x => x.title && x.url))()
"""
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        with CamofoxClient(config) as browser:
            for url in queries:
                browser.navigate(url)
                time.sleep(3)
                rows = browser.evaluate(script) or []
                if not isinstance(rows, list):
                    continue
                for row in rows:
                    normalized = _normalize_reddit_row(row, len(results) + 1)
                    if not normalized or normalized["url"] in seen:
                        continue
                    seen.add(normalized["url"])
                    results.append(normalized)
                    if len(results) >= limit:
                        return results
    except Exception as exc:
        _log(f"Reddit browser fallback failed: {type(exc).__name__}: {exc}")
    return results


def _normalize_reddit_row(row: dict[str, Any], index: int) -> dict[str, Any] | None:
    url = str(row.get("url") or "")
    title = str(row.get("title") or "").strip()
    if not title or "/comments/" not in url:
        return None
    score = _int_from_text(row.get("scoreText")) or 0
    num_comments = _int_from_text(row.get("commentsText")) or 0
    subreddit = str(row.get("subreddit") or "").strip()
    return {
        "id": f"RB{index}",
        "title": title[:300],
        "url": url,
        "score": score,
        "num_comments": num_comments,
        "subreddit": subreddit,
        "created_utc": None,
        "author": "",
        "selftext": str(row.get("excerpt") or "")[:500],
        "date": None,
        "engagement": {"score": score, "num_comments": num_comments, "upvote_ratio": None},
        "relevance": 0.55,
        "why_relevant": "Reddit browser fallback",
        "metadata": {"browser_fallback": True},
    }


def search_x(
    topic: str,
    from_date: str,
    to_date: str,
    depth: str = "default",
    config: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not available(config):
        return []
    limit = DEPTH_LIMITS.get(depth, DEPTH_LIMITS["default"])
    query = urllib.parse.quote_plus(f'{topic} since:{from_date}')
    url = f"https://x.com/search?q={query}&src=typed_query&f=live"
    script = r"""
(() => Array.from(document.querySelectorAll('article'))
  .map(article => {
    const text = (article.innerText || '').trim();
    const status = Array.from(article.querySelectorAll('a[href*="/status/"]')).map(a => a.href || '').find(Boolean) || '';
    const handleMatch = text.match(/@([A-Za-z0-9_]{1,20})/);
    const time = article.querySelector('time');
    const metrics = Array.from(article.querySelectorAll('[aria-label]')).map(e => e.getAttribute('aria-label') || '').join(' | ');
    const likes = metrics.match(/([\d,.]+\s*[kKmM]?)\s+likes?/i);
    const reposts = metrics.match(/([\d,.]+\s*[kKmM]?)\s+reposts?/i);
    const replies = metrics.match(/([\d,.]+\s*[kKmM]?)\s+repl(?:y|ies)/i);
    return {text, url: status, author: handleMatch ? handleMatch[1] : '', date: time ? (time.getAttribute('datetime') || '').slice(0,10) : '', likesText: likes ? likes[1] : '', repostsText: reposts ? reposts[1] : '', repliesText: replies ? replies[1] : ''};
  })
  .filter(x => x.text && x.url))()
"""
    try:
        with CamofoxClient(config) as browser:
            browser.navigate(url)
            time.sleep(4)
            rows = browser.evaluate(script) or []
    except Exception as exc:
        _log(f"X browser fallback failed: {type(exc).__name__}: {exc}")
        return []
    if not isinstance(rows, list):
        return []
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        normalized = _normalize_x_row(row, len(results) + 1, topic)
        if not normalized or normalized["url"] in seen:
            continue
        seen.add(normalized["url"])
        results.append(normalized)
        if len(results) >= limit:
            break
    return results


def _normalize_x_row(row: dict[str, Any], index: int, topic: str) -> dict[str, Any] | None:
    url = str(row.get("url") or "")
    text = str(row.get("text") or "").strip()
    if not text or "/status/" not in url:
        return None
    engagement = {
        "likes": _int_from_text(row.get("likesText")),
        "reposts": _int_from_text(row.get("repostsText")),
        "replies": _int_from_text(row.get("repliesText")),
        "quotes": None,
    }
    return {
        "id": f"XB{index}",
        "text": text[:500],
        "url": url,
        "author_handle": str(row.get("author") or "").lstrip("@"),
        "date": row.get("date") or None,
        "engagement": engagement,
        "why_relevant": "X browser fallback",
        "relevance": token_overlap_relevance(topic, text) if topic else 0.5,
    }
