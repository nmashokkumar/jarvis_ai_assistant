"""
Multi-engine web & news metasearch tool for JARVIS via ddgs.
Optimized for 4-vCPU CPU inference, fast prefill, and zero hallucination.

Features:
- Dual-mode: 'news' for live events/stocks/scores, 'text' for technical documentation.
- Freshness timelimits ('d', 'w', 'm') to eliminate stale forum threads.
- Strict snippet caps (max 3 results, max 250 chars) for ultra-fast Kokoro TTS voice generation.
- SQLite TTL caching to answer follow-up queries in <2ms without network calls.
- Multi-engine backend failover (Brave, Startpage, DuckDuckGo, Yahoo).
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from database.manager import DatabaseManager

logger = logging.getLogger(__name__)

# Dedicated thread pool for synchronous search engine calls
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ddgs")


def _sync_search(
    query: str,
    category: str = "text",
    timelimit: Optional[str] = None,
    region: str = "in-en",
    max_results: int = 3,
) -> list[dict]:
    """Run search synchronously across engines via ddgs."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    with DDGS(timeout=5.0) as ddgs:
        if category == "news":
            return list(
                ddgs.news(
                    query,
                    region=region,
                    timelimit=timelimit or "d",
                    max_results=max_results,
                )
            )
        else:
            return list(
                ddgs.text(
                    query,
                    region=region,
                    timelimit=timelimit,
                    max_results=max_results,
                )
            )


async def search_web(
    query: str,
    category: str = "text",
    timelimit: Optional[str] = None,
    region: str = "in-en",
    max_results: int = 3,
    db: Optional["DatabaseManager"] = None,
) -> str:
    """
    Perform an optimized metasearch with SQLite TTL caching.

    Args:
        query: Search keywords
        category: 'text' (tech/definitions) or 'news' (live events/stocks/scores)
        timelimit: 'd' (past 24h), 'w' (past week), 'm' (past month), or None
        region: 'in-en' for India/regional, 'wt-wt' for worldwide
        max_results: Cap to 3 for fast 4-vCPU prompt prefill
        db: Optional DatabaseManager instance for TTL caching
    """
    clean_query = query.strip()
    if not clean_query:
        return "No search query provided."

    cache_key = f"{category}:{timelimit or 'none'}:{region}:{clean_query.lower()}"

    # 1. Check SQLite Cache (<2ms response)
    if db:
        try:
            cached = await db.get_cached_search(cache_key)
            if cached:
                logger.info("Search cache hit for %r", clean_query)
                return cached
        except Exception as cache_err:  # noqa: BLE001
            logger.debug("Cache check failed: %s", cache_err)

    # 2. Execute search in thread pool with strict 5-second timeout
    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            _executor,
            _sync_search,
            clean_query,
            category,
            timelimit,
            region,
            max_results,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Search failed for %r: %s", clean_query, exc)
        return f"Real-time search temporarily unavailable ({exc}). Proceeding with stored knowledge, Boss."

    if not results:
        return f"No recent web results found for '{clean_query}'. Either it's too obscure or the internet hasn't reported it yet."

    # 3. Format strictly capped snippets for fast voice prefill
    lines = [f"Search results for '{clean_query}' ({category}):\n"]
    for i, r in enumerate(results[:max_results], 1):
        title = r.get("title", "No title").strip()
        body = (r.get("body") or r.get("snippet") or "")[:250].strip()
        date_str = f" [{r.get('date')[:10]}]" if r.get("date") else ""
        source = f" (via {r.get('source')})" if r.get("source") else ""

        lines.append(f"{i}. {title}{date_str}{source}")
        if body:
            lines.append(f"   {body}")
        lines.append("")

    formatted_result = "\n".join(lines).strip()

    # 4. Save to SQLite Cache with appropriate TTL
    if db:
        try:
            # News/stocks/live events: 15 min TTL; Technical/static: 24 hour TTL
            ttl = 900 if (category == "news" or timelimit == "d") else 86400
            await db.save_cached_search(cache_key, formatted_result, ttl_seconds=ttl)
        except Exception as save_err:  # noqa: BLE001
            logger.debug("Cache save failed: %s", save_err)

    return formatted_result
