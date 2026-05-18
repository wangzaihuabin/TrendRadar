# coding=utf-8
"""Lightweight article content fetcher for app-oriented persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Dict, Optional

import requests


@dataclass
class ArticleFetchResult:
    status: str
    content_text: str = ""
    content_html: str = ""
    error: str = ""


class _ArticleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._capture_depth = 0
        self._tag_stack = []
        self._text_parts = []
        self._html_parts = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        self._tag_stack.append(tag)
        if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside"}:
            self._skip_depth += 1
            return
        if tag == "article":
            self._capture_depth += 1
        if self._capture_depth and tag in {"p", "h1", "h2", "h3", "li", "blockquote"}:
            self._html_parts.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._skip_depth:
            if tag in {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside"}:
                self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._capture_depth and tag in {"p", "h1", "h2", "h3", "li", "blockquote"}:
            self._html_parts.append(f"</{tag}>")
        if tag == "article" and self._capture_depth:
            self._capture_depth -= 1
        if self._tag_stack:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = re.sub(r"\s+", " ", unescape(data)).strip()
        if not text:
            return

        current_tag = self._tag_stack[-1] if self._tag_stack else ""
        if self._capture_depth or current_tag in {"p", "h1", "h2", "h3", "li", "blockquote"}:
            self._text_parts.append(text)
            if self._capture_depth:
                self._html_parts.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self._text_parts)

    @property
    def html(self) -> str:
        return "".join(self._html_parts)


class _TextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._parts = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = re.sub(r"\s+", " ", unescape(data)).strip()
        if text:
            self._parts.append(text)

    @property
    def text(self) -> str:
        return "\n".join(self._parts)


def _html_to_text(html: str) -> str:
    parser = _TextHTMLParser()
    parser.feed(html or "")
    return parser.text.strip()


def _extract_wallstreetcn_article_id(url: str) -> Optional[str]:
    match = re.search(r"wallstreetcn\.com/articles/(\d+)", url or "")
    return match.group(1) if match else None


def _fetch_wallstreetcn_content(url: str, timeout: int, max_chars: int) -> Optional[ArticleFetchResult]:
    article_id = _extract_wallstreetcn_article_id(url)
    if not article_id:
        return None

    api_url = f"https://api-prod.wallstreetcn.com/apiv1/content/articles/{article_id}?extract=0"
    response = requests.get(
        api_url,
        timeout=timeout,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 20000:
        return ArticleFetchResult(status="failed", error=f"wallstreetcn_api: {data.get('message', 'unknown')}")

    article = data.get("data") or {}
    content_html = article.get("content") or ""
    content_text = _html_to_text(content_html)
    if not content_text:
        content_text = article.get("content_short") or article.get("description") or ""

    if not content_text:
        return ArticleFetchResult(status="failed", error="empty_content")

    return ArticleFetchResult(
        status="fetched",
        content_text=content_text[:max_chars],
        content_html=content_html[: max_chars * 2],
    )


def _extract_meta_description(html: str) -> str:
    patterns = [
        r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+name=["\']wscn-share-content["\'][^>]+content=["\']([^"\']+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html or "", flags=re.I | re.S)
        if match:
            return unescape(match.group(1)).strip()
    return ""


def guess_language(text: str) -> str:
    if not text:
        return ""
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    if cjk > 0 and cjk >= latin * 0.2:
        return "zh"
    if latin > 0:
        return "en"
    return "unknown"


def fetch_article_content(url: str, timeout: int = 12, max_chars: int = 12000) -> ArticleFetchResult:
    if not url:
        return ArticleFetchResult(status="skipped", error="empty_url")

    try:
        source_result = _fetch_wallstreetcn_content(url, timeout, max_chars)
        if source_result is not None:
            return source_result

        response = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                )
            },
        )
        response.raise_for_status()
        parser = _ArticleHTMLParser()
        parser.feed(response.text)

        content_text = parser.text.strip()
        content_html = parser.html.strip()
        if not content_text:
            content_text = _extract_meta_description(response.text)
        if not content_text:
            return ArticleFetchResult(status="failed", error="empty_content")

        return ArticleFetchResult(
            status="fetched",
            content_text=content_text[:max_chars],
            content_html=content_html[: max_chars * 2],
        )
    except Exception as e:
        return ArticleFetchResult(status="failed", error=f"{type(e).__name__}: {str(e)[:200]}")


def build_content_payload(title: str, fetch_result: ArticleFetchResult) -> Dict[str, str]:
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    return {
        "language": guess_language(f"{title}\n{fetch_result.content_text}"),
        "content_fetch_status": fetch_result.status,
        "content_text": fetch_result.content_text,
        "content_html": fetch_result.content_html,
        "content_fetched_at": now,
        "content_error": fetch_result.error,
    }
