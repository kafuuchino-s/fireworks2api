"""Server-side web search for the Responses API built-in ``web_search`` tool.

Fireworks models do not provide a built-in web search, so this package implements
the search backend that the proxy uses to satisfy the OpenAI Responses API
``tools=[{"type": "web_search"}]`` contract. The search is delegated to a Grok
OpenAI-compatible endpoint (the same engine used by the GrokSearch project).

The answer/source splitting logic below is a faithful port of the pure functions in
``GuDaStudio/GrokSearch`` (MIT licensed). No MCP / FastMCP / tenacity runtime
dependency is introduced — only the deterministic text-parsing helpers are reused.
"""

from app.dataplane.websearch.grok_search import grok_web_search
from app.dataplane.websearch.sources import split_answer_and_sources

__all__ = ["grok_web_search", "split_answer_and_sources"]
