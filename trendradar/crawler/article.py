# coding=utf-8
"""Lightweight article content fetcher for app-oriented persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Dict

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
