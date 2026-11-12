"""
DuckDuckGo web search tool for JARVIS.
Returns the top 3 results formatted as plain text.
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# Thread pool for the synchronous DDGS calls
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ddgs")


def _sync_search(query: str, max_results: int = 3) -> list[dict]:
    """Run DuckDuckGo search synchronously (called from a thread)."""
    from duckduckgo_search import DDGS

    with DDGS() as ddgs:
        return list(ddgs.text(query, max_results=max_results))


async def search_web(query: str, max_results: int = 3) -> str:
    """
    Perform a DuckDuckGo web search and return formatted results.

    Args:
        query: search query string
        max_results: number of results to return (default 3)

    Returns:
        Formatted string with title, URL, and snippet for each result.
    """
    if not query.strip():
        return "No search query provided."

    try:
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            _executor, _sync_search, query, max_results
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("DuckDuckGo search failed: %s", exc)
        return f"Search failed: {exc}. Try again in a moment, Boss."

    if not results:
        return f"No results found for '{query}'. Either it's too obscure or the internet is hiding it."

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        href = r.get("href", "")
        body = r.get("body", "")[:300]  # cap snippet length
        lines.append(f"{i}. {title}")
        if href:
            lines.append(f"   URL: {href}")
        if body:
            lines.append(f"   {body}")
        lines.append("")

    return "\n".join(lines).strip()
