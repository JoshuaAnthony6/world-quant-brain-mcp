#!/usr/bin/env python3
"""
WorldQuant BRAIN Forum Functions - Optimized Version
Comprehensive forum functionality using BRAIN session for Zendesk SSO.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

# Constants
ZENDESK_BASE_URL = "https://support.worldquantbrain.com"
GLOSSARY_ARTICLE_ID = "4902349883927"
SNIPPET_LENGTH = 300

# Navigation patterns (compiled once for performance)
_NAVIGATION_PATTERNS = tuple(
    re.compile(p) for p in [
        r'^\d+ days? ago$',
        r'~\d+ minute read',
        r'^Follow$',
        r'^Not yet followed$',
        r'^Updated$',
        r'^AS\d+$',
        r'^[A-Z] - [A-Z] - [A-Z]',
        r'^[A-Z]$',
    ]
)
_DEFINITION_STARTERS = frozenset([
    'the', 'a', 'an', 'this', 'that', 'it', 'is', 'are', 'was', 'were',
    'for', 'to', 'in', 'on', 'at', 'by', 'with', 'of', 'from'
])

def _log(level: str, message: str) -> None:
    """Unified logging with timestamp matching main.py format."""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S,%f')[:-3]
    print(f"{timestamp} - {level} - {message}", file=sys.stderr)


@lru_cache(maxsize=128)
def _html_to_text(html: str) -> str:
    """Convert HTML to plain text with caching for repeated content."""
    if not html:
        return ""
    # Delay import for performance
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator='\n')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return '\n'.join(lines)


def _extract_post_id(post_url_or_id: str) -> str:
    """Extract numeric forum post id from URL or raw id."""
    if not post_url_or_id:
        raise ValueError("Forum post id is required")
    match = re.search(r'/posts/(\d+)|^(\d+)', str(post_url_or_id))
    post_id = next((g for g in match.groups() if g), None) if match else None
    if not post_id:
        raise ValueError(f"Could not extract forum post id from: {post_url_or_id}")
    return post_id


def _extract_locale(post_url_or_id: str, default: str = "zh-cn") -> str:
    """Extract forum locale from URL."""
    if isinstance(post_url_or_id, str):
        match = re.search(r'/hc/([^/]+)/community/posts/', post_url_or_id)
        if match:
            return match.group(1)
    return default


def _is_navigation_or_metadata(line: str) -> bool:
    """Check if line is navigation/metadata."""
    stripped = line.strip()
    return any(p.match(stripped) for p in _NAVIGATION_PATTERNS)


def _looks_like_term(line: str) -> bool:
    """Check if line looks like a glossary term."""
    if len(line) > 100 or len(line) < 2:
        return False
    if _is_navigation_or_metadata(line):
        return False
    first_word = line.lower().split()[0] if line else ''
    if first_word in _DEFINITION_STARTERS:
        return False
    starts_capital = line[0].isupper() if line else False
    is_short = len(line) <= 80
    has_all_caps = bool(re.match(r'^[A-Z\s\-/&()]+$', line))
    return is_short and (starts_capital or has_all_caps)


def _parse_glossary_terms(content: str) -> List[Dict[str, str]]:
    """Parse glossary terms from HTML."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(content, 'html.parser')
    article_body = soup.select_one('.article-body') or soup
    lines = article_body.get_text(separator='\n').split('\n')

    terms = []
    current_term = None
    current_definition = []

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if _looks_like_term(line):
            if current_term:
                terms.append({
                    "term": current_term,
                    "definition": " ".join(current_definition).strip()
                })
            current_term = line
            current_definition = []
        elif current_term:
            current_definition.append(line)

    if current_term:
        terms.append({
            "term": current_term,
            "definition": " ".join(current_definition).strip()
        })

    return [
        t for t in terms
        if t["term"] and len(t["definition"]) > 10
        and not _is_navigation_or_metadata(t["term"])
        and "ago" not in t["definition"]
        and "minute read" not in t["definition"]
    ]


def _load_env_file() -> None:
    """Load .env file if exists."""
    try:
        from dotenv import load_dotenv, find_dotenv
        env_path = find_dotenv(usecwd=True)
        if env_path:
            load_dotenv(env_path, override=False)
            return
    except ImportError:
        pass

    # Fallback: manual .env parsing
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                os.environ.setdefault(key, value)
        except Exception as e:
            _log("WARNING", f"Failed to parse .env: {e}")


class ForumClient:
    """Forum client for WorldQuant BRAIN support site."""

    _HC_LOCALES = ("zh-cn", "en-us")

    def __init__(self) -> None:
        _load_env_file()

        self.base_url = os.getenv("FORUM_SETTINGS_BASE_URL", ZENDESK_BASE_URL)

        # Parse timeout (seconds)
        try:
            self.timeout = float(os.getenv("FORUM_SETTINGS_TIMEOUT", "30"))
        except ValueError:
            self.timeout = 30.0

        # Parse concurrency
        try:
            concurrency = int(os.getenv("FORUM_MAX_CONCURRENCY", "1"))
            self._semaphore = asyncio.Semaphore(max(1, concurrency))
        except ValueError:
            self._semaphore = asyncio.Semaphore(1)

    def _post_api_url(self, post_id: str) -> str:
        return f"{self.base_url}/api/v2/community/posts/{post_id}.json?include=users"

    def _comments_api_url(self, post_id: str, page: int) -> str:
        return f"{self.base_url}/api/v2/community/posts/{post_id}/comments.json?page={page}&include=users"

    async def _get_brain_client(self):
        """Get brain_client with import handling."""
        try:
            from main import brain_client
            return brain_client
        except ImportError:
            current_dir = Path(__file__).parent
            if str(current_dir) not in sys.path:
                sys.path.insert(0, str(current_dir))
            from main import brain_client
            return brain_client

    async def _ensure_session(self, locale: str = "zh-cn"):
        """Ensure Zendesk session via BRAIN SSO."""
        brain_client = await self._get_brain_client()
        await brain_client.ensure_authenticated()

        access_url = (
            "https://worldquantbrain.zendesk.com/access"
            f"?brand_id=1500000894061&locale={locale}"
            f"&return_to={self.base_url}/hc/{locale}"
        )

        def do_request():
            return brain_client.session.get(
                access_url,
                timeout=int(self.timeout),
                allow_redirects=True,
                headers={'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'},
            )

        response = await asyncio.to_thread(do_request)
        _log("INFO", f"SSO handshake: status={response.status_code}, url={response.url}")
        return brain_client.session

    async def _get_json(self, session, url: str) -> Dict[str, Any]:
        """Fetch JSON from API."""
        def do_request():
            resp = session.get(
                url,
                timeout=int(self.timeout),
                headers={'Accept': 'application/json'},
            )
            resp.raise_for_status()
            return resp.json()
        return await asyncio.to_thread(do_request)

    async def _post_json(self, session, url: str, data: Dict) -> Any:
        """Post JSON to API."""
        def do_request():
            resp = session.post(
                url,
                json=data,
                timeout=int(self.timeout),
                headers={'Content-Type': 'application/json'},
            )
            if resp.status_code not in (200, 201, 204):
                resp.raise_for_status()
            return resp
        return await asyncio.to_thread(do_request)

    async def _with_fallback(self, locale: str, url_builder, extractor, label: str) -> Dict[str, Any]:
        """Try locale with fallback to alternative."""
        alt_locale = "en-us" if locale == "zh-cn" else "zh-cn"
        items = []
        seen_ids = set()
        last_error = None

        for loc in (alt_locale, locale):  # Primary locale last for priority
            try:
                session = await self._ensure_session(loc)
                url = url_builder(loc)
                while url and len(items) < 1000:  # Safety limit
                    payload = await self._get_json(session, url)
                    for item in extractor(payload):
                        item_id = str(item.get("id", ""))
                        if item_id and item_id not in seen_ids:
                            seen_ids.add(item_id)
                            items.append(item)
                    url = payload.get("next_page")
            except Exception as e:
                last_error = e
                _log("WARNING", f"{label} locale={loc}: {e}")

        if not items and last_error:
            raise last_error

        _log("SUCCESS", f"{label}: {len(items)} items")
        return {"success": True, "items": items, "count": len(items)}

    # === Public API Methods ===

    async def get_glossary_terms(self) -> List[Dict[str, str]]:
        """Get glossary terms."""
        async with self._semaphore:
            session = await self._ensure_session("en-us")
            payload = await self._get_json(
                session,
                f"{self.base_url}/api/v2/help_center/articles/{GLOSSARY_ARTICLE_ID}.json"
            )
            body = payload.get("article", {}).get("body", "")
            if not body:
                _log("WARNING", "Glossary article empty")
                return []
            terms = _parse_glossary_terms(body)
            _log("SUCCESS", f"Extracted {len(terms)} glossary terms")
            return terms

    async def read_forum_post(self, post_url_or_id: str, include_comments: bool = True) -> Dict[str, Any]:
        """Read forum post with comments."""
        async with self._semaphore:
            post_id = _extract_post_id(post_url_or_id)
            locale = _extract_locale(post_url_or_id)
            session = await self._ensure_session(locale)

            # Get post
            post_data = await self._get_json(session, self._post_api_url(post_id))
            post = post_data.get("post", {})
            users = {u.get("id"): u.get("name") for u in post_data.get("users", []) if isinstance(u, dict)}

            result = {
                "success": True,
                "post": {
                    "title": post.get("title", "Unknown"),
                    "author": users.get(post.get("author_id"), "Unknown"),
                    "body": _html_to_text(post.get("details", "")),
                    "votes": str(post.get("vote_sum", 0)),
                    "date": post.get("created_at", ""),
                    "url": post.get("html_url", ""),
                },
                "comments": [],
                "total_comments": 0,
            }

            # Get comments
            if include_comments:
                comments = []
                page = 1
                while True:
                    try:
                        cdata = await self._get_json(session, self._comments_api_url(post_id, page))
                        cusers = {u.get("id"): u.get("name") for u in cdata.get("users", []) if isinstance(u, dict)}
                        users.update(cusers)

                        for c in cdata.get("comments", []):
                            comments.append({
                                "author": users.get(c.get("author_id"), "Unknown"),
                                "body": _html_to_text(c.get("body", "")),
                                "date": c.get("created_at", ""),
                            })

                        if not cdata.get("next_page"):
                            break
                        page += 1
                    except Exception as e:
                        _log("WARNING", f"Comment fetch stopped: {e}")
                        break

                result["comments"] = comments
                result["total_comments"] = len(comments)

            _log("SUCCESS", f"Read post {post_id} with {result['total_comments']} comments")
            return result

    async def list_community_posts(self, sort_by: str = "recent_activity", limit: int = 50, locale: str = "zh-cn") -> Dict[str, Any]:
        """List community posts."""
        async with self._semaphore:
            def url_builder(loc):
                return f"{self.base_url}/api/v2/community/posts.json?sort_by={sort_by}&limit={min(limit, 100)}"

            def extractor(payload):
                return [{
                    "id": p["id"],
                    "title": p["title"],
                    "author": (p.get("author", {}) or {}).get("name", "?"),
                    "votes": p.get("vote_sum", 0),
                    "comment_count": p.get("comment_count", 0),
                    "created_at": p.get("created_at"),
                    "url": p.get("html_url", ""),
                } for p in payload.get("posts", [])]

            result = await self._with_fallback(locale, url_builder, extractor, "Community posts")
            posts = result["items"][:limit]
            return {
                "success": True,
                "posts": posts,
                "count": len(posts),
                "has_more": len(result["items"]) > limit,
            }

    async def list_community_topics(self, locale: str = "zh-cn") -> List[Dict[str, Any]]:
        """List community topics."""
        async with self._semaphore:
            session = await self._ensure_session(locale)
            payload = await self._get_json(session, f"{self.base_url}/api/v2/community/topics.json")
            topics = [{
                "id": t["id"],
                "name": t["name"],
                "description": t.get("description", ""),
                "post_count": t.get("post_count", 0),
            } for t in payload.get("topics", [])]
            _log("SUCCESS", f"Listed {len(topics)} topics")
            return topics

    async def get_topic_posts(self, topic_id: str, sort_by: str = "recent_activity", limit: int = 50, locale: str = "zh-cn") -> Dict[str, Any]:
        """Get posts for a topic."""
        async with self._semaphore:
            def url_builder(loc):
                return f"{self.base_url}/api/v2/community/topics/{topic_id}/posts.json?sort_by={sort_by}&limit={min(limit, 100)}"

            def extractor(payload):
                return [{
                    "id": p["id"],
                    "title": p["title"],
                    "author": (p.get("author", {}) or {}).get("name", "?"),
                    "votes": p.get("vote_sum", 0),
                    "comment_count": p.get("comment_count", 0),
                    "created_at": p.get("created_at"),
                    "url": p.get("html_url", ""),
                } for p in payload.get("posts", [])]

            result = await self._with_fallback(locale, url_builder, extractor, f"Topic {topic_id} posts")
            posts = result["items"][:limit]
            return {
                "success": True,
                "topic_id": topic_id,
                "posts": posts,
                "count": len(posts),
                "has_more": len(result["items"]) > limit,
            }

    async def search_forum_posts(self, search_query: str, locale: str = "zh-cn", max_results: int = 50) -> Dict[str, Any]:
        """Search forum posts."""
        async with self._semaphore:
            def url_builder(loc):
                return f"{self.base_url}/api/v2/community/search.json?query={search_query}&per_page={min(max_results, 100)}"

            def extractor(payload):
                return [{
                    "id": r.get("id"),
                    "title": r.get("title", r.get("name", "")),
                    "author": (r.get("author", {}) or {}).get("name", "?"),
                    "votes": r.get("vote_sum", 0),
                    "comment_count": r.get("comment_count", 0),
                    "created_at": r.get("created_at"),
                    "url": r.get("html_url", ""),
                    "snippet": (r.get("snippet") or r.get("details", ""))[:SNIPPET_LENGTH],
                } for r in payload.get("results", [])]

            result = await self._with_fallback(locale, url_builder, extractor, f"Search '{search_query}'")
            items = result["items"][:max_results]
            return {
                "success": True,
                "query": search_query,
                "results": items,
                "count": len(items),
                "has_more": len(result["items"]) > max_results,
            }

    async def vote_post(self, post_id: str, direction: str = "up", locale: str = "zh-cn") -> Dict[str, Any]:
        """Vote on a post."""
        if direction not in ("up", "down"):
            raise ValueError("direction must be 'up' or 'down'")

        async with self._semaphore:
            session = await self._ensure_session(locale)
            url = f"{self.base_url}/api/v2/community/posts/{post_id}/vote.json"
            resp = await self._post_json(session, url, {"vote": {"direction": direction}})
            _log("SUCCESS", f"Voted {direction} on post {post_id}")
            return {
                "success": True,
                "post_id": post_id,
                "direction": direction,
                "status_code": resp.status_code,
            }

    async def list_help_center_articles(self, locale: str = "zh-cn", section_id: str | None = None,
                                        category_id: str | None = None, sort_by: str = "updated_at",
                                        limit: int = 50) -> Dict[str, Any]:
        """List Help Center articles."""
        async with self._semaphore:
            params = [f"sort_by={sort_by}", f"per_page={min(limit, 100)}"]
            if section_id:
                params.append(f"section_id={section_id}")
            if category_id:
                params.append(f"category_id={category_id}")
            param_str = "&".join(params)

            def url_builder(loc):
                return f"{self.base_url}/api/v2/help_center/{loc}/articles.json?{param_str}"

            def extractor(payload):
                return [{
                    "id": a["id"],
                    "title": a["title"],
                    "section_id": a.get("section_id"),
                    "category_id": a.get("category_id"),
                    "updated_at": a.get("updated_at"),
                    "url": a.get("html_url", ""),
                } for a in payload.get("articles", [])]

            result = await self._with_fallback(locale, url_builder, extractor, "HC articles")
            items = result["items"][:limit]
            return {
                "success": True,
                "articles": items,
                "count": len(items),
                "has_more": len(result["items"]) > limit,
            }

    async def search_help_center_articles(self, query: str, locale: str = "zh-cn", limit: int = 50) -> Dict[str, Any]:
        """Search Help Center articles."""
        async with self._semaphore:
            def url_builder(loc):
                return f"{self.base_url}/api/v2/help_center/articles/search.json?query={query}&per_page={min(limit, 100)}"

            def extractor(payload):
                return [{
                    "id": r["id"],
                    "title": r.get("title", r.get("name", "")),
                    "section_id": r.get("section_id"),
                    "url": r.get("html_url", ""),
                    "snippet": (r.get("snippet") or r.get("body", ""))[:SNIPPET_LENGTH],
                } for r in payload.get("results", [])]

            result = await self._with_fallback(locale, url_builder, extractor, f"HC search '{query}'")
            items = result["items"][:limit]
            return {
                "success": True,
                "query": query,
                "results": items,
                "count": len(items),
                "has_more": len(result["items"]) > limit,
            }

    async def get_help_center_article(self, article_id: str, locale: str = "zh-cn") -> Dict[str, Any]:
        """Get Help Center article."""
        async with self._semaphore:
            alt_locale = "en-us" if locale == "zh-cn" else "zh-cn"
            last_error = None

            for loc in (locale, alt_locale):
                try:
                    session = await self._ensure_session(loc)
                    payload = await self._get_json(
                        session,
                        f"{self.base_url}/api/v2/help_center/articles/{article_id}.json"
                    )
                    article = payload.get("article", {})
                    if article:
                        return {
                            "success": True,
                            "id": article.get("id"),
                            "title": article.get("title"),
                            "body": _html_to_text(article.get("body", "")),
                            "url": article.get("html_url", ""),
                            "updated_at": article.get("updated_at"),
                        }
                except Exception as e:
                    last_error = e
                    _log("WARNING", f"Article {article_id} locale={loc}: {e}")

            raise last_error or Exception(f"Article {article_id} not found")

    async def list_help_center_sections(self, locale: str = "zh-cn") -> List[Dict[str, Any]]:
        """List Help Center sections."""
        async with self._semaphore:
            def url_builder(loc):
                return f"{self.base_url}/api/v2/help_center/{loc}/sections.json"

            def extractor(payload):
                return [{
                    "id": s["id"],
                    "name": s["name"],
                    "description": s.get("description", ""),
                    "category_id": s.get("category_id"),
                } for s in payload.get("sections", [])]

            result = await self._with_fallback(locale, url_builder, extractor, "HC sections")
            return result["items"]

    async def list_help_center_categories(self, locale: str = "zh-cn") -> List[Dict[str, Any]]:
        """List Help Center categories."""
        async with self._semaphore:
            def url_builder(loc):
                return f"{self.base_url}/api/v2/help_center/{loc}/categories.json"

            def extractor(payload):
                return [{
                    "id": c["id"],
                    "name": c["name"],
                    "description": c.get("description", ""),
                } for c in payload.get("categories", [])]

            result = await self._with_fallback(locale, url_builder, extractor, "HC categories")
            return result["items"]

    async def get_article_comments(self, article_id: str, locale: str = "zh-cn") -> Dict[str, Any]:
        """Get article comments."""
        async with self._semaphore:
            alt_locale = "en-us" if locale == "zh-cn" else "zh-cn"

            for loc in (locale, alt_locale):
                try:
                    session = await self._ensure_session(loc)
                    comments = []
                    url = f"{self.base_url}/api/v2/help_center/articles/{article_id}/comments.json"

                    while url:
                        payload = await self._get_json(session, url)
                        for c in payload.get("comments", []):
                            comments.append({
                                "id": c["id"],
                                "author": (c.get("author", {}) or {}).get("name", "?"),
                                "body": _html_to_text(c.get("body", "")),
                                "created_at": c.get("created_at"),
                            })
                        url = payload.get("next_page")

                    if comments:
                        return {
                            "success": True,
                            "article_id": article_id,
                            "comments": comments,
                            "count": len(comments),
                        }
                except Exception as e:
                    _log("WARNING", f"Comments {article_id} locale={loc}: {e}")

            return {"success": True, "article_id": article_id, "comments": [], "count": 0}


# Global instance
forum_client = ForumClient()


if __name__ == "__main__":
    print("WorldQuant BRAIN Forum Functions", file=sys.stderr)
    email = os.getenv("CREDENTIALS_EMAIL")
    password = os.getenv("CREDENTIALS_PASSWORD")
    if email and password:
        try:
            result = asyncio.run(forum_client.read_forum_post("36371597455127"))
            print(result)
        except Exception as e:
            _log("ERROR", f"Test failed: {e}")
    else:
        _log("INFO", "Set CREDENTIALS_EMAIL and CREDENTIALS_PASSWORD for testing")
