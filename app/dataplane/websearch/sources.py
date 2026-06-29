"""Answer / source-list splitting helpers.

Ported from ``GuDaStudio/GrokSearch`` (MIT, https://github.com/GuDaStudio/GrokSearch),
file ``src/grok_search/sources.py``. These are deterministic pure functions with no
external dependencies; they parse a free-form model answer that contains an embedded
"Sources" section and split it into (answer_body, source_list).

A source is normalized to ``{"url": str, "title"?: str, "description"?: str}``.
"""

from __future__ import annotations

import ast
import json
import re
from typing import Any

_MD_LINK_PATTERN = re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
_URL_PATTERN = re.compile(r'https?://[^\s<>"\'`，。、；：！？》）】\)]+')

_SOURCES_HEADING_PATTERN = re.compile(
    r"(?im)^"
    r"(?:#{1,6}\s*)?"
    r"(?:\*\*|__)?\s*"
    r"(sources?|references?|citations?|信源|参考资料|参考|引用|来源列表|来源)"
    r"\s*(?:\*\*|__)?"
    r"(?:\s*[（(][^)\n]*[)）])?"
    r"\s*[:：]?\s*$"
)

_SOURCES_FUNCTION_PATTERN = re.compile(
    r"(?im)(^|\n)\s*(sources|source|citations|citation|references|reference|"
    r"citation_card|source_cards|source_card)\s*\("
)

_DETAILS_OPEN_PATTERN = re.compile(r"(?i)<details>")
_DETAILS_CLOSE_PATTERN = re.compile(r"(?i)</details>")


def extract_unique_urls(text: str) -> list[str]:
    """Return all unique URLs in ``text``, in order of first appearance."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_PATTERN.finditer(text):
        url = match.group().rstrip(".,;:!?")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def merge_sources(*source_lists: list[dict]) -> list[dict]:
    seen: set[str] = set()
    merged: list[dict] = []
    for sources in source_lists:
        for item in sources or []:
            url = (item or {}).get("url")
            if not isinstance(url, str) or not url.strip():
                continue
            url = url.strip()
            if url in seen:
                continue
            seen.add(url)
            merged.append(item)
    return merged


def split_answer_and_sources(text: str) -> tuple[str, list[dict]]:
    raw = (text or "").strip()
    if not raw:
        return "", []
    for splitter in (
        _split_function_call_sources,
        _split_heading_sources,
        _split_details_block_sources,
        _split_tail_link_block,
    ):
        result = splitter(raw)
        if result:
            return result
    return raw, []


def _split_function_call_sources(text: str) -> tuple[str, list[dict]] | None:
    matches = list(_SOURCES_FUNCTION_PATTERN.finditer(text))
    if not matches:
        return None
    for match in reversed(matches):
        open_paren_idx = match.end() - 1
        extracted = _extract_balanced_call_at_end(text, open_paren_idx)
        if not extracted:
            continue
        _close_paren_idx, args_text = extracted
        sources = _parse_sources_payload(args_text)
        if not sources:
            continue
        answer = text[: match.start()].rstrip()
        return answer, sources
    return None


def _extract_balanced_call_at_end(text: str, open_paren_idx: int) -> tuple[int, str] | None:
    if open_paren_idx < 0 or open_paren_idx >= len(text) or text[open_paren_idx] != "(":
        return None
    depth = 1
    in_string: str | None = None
    escape = False
    for idx in range(open_paren_idx + 1, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == in_string:
                in_string = None
            continue
        if ch in ("'", '"'):
            in_string = ch
            continue
        if ch == "(":
            depth += 1
            continue
        if ch == ")":
            depth -= 1
            if depth == 0:
                if text[idx + 1 :].strip():
                    return None
                args_text = text[open_paren_idx + 1 : idx]
                return idx, args_text
    return None


def _split_heading_sources(text: str) -> tuple[str, list[dict]] | None:
    matches = list(_SOURCES_HEADING_PATTERN.finditer(text))
    if not matches:
        return None
    for match in reversed(matches):
        start = match.start()
        sources = _extract_sources_from_text(text[start:])
        if not sources:
            continue
        answer = text[:start].rstrip()
        return answer, sources
    return None


def _split_tail_link_block(text: str) -> tuple[str, list[dict]] | None:
    lines = text.splitlines()
    if not lines:
        return None
    idx = len(lines) - 1
    while idx >= 0 and not lines[idx].strip():
        idx -= 1
    if idx < 0:
        return None
    tail_end = idx
    link_like_count = 0
    while idx >= 0:
        line = lines[idx].strip()
        if not line:
            idx -= 1
            continue
        if not _is_link_only_line(line):
            break
        link_like_count += 1
        idx -= 1
    tail_start = idx + 1
    if link_like_count < 2:
        return None
    block_text = "\n".join(lines[tail_start : tail_end + 1])
    sources = _extract_sources_from_text(block_text)
    if not sources:
        return None
    answer = "\n".join(lines[:tail_start]).rstrip()
    return answer, sources


def _split_details_block_sources(text: str) -> tuple[str, list[dict]] | None:
    lower = text.lower()
    close_idx = lower.rfind("</details>")
    if close_idx == -1:
        return None
    tail = text[close_idx + len("</details>") :].strip()
    if tail:
        return None
    open_idx = _DETAILS_OPEN_PATTERN.search(text[:close_idx])
    if not open_idx:
        return None
    block_text = text[open_idx.start() : close_idx]
    sources = _extract_sources_from_text(block_text)
    if len(sources) < 2:
        return None
    answer = text[: open_idx.start()].rstrip()
    return answer, sources


def _is_link_only_line(line: str) -> bool:
    stripped = re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", line).strip()
    if not stripped:
        return False
    if stripped.startswith(("http://", "https://")):
        return True
    if _MD_LINK_PATTERN.search(stripped):
        return True
    return False


def _parse_sources_payload(payload: str) -> list[dict]:
    payload = (payload or "").strip().rstrip(";")
    if not payload:
        return []
    data: Any = None
    try:
        data = json.loads(payload)
    except Exception:
        try:
            data = ast.literal_eval(payload)
        except Exception:
            data = None
    if data is None:
        return _extract_sources_from_text(payload)
    if isinstance(data, dict):
        for key in ("sources", "citations", "references", "urls"):
            if key in data:
                return _normalize_sources(data[key])
        return _normalize_sources(data)
    return _normalize_sources(data)


def _normalize_sources(data: Any) -> list[dict]:
    if isinstance(data, (list, tuple)):
        items = list(data)
    elif isinstance(data, dict):
        items = [data]
    else:
        items = [data]
    normalized: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            for url in extract_unique_urls(item):
                if url not in seen:
                    seen.add(url)
                    normalized.append({"url": url})
            continue
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            title, url = item[0], item[1]
            if isinstance(url, str) and url.startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                out: dict = {"url": url}
                if isinstance(title, str) and title.strip():
                    out["title"] = title.strip()
                normalized.append(out)
            continue
        if isinstance(item, dict):
            url = item.get("url") or item.get("href") or item.get("link")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            if url in seen:
                continue
            seen.add(url)
            out = {"url": url}
            title = item.get("title") or item.get("name") or item.get("label")
            if isinstance(title, str) and title.strip():
                out["title"] = title.strip()
            desc = item.get("description") or item.get("snippet") or item.get("content")
            if isinstance(desc, str) and desc.strip():
                out["description"] = desc.strip()
            normalized.append(out)
            continue
    return normalized


def _extract_sources_from_text(text: str) -> list[dict]:
    sources: list[dict] = []
    seen: set[str] = set()
    for title, url in _MD_LINK_PATTERN.findall(text or ""):
        url = (url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        title = (title or "").strip()
        sources.append({"title": title, "url": url} if title else {"url": url})
    for url in extract_unique_urls(text or ""):
        if url in seen:
            continue
        seen.add(url)
        sources.append({"url": url})
    return sources
