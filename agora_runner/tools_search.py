"""web_search_tinyfish -- the shared web_search client tool for both providers."""

import json
import urllib.parse

from agora_runner.config import TINYFISH_API_KEY
from agora_runner.log import log, debug_log
from agora_runner.http_util import http_json


def web_search_tinyfish(query, max_results=5):
    """Web search via TinyFish's Search API (2026-07-23, Agora Issues #1
    revisited a second time). The first replacement for the broken
    provider-hosted search tools was a no-key DuckDuckGo HTML scrape --
    that got anti-bot-blocked (HTTP 202 "anomaly" challenge page) on the
    very first live query, exactly the fragility its own tradeoffs warned
    about. TinyFish is a real documented API (structured JSON, no scraping)
    that's free for Search/Fetch as of their 2026-05-04 pricing change."""
    if not query.strip():
        return "[web_search: empty query]"
    if not TINYFISH_API_KEY:
        return "[web_search: TINYFISH_API_KEY not configured]"
    url = "https://api.search.tinyfish.ai?" + urllib.parse.urlencode({"query": query})
    debug_log(f"web_search_tinyfish: query={query!r}")
    try:
        status, body = http_json("GET", url, headers={"X-API-Key": TINYFISH_API_KEY})
    except Exception as e:
        log(f"web_search_tinyfish error: {e}")
        return f"[web_search error: {e}]"
    if status != 200:
        log(f"web_search_tinyfish {status}: {json.dumps(body)[:300]}")
        return f"[web_search: TinyFish returned HTTP {status}]"
    results = body.get("results") or []
    if not results:
        return f"[web_search: no results for {query!r}]"
    return "\n\n".join(
        f"{r.get('title', '')}\n{r.get('url', '')}\n{r.get('snippet', '')}"
        for r in results[:max_results]
    )
