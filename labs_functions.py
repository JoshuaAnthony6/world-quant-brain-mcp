#!/usr/bin/env python3
"""
BRAIN Labs client for WorldQuant BRAIN, using Playwright.

BRAIN Labs is delivered as an AWS WorkSpaces Web (pixel-streamed) remote browser:
clicking "Sign in to BRAIN Labs" calls POST /authentication/brainlabs which returns
a SAML assertion that federates into AWS Cognito and opens a *.workspaces-web.com
session whose deepLinks param points at the internal JupyterLab
(https://api.worldquantbrain.com/labs/?token=...). That internal JupyterLab is only
reachable from inside the WorkSpaces VPC, so there is no DOM / Jupyter REST API to
drive from outside. What we *can* do programmatically is perform the two-step
sign-in (platform + Labs password) and hand back the live WorkSpaces deepLink URL
for a human (or a WorkSpaces-side runner) to open, plus emit/ingest helpers for the
Labs data-analysis script.

This client mirrors forum_functions.ForumClient: it shares the authenticated
brain_client session, launches Playwright, and serializes every Labs operation
through a single concurrency lock (LABS_MAX_CONCURRENCY, default 1) because a Labs
account has exactly one interactive session and concurrent sign-ins invalidate each
other's tokens.
"""

import asyncio
import os
import sys
import json
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover - environment dependent
    print("[ERROR] Playwright not installed. Please install it with: pip install playwright", file=sys.stderr)
    async_playwright = None


def log(message: str, level: str = "INFO"):
    print(f"[{level}] {message}", file=sys.stderr)


# Reuse the same browser discovery logic the forum client uses.
try:
    from forum_functions import get_browser_path
except Exception:  # pragma: no cover - fallback if import order differs
    def get_browser_path():
        return None


def _brain_client():
    """Import the shared authenticated BRAIN client lazily (avoids circular import)."""
    try:
        from main import brain_client
    except ImportError:
        current_dir = Path(__file__).parent
        if str(current_dir) not in sys.path:
            sys.path.insert(0, str(current_dir))
        from main import brain_client
    return brain_client


def decode_deeplink(workspaces_url: str) -> Dict[str, Optional[str]]:
    """Extract and decode the labs token URL from a WorkSpaces deepLink URL.

    workspaces_url looks like:
        https://<id>.workspaces-web.com/?deepLinks=https://api.worldquantbrain.com/labs/?token=<double-url-encoded>
    Returns the workspaces portal, the (single-encoded) labs URL, and the raw token.
    """
    out: Dict[str, Optional[str]] = {
        "workspaces_url": workspaces_url,
        "labs_url": None,
        "token": None,
    }
    try:
        parsed = urllib.parse.urlparse(workspaces_url)
        qs = urllib.parse.parse_qs(parsed.query)
        deeplinks = qs.get("deepLinks", [None])[0]
        if not deeplinks:
            return out
        # deepLinks is single-url-encoded inside the query; parse_qs already decoded it
        # once, leaving https://api.worldquantbrain.com/labs/?token=<still-encoded>.
        out["labs_url"] = deeplinks
        labs_parsed = urllib.parse.urlparse(deeplinks)
        labs_qs = urllib.parse.parse_qs(labs_parsed.query)
        out["token"] = labs_qs.get("token", [None])[0]
    except Exception as exc:  # pragma: no cover - defensive
        log(f"deepLink decode warning: {exc}", "WARNING")
    return out


class LabsClient:
    """BRAIN Labs client (Playwright + single-concurrency lock)."""

    def __init__(self):
        # Load .env so env overrides work even outside the server process.
        try:
            from dotenv import load_dotenv, find_dotenv
            env_path = find_dotenv(usecwd=True)
            if env_path:
                load_dotenv(env_path, override=False)
            else:
                candidate = Path(__file__).parent / ".env"
                if candidate.exists():
                    load_dotenv(candidate, override=False)
        except Exception:
            pass

        self.platform_url = os.getenv("LABS_PLATFORM_URL", "https://platform.worldquantbrain.com")
        self.brainlabs_path = "/profile/account/brainlabs"
        try:
            self.selector_timeout_ms = int(float(os.getenv("LABS_SETTINGS_TIMEOUT", "30")) * 1000)
        except Exception:
            self.selector_timeout_ms = 30000
        self.headless = str(os.getenv("LABS_SETTINGS_HEADLESS", os.getenv("FORUM_SETTINGS_HEADLESS", "true"))).lower() in ("1", "true", "yes", "on")
        try:
            self.max_concurrency = max(1, int(os.getenv("LABS_MAX_CONCURRENCY", "1")))
        except Exception:
            self.max_concurrency = 1
        # The single Labs operation lock: only one Labs sign-in/op may run at a time.
        self._labs_operation_semaphore = asyncio.Semaphore(self.max_concurrency)

    # -- browser context (mirrors ForumClient._get_browser_context) ---------
    async def _get_browser_context(self, p: Any, email: str, password: str, timeout_seconds: int = 30):
        """Authenticate the shared BRAIN session and return (browser, context)."""
        brain_client = _brain_client()
        try:
            await asyncio.wait_for(brain_client.ensure_authenticated(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            log(f"Authentication check timed out after {timeout_seconds}s, proceeding anyway.", "WARNING")
        except Exception:
            log("Authenticating with BRAIN platform...", "INFO")
            try:
                auth_result = await asyncio.wait_for(
                    brain_client.authenticate(email, password),
                    timeout=timeout_seconds,
                )
                if auth_result.get("status") != "authenticated":
                    raise Exception("BRAIN platform authentication failed.")
            except asyncio.TimeoutError:
                log(f"Authentication timed out after {timeout_seconds}s, continuing with partial session.", "WARNING")

        browser_path = get_browser_path()
        browser_args = [
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--disable-sync",
            "--no-first-run",
        ]
        try:
            if browser_path and os.path.exists(browser_path):
                browser = await asyncio.wait_for(
                    p.chromium.launch(executable_path=browser_path, args=browser_args, timeout=timeout_seconds * 1000),
                    timeout=timeout_seconds + 10,
                )
            else:
                browser = await asyncio.wait_for(
                    p.chromium.launch(headless=self.headless, args=browser_args, timeout=timeout_seconds * 1000),
                    timeout=timeout_seconds + 10,
                )
        except asyncio.TimeoutError:
            raise Exception(f"Browser launch timed out after {timeout_seconds + 10}s")

        try:
            context = await asyncio.wait_for(
                browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"
                ),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            await browser.close()
            raise Exception("Browser context creation timed out")

        # Transfer the authenticated BRAIN session cookies into the browser so the
        # platform SPA recognises us without re-entering the email login.
        try:
            cookies = brain_client.session.cookies
            playwright_cookies = []
            for cookie in cookies:
                cookie_dict = {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain if cookie.domain else ".worldquantbrain.com",
                    "path": cookie.path if cookie.path else "/",
                    "secure": cookie.secure if hasattr(cookie, "secure") else True,
                    "httpOnly": "HttpOnly" in cookie._rest if hasattr(cookie, "_rest") else False,
                    "sameSite": "Lax",
                }
                if hasattr(cookie, "expires") and cookie.expires:
                    cookie_dict["expires"] = cookie.expires
                playwright_cookies.append(cookie_dict)
            if playwright_cookies:
                await asyncio.wait_for(context.add_cookies(playwright_cookies), timeout=10)
        except Exception as e:
            log(f"Cookie transfer warning (continuing): {str(e)}", "WARNING")

        return browser, context

    async def _ui_sign_in(self, page, email: str, password: str):
        """Fallback platform email/password sign-in when the cookie session is not honoured."""
        log("Performing platform UI sign-in...", "INFO")
        await page.wait_for_selector("input[type='email'], textbox", timeout=self.selector_timeout_ms)
        await page.get_by_role("textbox", name="Email").fill(email)
        await page.get_by_role("textbox", name="Password").fill(password)
        await page.get_by_role("button", name="Sign In").click()
        await page.wait_for_load_state("networkidle", timeout=self.selector_timeout_ms)

    async def open_labs_session(self, email: str, password: str, timeout_seconds: int = 60) -> Dict[str, Any]:
        """Sign in to BRAIN Labs and return the live WorkSpaces deepLink session URL.

        Serialized by the single Labs concurrency lock. Returns a dict with the
        WorkSpaces portal URL, the decoded internal labs URL, and the token.
        """
        if async_playwright is None:
            raise ImportError("Playwright not available. Please install it with: pip install playwright")

        async with self._labs_operation_semaphore:
            async with async_playwright() as p:
                browser = None
                try:
                    browser, context = await self._get_browser_context(p, email, password, timeout_seconds)
                    page = await context.new_page()

                    labs_url = self.platform_url + self.brainlabs_path
                    log(f"Navigating to BRAIN Labs account page: {labs_url}", "INFO")
                    await page.goto(labs_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)

                    # If the cookie session was not honoured we land on /sign-in.
                    if "/sign-in" in page.url:
                        await self._ui_sign_in(page, email, password)
                        await page.goto(labs_url, wait_until="domcontentloaded", timeout=timeout_seconds * 1000)

                    # Fill the BRAIN Labs password and submit, capturing the popup tab
                    # that carries the WorkSpaces deepLink.
                    pw_box = page.get_by_role("textbox", name="Password*")
                    await pw_box.wait_for(timeout=self.selector_timeout_ms)
                    await pw_box.fill(password)

                    async with context.expect_page(timeout=timeout_seconds * 1000) as popup_info:
                        await page.get_by_role("button", name="Sign in to BRAIN Labs").click()
                    popup = await popup_info.value

                    # The popup URL is the WorkSpaces deepLink; poll until it materialises.
                    deeplink = ""
                    for _ in range(int(timeout_seconds * 2)):
                        url = popup.url or ""
                        if "workspaces-web.com" in url and "deepLinks" in url:
                            deeplink = url
                            break
                        await asyncio.sleep(0.5)
                    if not deeplink:
                        deeplink = popup.url or ""

                    try:
                        await popup.close()
                    except Exception:
                        pass

                    if "workspaces-web.com" not in deeplink:
                        raise RuntimeError(f"Did not capture a WorkSpaces deepLink (got: {deeplink!r})")

                    decoded = decode_deeplink(deeplink)
                    log("Captured BRAIN Labs WorkSpaces deepLink.", "SUCCESS")
                    return {
                        "status": "ok",
                        "workspaces_url": decoded["workspaces_url"],
                        "labs_url": decoded["labs_url"],
                        "token": decoded["token"],
                        "note": (
                            "BRAIN Labs runs inside an AWS WorkSpaces Web pixel-stream. Open workspaces_url "
                            "in a browser to reach JupyterLab; the internal labs_url is only reachable from "
                            "within the WorkSpaces session."
                        ),
                    }
                except Exception as e:
                    log(f"open_labs_session failed: {str(e)}", "ERROR")
                    raise
                finally:
                    if browser:
                        await browser.close()
                        log("Browser closed.", "INFO")

    # -- data-analysis helpers (run under the same lock) --------------------
    def _labs_agent_path(self) -> Optional[Path]:
        candidate = os.getenv("LABS_AGENT_SCRIPT")
        if candidate and Path(candidate).exists():
            return Path(candidate)
        # Fall back to the engine bundled next to this module.
        bundled = Path(__file__).parent / "labs_data_analysis_agent.py"
        if bundled.exists():
            return bundled
        return None

    async def emit_labs_script(
        self,
        dataset_id: str,
        fields: List[str],
        region: str = "USA",
        universe: str = "TOP3000",
        delay: int = 1,
        labs_output: str = "/tmp/labs_data_analysis_result.json",
    ) -> Dict[str, Any]:
        """Generate the pasteable BRAIN Labs data-analysis script.

        Requires LABS_AGENT_SCRIPT to point at labs_data_analysis_agent.py (the
        analysis engine). The emitted script is meant to be pasted into the Labs
        JupyterLab (which can `from brain import Brain`), since raw panel data is
        only available inside Labs.
        """
        async with self._labs_operation_semaphore:
            agent = self._labs_agent_path()
            if agent is None:
                return {
                    "error": "LABS_AGENT_SCRIPT not configured. Set it to the path of "
                    "labs_data_analysis_agent.py to emit Labs scripts.",
                }
            body = agent.read_text()
            body = body.rsplit('if __name__ == "__main__":\n    main()\n', 1)[0].rstrip()
            invocation = (
                "\n\n# Brain Labs entry point generated by emit_labs_script.\n"
                'if __name__ == "__main__":\n'
                "    main([\n"
                '        "run-labs",\n'
                f"        \"--dataset-id\", {dataset_id!r},\n"
                f"        \"--region\", {region!r},\n"
                f"        \"--universe\", {universe!r},\n"
                f"        \"--delay\", {str(delay)!r},\n"
                f"        \"--output\", {labs_output!r},\n"
                f"        *{fields!r},\n"
                "    ])\n"
            )
            script = body + invocation
            return {"status": "ok", "script": script, "labs_output": labs_output}

    async def ingest_labs_result(self, result_json: str) -> Dict[str, Any]:
        """Parse a Labs data-analysis JSON result (string or file path) and return it."""
        async with self._labs_operation_semaphore:
            text = result_json
            try:
                if os.path.exists(result_json):
                    text = Path(result_json).read_text()
            except Exception:
                pass
            try:
                data = json.loads(text)
            except json.JSONDecodeError as e:
                return {"error": f"Could not parse Labs result JSON: {e}"}
            return {"status": "ok", "result": data}


# Singleton mirroring forum_functions.forum_client.
labs_client = LabsClient()
