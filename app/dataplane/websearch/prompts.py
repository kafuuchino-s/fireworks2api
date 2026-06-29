"""Prompts and time-context helpers for the Grok web search backend.

The search prompt is functionally equivalent to the one used by
``GuDaStudio/GrokSearch`` (MIT): it asks the Grok model to use its built-in web
search, answer the query, and append a "Sources" section so the answer/source
splitter can separate the body from the citation list.
"""

from __future__ import annotations

from datetime import datetime, timezone


def get_local_time_info() -> str:
    """Return a localized "[Current Time Context]" block for time-sensitive queries.

    Mirrors GrokSearch's ``get_local_time_info``: prefer the system local timezone,
    fall back to UTC on any error.
    """
    try:
        local_now = datetime.now().astimezone()
    except Exception:
        local_now = datetime.now(timezone.utc)
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    try:
        weekday = weekdays[local_now.weekday()]
    except Exception:
        weekday = ""
    tz_name = local_now.tzname() or "Local"
    return (
        "[Current Time Context]\n"
        f"- Date: {local_now.strftime('%Y-%m-%d')} ({weekday})\n"
        f"- Time: {local_now.strftime('%H:%M:%S')}\n"
        f"- Timezone: {tz_name}\n"
    )


_TIME_KEYWORDS_CN = [
    "当前", "现在", "今天", "明天", "昨天", "本周", "上周", "下周", "这周",
    "本月", "上月", "下月", "这个月", "今年", "去年", "明年",
    "最新", "最近", "近期", "刚刚", "刚才", "实时", "即时", "目前",
]
_TIME_KEYWORDS_EN = [
    "current", "now", "today", "tomorrow", "yesterday",
    "this week", "last week", "next week",
    "this month", "last month", "next month",
    "this year", "last year", "next year",
    "latest", "recent", "recently", "just now", "real-time", "realtime", "up-to-date",
]


def needs_time_context(query: str) -> bool:
    lowered = (query or "").lower()
    return any(kw in query for kw in _TIME_KEYWORDS_CN) or any(kw in lowered for kw in _TIME_KEYWORDS_EN)


# System prompt: instruct Grok to use its built-in web search and return a cited answer
# with an explicit "Sources" section (markdown links), so split_answer_and_sources can
# separate the body from the citations. Kept generic and provider-agnostic.
SEARCH_PROMPT = (
    "You are a web research assistant with access to a real-time web search tool. "
    "Use it to find up-to-date, accurate information for the user's query. "
    "After searching, write a clear, well-structured answer in the user's language.\n\n"
    "Rules:\n"
    "1. Search the web for current information; do not rely only on prior knowledge "
    "when the query is about recent, time-sensitive, or factual matters.\n"
    "2. Answer directly and concisely, in Markdown.\n"
    "3. Cite every claim by inlining source URLs as Markdown links where relevant.\n"
    "4. At the very end of your answer, append a section titled `Sources` listing "
    "every source as a Markdown link, one per line, in the form:\n"
    "   - [Title](https://example.com)\n"
    "   If a title is unavailable, use the bare URL:\n"
    "   - https://example.com\n"
    "5. Put nothing after the Sources section."
)
