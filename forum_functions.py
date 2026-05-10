#!/usr/bin/env python3
"""
WorldQuant BRAIN Forum Functions - Hybrid SSO+API Version
Uses Playwright ONCE for Zendesk SSO login to obtain cookies,
then uses Zendesk REST API for all subsequent requests.
Browser is only launched when cookies expire or on first call.
"""

import asyncio
import re
import sys
import time
import io
from datetime import datetime
from typing import Dict, Any, List, Optional

import requests
from bs4 import BeautifulSoup

# Constants
ZENDESK_BASE_URL = "https://support.worldquantbrain.com"
GLOSSARY_ARTICLE_ID = "4902349883927"
COOKIE_TTL = 3600  # 1 hour
DEFAULT_PAGE_SIZE = 100
SNIPPET_LENGTH_ARTICLE = 300
SNIPPET_LENGTH_POST = 500

# Global cookie cache
_cookie_cache = None
_cookie_timestamp = 0


def log(message: str, level: str = "INFO"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", file=sys.stderr)


def _html_to_text(html: str, max_length: int = 0) -> str:
    """Convert HTML to plain text, optionally truncating."""
    if not html:
        return ""
    text = BeautifulSoup(html, 'html.parser').get_text(strip=True)
    if max_length and len(text) > max_length:
        return text[:max_length]
    return text


def _parse_glossary_terms(html_content: str) -> List[Dict[str, str]]:
    """Parse glossary terms from Zendesk article HTML.
    
    The glossary uses H3/H4 tags for terms, followed by definition paragraphs.
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    
    terms = []
    
    # Find all h3 and h4 tags (term headers)
    headers = soup.find_all(['h3', 'h4'])
    
    for header in headers:
        term = header.get_text(strip=True)
        
        # Skip navigation headers (single letters like "A", "B", "C")
        if len(term) <= 2 and term.isalpha():
            continue
            
        # Skip the main title
        if 'glossary' in term.lower() and len(term) > 50:
            continue
        
        # Get the next sibling elements until next header
        definition_parts = []
        current = header.find_next_sibling()
        
        while current and current.name not in ['h3', 'h4']:
            if current.name == 'p':
                text = current.get_text(strip=True)
                if text and not text.startswith('A - B - C'):  # Skip navigation
                    definition_parts.append(text)
            current = current.find_next_sibling()
        
        definition = ' '.join(definition_parts)
        
        # Filter out invalid entries
        if (term and definition and 
            len(term) > 1 and len(definition) > 10 and
            "ago" not in definition and 
            "minute read" not in definition and
            not term.startswith('http')):
            terms.append({"term": term, "definition": definition})
    
    return terms


def _build_search_result(item: Dict, result_type: str, snippet_length: int = 300) -> Dict:
    """Build a standardized search result dictionary."""
    return {
        'title': item.get('title', ''),
        'link': item.get('html_url', ''),
        'snippet': _html_to_text(item.get('body', ''), snippet_length),
        'votes': item.get('vote_sum', 0),
        'comments': item.get('comment_count', 0),
        'author': (item.get('author', {}) or {}).get('name', 'Unknown'),
        'date': item.get('created_at', ''),
        'id': str(item.get('id', '')),
        'result_type': result_type,
    }


class ForumClient:
    """Forum client: Playwright SSO once, then Zendesk REST API for all calls."""

    def __init__(self):
        self.base_url = ZENDESK_BASE_URL
        self.api_base = f"{self.base_url}/api/v2"
        self._session = None

    async def _get_session(self, email: str = None, password: str = None) -> requests.Session:
        """Get or create a Zendesk-authenticated requests.Session.
        Uses Playwright once to SSO login, then caches cookies."""
        global _cookie_cache, _cookie_timestamp

        now = time.time()
        if self._session and (now - _cookie_timestamp) < COOKIE_TTL:
            return self._session

        log("Launching browser for Zendesk SSO login...", "INFO")
        
        from playwright.async_api import async_playwright
        from _models import load_config
        
        # Use provided credentials or load from config
        if not email or not password:
            config = load_config()
            creds = config.get("credentials", {})
            email = email or creds.get("email")
            password = password or creds.get("password")
        
        if not email or not password:
            raise Exception("Credentials not found. Please provide email and password or configure in config.")

        async with async_playwright() as p:
            # Auto-detect available browser
            browser_type = await self._detect_browser(p)
            browser = await browser_type.launch(headless=True, args=['--no-sandbox'])
            context = await browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0')

            page = await context.new_page()

            log("Navigating to Zendesk Help Center for SSO...", "INFO")
            
            # Navigate to Zendesk login page
            await page.goto(f"{self.base_url}/hc/en-us", wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            
            # Check if already logged in
            if await self._is_logged_in(page):
                log("Already logged in to Zendesk.", "SUCCESS")
            else:
                log("Performing SSO login...", "INFO")
                await self._perform_sso_login(page, email, password)

            log("Extracting Zendesk cookies from browser...", "INFO")
            browser_cookies = await context.cookies()

            sess = requests.Session()
            sess.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0',
                'Accept': 'application/json',
            })
            for c in browser_cookies:
                if 'worldquantbrain' in c.get('domain', ''):
                    sess.cookies.set(c['name'], c['value'], domain=c.get('domain', ''))

            await browser.close()
            log(f"Zendesk session ready with {len(sess.cookies)} cookies.", "SUCCESS")

        self._session = sess
        _cookie_cache = sess
        _cookie_timestamp = time.time()
        return sess

    async def _detect_browser(self, playwright):
        """Auto-detect available browser (msedge, chrome, or chromium)."""
        import shutil
        
        # Check for Edge first (Windows default)
        if shutil.which("msedge"):
            log("Using Microsoft Edge browser.", "INFO")
            return playwright.chromium
        
        # Check for Chrome
        if shutil.which("chrome") or shutil.which("google-chrome"):
            log("Using Chrome browser.", "INFO")
            return playwright.chromium
        
        # Fallback to default chromium
        log("Using default Chromium browser.", "INFO")
        return playwright.chromium

    async def _is_logged_in(self, page) -> bool:
        """Check if user is already logged in to Zendesk."""
        try:
            # Look for user menu or sign out link
            user_menu = await page.query_selector('a[href*="sign_out"], .user-menu, .header-profile')
            return user_menu is not None
        except Exception:
            return False

    async def _perform_sso_login(self, page, email: str, password: str):
        """Perform SSO login through BRAIN platform."""
        try:
            # Click sign in button if present
            sign_in_btn = await page.query_selector('a[href*="sign_in"], .sign-in, #sign-in')
            if sign_in_btn:
                await sign_in_btn.click()
                await page.wait_for_timeout(2000)
            
            # Fill in credentials if on login page
            email_field = await page.query_selector('input[type="email"], input[name="email"], #email')
            if email_field:
                await email_field.fill(email)
                await page.wait_for_timeout(500)
            
            password_field = await page.query_selector('input[type="password"], input[name="password"], #password')
            if password_field:
                await password_field.fill(password)
                await page.wait_for_timeout(500)
            
            # Click submit
            submit_btn = await page.query_selector('button[type="submit"], input[type="submit"], .submit')
            if submit_btn:
                await submit_btn.click()
                await page.wait_for_timeout(5000)
            
            # Wait for navigation to complete (with shorter timeout)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass  # Continue even if timeout
            await page.wait_for_timeout(3000)
            
            log("SSO login completed.", "SUCCESS")
        except Exception as e:
            log(f"SSO login error: {e}", "ERROR")
            raise

    async def get_glossary_terms(self, email: str = None, password: str = None) -> List[Dict[str, str]]:
        """Extract glossary terms via Zendesk Help Center article API."""
        session = await self._get_session(email, password)

        for locale in ["en-us", "zh-cn", ""]:
            try:
                prefix = f"/{locale}" if locale else ""
                url = f"{self.api_base}/help_center{prefix}/articles/{GLOSSARY_ARTICLE_ID}.json"
                log(f"Fetching glossary: {url}", "INFO")
                resp = session.get(url, timeout=30)
                if resp.status_code == 200:
                    html_body = resp.json().get("article", {}).get("body", "")
                    if html_body:
                        terms = _parse_glossary_terms(html_body)
                        log(f"Extracted {len(terms)} glossary terms", "SUCCESS")
                        return terms
                log(f"Glossary locale '{locale}' status: {resp.status_code}", "WARN")
            except Exception as e:
                log(f"Glossary locale '{locale}' error: {e}", "WARN")

        raise Exception("Failed to fetch glossary from all locale variants")

    async def search_forum_posts(
        self,
        email: str = None, password: str = None,
        search_query: str = "", max_results: int = 50, locale: str = "en-us"
    ) -> Dict[str, Any]:
        """Search Help Center articles + community posts via Zendesk API.
        
        Note: Zendesk search API may not be available, so we fetch and filter posts locally.
        """
        session = await self._get_session(email, password)
        all_results = []
        per_page = min(max_results, DEFAULT_PAGE_SIZE)
        query_lower = search_query.lower() if search_query else ""

        def matches_query(title: str, body: str) -> bool:
            if not query_lower:
                return True
            return query_lower in title.lower() or query_lower in body.lower()

        try:
            # Search articles
            page = 1
            while len(all_results) < max_results:
                url = f"{self.api_base}/help_center/{locale}/articles.json"
                params = {"page": page, "per_page": per_page}
                log(f"Fetching articles: page={page}", "INFO")
                resp = session.get(url, params=params, timeout=30)
                if resp.status_code != 200:
                    break
                
                articles = resp.json().get("articles", [])
                if not articles:
                    break

                for a in articles:
                    if matches_query(a.get("title", ""), a.get("body", "")):
                        all_results.append(_build_search_result(a, 'article', SNIPPET_LENGTH_ARTICLE))

                log(f"  Articles page {page}: {len(articles)} fetched, matched={len([r for r in all_results if r['result_type'] == 'article'])}", "INFO")
                if len(articles) < per_page:
                    break
                page += 1
                await asyncio.sleep(0.3)

            # Search community posts
            comm_page = 1
            comm_matched = 0
            while len(all_results) < max_results * 2:
                url = f"{self.api_base}/help_center/community/posts.json"
                params = {"page": comm_page, "per_page": per_page}
                resp = session.get(url, params=params, timeout=30)
                if resp.status_code != 200:
                    log(f"Community posts API returned {resp.status_code}", "WARN")
                    break
                
                posts = resp.json().get("posts", [])
                if not posts:
                    break
                
                for p in posts:
                    if matches_query(p.get("title", ""), p.get("body", "")):
                        all_results.append(_build_search_result(p, 'community_post', SNIPPET_LENGTH_POST))
                        comm_matched += 1
                
                log(f"  Posts page {comm_page}: {len(posts)} fetched, matched={comm_matched}", "INFO")
                if len(posts) < per_page:
                    break
                comm_page += 1
                await asyncio.sleep(0.3)

            log(f"Search '{search_query}': {len(all_results)} total results", "SUCCESS")
            return {"success": True, "results": all_results[:max_results], "total_found": len(all_results)}

        except Exception as e:
            log(f"Search failed: {e}", "ERROR")
            raise

    async def read_full_forum_post(
        self,
        email: str = None, password: str = None,
        post_url_or_id: str = "", include_comments: bool = True
    ) -> Dict[str, Any]:
        """Read a full forum post via Zendesk API."""
        session = await self._get_session(email, password)
        
        # Parse post ID and type from URL or ID string
        post_id = post_url_or_id
        is_article = '/articles/' in post_url_or_id

        if post_url_or_id.startswith('http'):
            if m := re.search(r'/articles/(\d+)', post_url_or_id):
                post_id = m.group(1)
                is_article = True
            elif m := re.search(r'/posts/(\d+)', post_url_or_id):
                post_id = m.group(1)

        try:
            # Build URL based on content type
            endpoint = f"help_center/{'articles' if is_article else 'community/posts'}/{post_id}.json"
            url = f"{self.api_base}/{endpoint}"

            log(f"Reading post: {url}", "INFO")
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            key = "article" if is_article else "post"
            item = data.get(key, {})
            post_data = {
                'title': item.get('title', 'Unknown Title'),
                'author': (item.get('author', {}) or {}).get('name', 'Unknown'),
                'body': _html_to_text(item.get('body', '')),
                'details': {
                    'votes': item.get('vote_sum', 0),
                    'date': item.get('created_at', 'Unknown Date')
                }
            }

            # Fetch comments
            comments = []
            if include_comments:
                comments_url = f"{self.api_base}/help_center/{'articles' if is_article else 'community/posts'}/{post_id}/comments.json"
                page = 1
                while True:
                    resp_c = session.get(comments_url, params={"page": page, "per_page": DEFAULT_PAGE_SIZE}, timeout=30)
                    if resp_c.status_code != 200:
                        break
                    clist = resp_c.json().get("comments", [])
                    if not clist:
                        break
                    for c in clist:
                        comments.append({
                            'author': (c.get('author', {}) or {}).get('name', 'Unknown'),
                            'body': _html_to_text(c.get('body', '')),
                            'date': c.get('created_at', 'Unknown Date')
                        })
                    if len(clist) < DEFAULT_PAGE_SIZE:
                        break
                    page += 1

            log(f"Read post '{post_data['title'][:50]}' + {len(comments)} comments", "SUCCESS")
            return {"success": True, "post": post_data, "comments": comments, "total_comments": len(comments)}

        except Exception as e:
            log(f"Read post failed: {e}", "ERROR")
            raise

    async def list_articles(
        self,
        email: str = None, password: str = None,
        locale: str = "en-us", max_results: int = 100
    ) -> Dict[str, Any]:
        """List all Help Center articles."""
        session = await self._get_session(email, password)
        all_articles = []
        page = 1
        per_page = min(max_results, DEFAULT_PAGE_SIZE)

        try:
            while len(all_articles) < max_results:
                url = f"{self.api_base}/help_center/{locale}/articles.json"
                params = {"page": page, "per_page": per_page, "sort_by": "created_at", "sort_order": "desc"}
                resp = session.get(url, params=params, timeout=30)
                if resp.status_code != 200:
                    break
                
                articles = resp.json().get("articles", [])
                if not articles:
                    break
                
                for a in articles:
                    all_articles.append({
                        'id': a.get('id'),
                        'title': a.get('title', ''),
                        'url': a.get('html_url', ''),
                        'snippet': _html_to_text(a.get('body', ''), SNIPPET_LENGTH_ARTICLE),
                        'votes': a.get('vote_sum', 0),
                        'comments': a.get('comment_count', 0),
                        'created_at': a.get('created_at', ''),
                        'updated_at': a.get('updated_at', ''),
                    })
                
                if len(articles) < per_page:
                    break
                page += 1
                await asyncio.sleep(0.2)

            log(f"Listed {len(all_articles)} articles", "SUCCESS")
            return {"success": True, "results": all_articles[:max_results], "total_found": len(all_articles)}

        except Exception as e:
            log(f"List articles failed: {e}", "ERROR")
            raise

    async def list_categories(self, email: str = None, password: str = None, locale: str = "en-us") -> Dict[str, Any]:
        """List all Help Center categories."""
        session = await self._get_session(email, password)

        try:
            url = f"{self.api_base}/help_center/{locale}/categories.json"
            log(f"Listing categories: {url}", "INFO")
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            categories = resp.json().get("categories", [])

            result = [{
                'id': c.get('id'),
                'name': c.get('name', ''),
                'description': c.get('description', ''),
                'url': c.get('html_url', ''),
                'position': c.get('position', 0),
            } for c in categories]

            log(f"Listed {len(result)} categories", "SUCCESS")
            return {"success": True, "results": result, "total_found": len(result)}

        except Exception as e:
            log(f"List categories failed: {e}", "ERROR")
            raise

    async def vote_post(self, email: str = None, password: str = None, post_id: str = "", vote_type: str = "up") -> Dict[str, Any]:
        """Vote on a community post.

        Args:
            email: Your BRAIN platform email (optional if in config)
            password: Your BRAIN platform password (optional if in config)
            post_id: The post ID
            vote_type: 'up' or 'down'
        """
        session = await self._get_session(email, password)

        try:
            url = f"{self.api_base}/help_center/community/posts/{post_id}/vote.json"
            payload = {"vote": {"value": 1 if vote_type == "up" else -1}}

            log(f"Voting {vote_type} on post {post_id}", "INFO")
            resp = session.post(url, json=payload, timeout=30)
            resp.raise_for_status()

            log(f"Successfully voted {vote_type} on post {post_id}", "SUCCESS")
            return {"success": True, "post_id": post_id, "vote_type": vote_type}

        except Exception as e:
            log(f"Vote failed: {e}", "ERROR")
            raise


# Global client instance
forum_client = ForumClient()

if __name__ == "__main__":
    print("Forum Functions - Hybrid SSO+API Version.", file=sys.stderr)
