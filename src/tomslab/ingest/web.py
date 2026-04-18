"""Scrape an authoritative web thread (currently: Linnsoft's Tom B Traders
Lab forum) into the same ``documents`` / ``document_pages`` tables the
PDF pipeline uses, so Ask Tom can cite forum posts alongside Tom's own
Discord messages and authored PDFs.

Designed to be re-runnable: posts already stored are matched by comment
id and either skipped or updated in place.
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from tomslab import db as dbmod

log = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/121.0 Safari/537.36 TomsLab/0.1"
)


@dataclass
class ForumPost:
    permalink: str          # "#comment-2493" or similar — unique per post
    author: str
    posted_on: str          # raw textual date from the page
    body_text: str
    image_urls: list[str]


# ---------------------------------------------------------------------------
# scraping
# ---------------------------------------------------------------------------
def fetch_page(url: str) -> BeautifulSoup:
    r = requests.get(url, timeout=30, headers={"User-Agent": _USER_AGENT})
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def _next_page_url(soup: BeautifulSoup, current_url: str) -> str | None:
    """Find the Drupal-style "next page" link if any."""
    nxt = soup.select_one("li.pager-next a[href], li.next a[href]")
    if not nxt:
        return None
    href = nxt.get("href")
    return urljoin(current_url, href) if href else None


def _parse_posts(soup: BeautifulSoup, base_url: str) -> list[ForumPost]:
    """Extract every post or comment on a Drupal forum page."""
    posts: list[ForumPost] = []

    # Original post (OP) — Drupal wraps it in a .node-forum wrapper.
    for node in soup.select("div.node-forum, article.node-forum"):
        body = node.select_one(".field-name-body, .forum-post-content, .content")
        if not body:
            continue
        posts.append(ForumPost(
            permalink=_make_op_permalink(base_url),
            author=_find_author(node),
            posted_on=_find_date(node),
            body_text=_extract_text(body),
            image_urls=_extract_images(body, base_url),
        ))

    # Replies / comments — each is a <div id="post-NNNN" class="comment ...">
    for c in soup.select("div.comment[id^='post-']"):
        anchor_id = c.get("id") or ""
        cid = anchor_id.replace("post-", "") if anchor_id else ""
        body = c.select_one(".forum-post-content, .field-name-comment-body, "
                            ".forum-post-main, .content")
        if not body:
            continue
        posts.append(ForumPost(
            permalink=f"#comment-{cid}" if cid else _hash_permalink(body),
            author=_find_author(c),
            posted_on=_find_date(c),
            body_text=_extract_text(body),
            image_urls=_extract_images(body, base_url),
        ))
    return posts


def _find_author(scope) -> str:
    for sel in (".username", ".author-name", ".field-name-name a", ".forum-author a"):
        el = scope.select_one(sel)
        if el and el.get_text(strip=True):
            return el.get_text(strip=True)
    return "unknown"


def _find_date(scope) -> str:
    for sel in (".forum-posted-on", "time", ".date-display-single"):
        el = scope.select_one(sel)
        if el and el.get_text(strip=True):
            return re.sub(r"\s+", " ", el.get_text(" ", strip=True))
    return ""


def _extract_text(body) -> str:
    """Plain-text body, preserving line breaks between block-level elements."""
    for tag in body.select("script, style"):
        tag.decompose()
    # Insert newlines around block-level tags so paragraphs don't smush.
    for block in body.select("p, br, li, div, h1, h2, h3, h4"):
        block.insert_after("\n")
    text = body.get_text("", strip=False)
    # Collapse whitespace and repeated blank lines.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_images(body, base_url: str) -> list[str]:
    urls: list[str] = []
    for img in body.select("img[src]"):
        u = urljoin(base_url, img["src"])
        if u.startswith("http"):
            urls.append(u)
    # dedupe, preserve order
    seen: set[str] = set()
    unique: list[str] = []
    for u in urls:
        if u in seen:
            continue
        seen.add(u)
        unique.append(u)
    return unique


def _make_op_permalink(base_url: str) -> str:
    parsed = urlparse(base_url)
    return f"op:{parsed.path}"


def _hash_permalink(body) -> str:
    h = hashlib.md5(body.get_text(strip=True).encode("utf-8")).hexdigest()
    return f"hash:{h}"


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
LINNSOFT_THREAD_URL = "https://www.linnsoft.com/topic/tom-b-traders-lab"
LINNSOFT_FILENAME = "linnsoft_tom_b_traders_lab.thread"
LINNSOFT_TITLE = "Linnsoft · Tom B Traders Lab forum"


def _get_or_create_document(
    conn, title: str, filename: str, source_url: str
) -> int:
    row = conn.execute(
        "SELECT id FROM documents WHERE filename = ?", (filename,)
    ).fetchone()
    if row:
        return int(row["id"])
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO documents(title, filename, author, doc_type, source_path, "
        "page_count, added_at) VALUES (?,?,?,?,?,?,?)",
        (title, filename, "community_forum", "reference", source_url, 0, now),
    )
    conn.commit()
    return int(cur.lastrowid)


def ingest_linnsoft_thread(conn, url: str = LINNSOFT_THREAD_URL) -> int:
    """Scrape the full thread (handling pagination) and upsert into
    documents + document_pages. Returns the number of NEW pages added."""
    log.info("Fetching %s", url)

    all_posts: list[ForumPost] = []
    current = url
    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        soup = fetch_page(current)
        all_posts.extend(_parse_posts(soup, current))
        nxt = _next_page_url(soup, current)
        current = nxt

    if not all_posts:
        log.warning("No posts parsed from %s", url)
        return 0

    doc_id = _get_or_create_document(conn, LINNSOFT_TITLE, LINNSOFT_FILENAME, url)

    # Index existing pages by the permalink we previously stored in
    # extracted_text's first line (or rendered_path). To keep migrations
    # minimal we store "permalink" as the rendered_path (it's a URL, not
    # a PNG, but the column is a free-form string and we don't render
    # forum posts to images).
    existing = {
        r["rendered_path"]: r["page_num"]
        for r in conn.execute(
            "SELECT page_num, rendered_path FROM document_pages "
            "WHERE document_id = ?",
            (doc_id,),
        )
    }

    added = 0
    updated = 0
    now = datetime.now(timezone.utc).isoformat()
    # Page numbers are assigned in post order starting from 1.
    for i, p in enumerate(all_posts, start=1):
        text = _format_page(p)
        if p.permalink in existing:
            conn.execute(
                "UPDATE document_pages SET extracted_text = ?, added_at = ?, "
                "text_source = 'web' "
                "WHERE document_id = ? AND rendered_path = ?",
                (text, now, doc_id, p.permalink),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO document_pages("
                "document_id, page_num, rendered_path, extracted_text, ocr_text, "
                "text_source, added_at) VALUES (?,?,?,?,?,?,?)",
                (doc_id, i, p.permalink, text, "", "web", now),
            )
            added += 1

    # Keep the page_count in sync.
    total_pages = conn.execute(
        "SELECT COUNT(*) AS n FROM document_pages WHERE document_id = ?",
        (doc_id,),
    ).fetchone()["n"]
    conn.execute(
        "UPDATE documents SET page_count = ? WHERE id = ?", (total_pages, doc_id)
    )
    conn.commit()

    log.info("Ingested %d new posts / %d updated from %s", added, updated, url)
    return added


def _format_page(p: ForumPost) -> str:
    """Flatten a forum post into the same text blob the embedder sees."""
    head = []
    if p.author:
        head.append(f"Author: {p.author}")
    if p.posted_on:
        head.append(f"Posted: {p.posted_on}")
    if head:
        return "\n".join(head) + "\n\n" + p.body_text
    return p.body_text
