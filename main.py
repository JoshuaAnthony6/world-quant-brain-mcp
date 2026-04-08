#!/usr/bin/env python3
"""
WorldQuant BRAIN MCP Server - Python Version
A comprehensive Model Context Protocol (MCP) server for WorldQuant BRAIN platform integration.
"""

import json
import time
import asyncio
import logging
from typing import Dict, List, Optional, Any, Union, Tuple
import re
import base64
from bs4 import BeautifulSoup
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import os
import sys
from pathlib import Path
from time import sleep
from urllib.parse import urljoin
import redis
import hashlib
import math

import requests
import pandas as pd
import zlib
import msgpack
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, EmailStr

# Import the new forum client
from forum_functions import forum_client

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Pydantic models for type safety
class AuthCredentials(BaseModel):
    email: EmailStr
    password: str

class SimulationSettings(BaseModel):
    instrumentType: str = "EQUITY"
    region: str = "USA"
    universe: str = "TOP3000"
    delay: int = 1
    decay: float = 0.0
    neutralization: str = "NONE"
    truncation: float = 0.0
    pasteurization: str = "ON"
    unitHandling: str = "VERIFY"
    nanHandling: str = "OFF"
    language: str = "FASTEXPR"
    visualization: bool = True
    testPeriod: str = "P0Y0M"
    selectionHandling: str = "POSITIVE"
    selectionLimit: int = 1000
    maxTrade: str = "OFF"
    componentActivation: str = "IS"

class SimulationData(BaseModel):
    type: str = "REGULAR"  # "REGULAR" or "SUPER"
    settings: SimulationSettings
    regular: Optional[str] = None
    combo: Optional[str] = None
    selection: Optional[str] = None

class BrainApiClient:
    """WorldQuant BRAIN API client with comprehensive functionality."""
    
    def __init__(self):
        # Best-effort: load .env early so env overrides are available here
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
            # Fallback: simple parser
            try:
                candidate = Path(__file__).parent / ".env"
                if candidate.exists():
                    for line in candidate.read_text().splitlines():
                        line = line.strip()
                        if not line or line.startswith('#') or '=' not in line:
                            continue
                        k, v = line.split('=', 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        os.environ.setdefault(k, v)
            except Exception:
                pass

        self.base_url = "https://api.worldquantbrain.com"
        self.session = requests.Session()
        self.auth_credentials = None
        self.is_authenticating = False
        self._request_semaphore = asyncio.Semaphore(int(os.environ.get("BRAIN_MAX_CONCURRENCY", "8")))
        self._session_lock = asyncio.Lock()
        self._auth_lock = asyncio.Lock()
        # Allow timeout override via env (e.g., API_SETTINGS_TIMEOUT)
        try:
            self._default_timeout_seconds = int(os.environ.get("API_SETTINGS_TIMEOUT", "30"))
        except Exception:
            self._default_timeout_seconds = 30
        self._create_simulation_semaphore = asyncio.Semaphore(int(os.environ.get("BRAIN_CREATE_SIMULATION_MAX_CONCURRENCY", "6")))
        self._forum_rate_limit_lock = asyncio.Lock()
        self._forum_rate_limit_until = 0.0
        
        # Configure session
        self.session.timeout = self._default_timeout_seconds
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        # Load OS/IS Sharpe ratio data for datafield quality filtering
        self._isos_data = {}
        try:
            info_data_path = Path(__file__).parent / 'config' / 'info_data.bin'
            if info_data_path.exists():
                with open(info_data_path, 'rb') as f:
                    self._isos_data = msgpack.unpackb(zlib.decompress(f.read()), raw=False)
                self.log(f"Loaded OS/IS Sharpe data: {len(self._isos_data)} region_delay entries", "INFO")
            else:
                self.log(f"OS/IS Sharpe data file not found at {info_data_path}, sharpe filtering disabled", "WARNING")
        except Exception as e:
            self.log(f"Failed to load OS/IS Sharpe data: {str(e)}, sharpe filtering disabled", "WARNING")

        # Initialize Redis connection
        try:
            redis_host = os.environ.get('REDIS_HOST', 'localhost')
            try:
                redis_port = int(os.environ.get('REDIS_PORT', str(6379)))
            except Exception:
                redis_port = 6379

            self.redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=0,
                decode_responses=True,
                socket_connect_timeout=5
            )
            # Test connection
            self.redis_client.ping()
            self.log("Redis connection established", "INFO")
        except Exception as e:
            self.log(f"Redis connection failed: {str(e)}, caching disabled", "WARNING")
            self.redis_client = None
    
    def log(self, message: str, level: str = "INFO"):
        """Log messages to stderr to avoid MCP protocol interference."""
        print(f"[{level}] {message}", file=sys.stderr)
    
    def _to_absolute_url(self, url: str) -> str:
        if not url:
            return url
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return urljoin(self.base_url, url)

    def _generate_cache_key(self, prefix: str, params: dict) -> str:
        """Generate a cache key from prefix and parameters."""
        # Sort params to ensure consistent key generation
        sorted_params = sorted(params.items())
        param_str = json.dumps(sorted_params, sort_keys=True)
        hash_str = hashlib.md5(param_str.encode()).hexdigest()
        return f"{prefix}:{hash_str}"
    
    def _get_cached_data(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Get data from Redis cache."""
        if not self.redis_client:
            return None
        try:
            cached = self.redis_client.get(cache_key)
            if cached:
                self.log(f"Cache hit for key: {cache_key}", "INFO")
                return json.loads(cached)
        except Exception as e:
            self.log(f"Cache read error: {str(e)}", "WARNING")
        return None
    
    def _set_cached_data(self, cache_key: str, data: Dict[str, Any], ttl: int = 604800):
        """Set data in Redis cache with TTL (default 1 week = 604800 seconds)."""
        if not self.redis_client:
            return
        try:
            self.redis_client.setex(cache_key, ttl, json.dumps(data))
            self.log(f"Cached data with key: {cache_key}, TTL: {ttl}s", "INFO")
        except Exception as e:
            self.log(f"Cache write error: {str(e)}", "WARNING")
    
    async def _rate_limit_forum_op(self, op_name: str) -> Optional[Dict[str, Any]]:
        if self.redis_client:
            try:
                lock_key = "rate_limit:forum_ops"
                if not self.redis_client.set(lock_key, "locked", ex=60, nx=True):
                    ttl = self.redis_client.ttl(lock_key)
                    if not isinstance(ttl, int) or ttl < 0:
                        ttl = 60
                    return {
                        'status': 'rate_limited',
                        'message': f"Rate limit exceeded. Please wait {ttl} seconds before trying again.",
                        'retry_after': ttl,
                    }
            except Exception as e:
                self.log(f"Rate limiting for {op_name} failed, falling back to local limiter: {str(e)}", "WARNING")

        async with self._forum_rate_limit_lock:
            now = time.time()
            until = float(self._forum_rate_limit_until)
            if now < until:
                ttl = int(until - now)
                if ttl < 0:
                    ttl = 0
                return {
                    'status': 'rate_limited',
                    'message': f"Rate limit exceeded. Please wait {ttl} seconds before trying again.",
                    'retry_after': ttl,
                }
            self._forum_rate_limit_until = now + 60
            return None
    
    async def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Run blocking requests I/O in a worker thread to avoid blocking the asyncio event loop."""
        absolute_url = self._to_absolute_url(url)
        timeout = kwargs.pop("timeout", self._default_timeout_seconds)
        # Add extra buffer for asyncio timeout to catch stuck threads
        asyncio_timeout = timeout + 10
        
        async with self._request_semaphore:
            async with self._session_lock:
                try:
                    # Wrap asyncio.to_thread with wait_for to prevent infinite hangs
                    return await asyncio.wait_for(
                        asyncio.to_thread(
                            self.session.request,
                            method,
                            absolute_url,
                            timeout=timeout,
                            **kwargs,
                        ),
                        timeout=asyncio_timeout
                    )
                except asyncio.TimeoutError:
                    self.log(f"Request asyncio timeout for {method} {absolute_url} after {asyncio_timeout}s", "ERROR")
                    raise TimeoutError(f"Request timed out after {asyncio_timeout}s")
                except asyncio.CancelledError:
                    self.log(f"Request cancelled for {method} {absolute_url}", "WARNING")
                    raise
                except requests.Timeout as e:
                    self.log(f"Request timeout for {method} {absolute_url}: {str(e)}", "ERROR")
                    raise TimeoutError(f"Request timed out after {timeout}s") from e
                except requests.ConnectionError as e:
                    self.log(f"Connection error for {method} {absolute_url}: {str(e)}", "ERROR")
                    raise ConnectionError(f"Failed to connect to {absolute_url}") from e
                except requests.HTTPError as e:
                    self.log(f"HTTP error for {method} {absolute_url}: {str(e)}", "ERROR")
                    raise
                except Exception as e:
                    # Catch other unexpected errors (e.g., RemoteDisconnected wrapped in other exceptions)
                    error_str = str(e)
                    if "RemoteDisconnected" in error_str or "Connection aborted" in error_str:
                        self.log(f"Remote disconnected for {method} {absolute_url}: {error_str}", "ERROR")
                        raise ConnectionError(f"Remote server disconnected: {absolute_url}") from e
                    raise
    
    async def authenticate(self, email: str, password: str) -> Dict[str, Any]:
        """Authenticate with WorldQuant BRAIN platform with biometric support."""
        self.log("🔐 Starting Authentication process...", "INFO")
        auth_timeout = self._default_timeout_seconds + 10  # Extra buffer for asyncio timeout
        
        try:
            async with self._auth_lock:
                # Store credentials for potential re-authentication
                self.auth_credentials = {'email': email, 'password': password}
                
                # Clear any existing session data (quick operation, no lock needed for this)
                self.session.cookies.clear()
                self.session.auth = None
                
                # Create Basic Authentication header (base64 encoded credentials)
                import base64
                credentials = f"{email}:{password}"
                encoded_credentials = base64.b64encode(credentials.encode()).decode()
                
                # Send POST request with Basic Authentication header
                headers = {
                    'Authorization': f'Basic {encoded_credentials}'
                }
                
                # Use a direct thread call with timeout, no nested locks
                try:
                    response = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.session.request,
                            'POST',
                            'https://api.worldquantbrain.com/authentication',
                            headers=headers,
                            timeout=self._default_timeout_seconds,
                        ),
                        timeout=auth_timeout
                    )
                except asyncio.TimeoutError:
                    self.log(f"❌ Authentication request timed out after {auth_timeout}s", "ERROR")
                    raise TimeoutError(f"Authentication timed out after {auth_timeout}s")

                # Check for successful authentication (status code 201)
                if response.status_code == 201:
                    self.log("Authentication successful", "SUCCESS")
                    
                    # Check if JWT token was automatically stored by session
                    jwt_token = self.session.cookies.get('t')
                    if jwt_token:
                        self.log("JWT token automatically stored by session", "SUCCESS")
                    
                    # Return success response
                    return {
                        'user': {'email': email},
                        'status': 'authenticated',
                        'permissions': ['read', 'write'],
                        'message': 'Authentication successful',
                        'status_code': response.status_code,
                        'has_jwt': jwt_token is not None
                    }
                
                # Check if biometric authentication is required (401 with persona)
                elif response.status_code == 401:
                    www_auth = response.headers.get("WWW-Authenticate")
                    location = response.headers.get("Location")
                    
                    if www_auth == "persona" and location:
                        self.log("🔴 Biometric authentication required", "INFO")
                        
                        # Handle biometric authentication
                        from urllib.parse import urljoin
                        biometric_url = urljoin(response.url, location)
                        
                        # Release auth_lock before calling biometric auth to avoid deadlock
                        # Biometric auth will acquire its own locks as needed
                        return await self._handle_biometric_auth(biometric_url, email)
                    else:
                        raise Exception("Incorrect email or password")
                else:
                    raise Exception(f"Authentication failed with status code: {response.status_code}")
                    
        except asyncio.TimeoutError:
            self.log(f"❌ Authentication timed out", "ERROR")
            raise TimeoutError("Authentication request timed out")
        except requests.HTTPError as e:
            self.log(f"❌ HTTP error during authentication: {e}", "ERROR")
            raise
        except Exception as e:
            self.log(f"❌ Authentication failed: {str(e)}", "ERROR")
            raise
    
    async def _handle_biometric_auth(self, biometric_url: str, email: str) -> Dict[str, Any]:
        """Handle biometric authentication using browser automation."""
        self.log("🌐 Starting biometric authentication...", "INFO")
        
        try:
            # Import playwright for browser automation
            from playwright.async_api import async_playwright
            import time
            
            # 尝试导入browser_setup模块来获取浏览器路径
            browser_path = None
            try:
                from browser_setup import ensure_browser_available
                browser_path = ensure_browser_available()
            except ImportError:
                # 如果导入失败，尝试从当前目录导入
                try:
                    import sys
                    from pathlib import Path
                    current_dir = Path(__file__).parent
                    sys.path.insert(0, str(current_dir))
                    from browser_setup import ensure_browser_available
                    browser_path = ensure_browser_available()
                except:
                    pass
            
            async with async_playwright() as p:
                # 设置浏览器启动参数
                browser_args = ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage']
                
                if browser_path and os.path.exists(browser_path):
                    self.log(f"使用自定义浏览器路径: {browser_path}", "INFO")
                    browser = await p.chromium.launch(executable_path=browser_path, args=browser_args)
                else:
                    self.log("使用默认Playwright浏览器", "INFO")
                    browser = await p.chromium.launch(headless=True, args=browser_args)
                    
                page = await browser.new_page()

                self.log("🌐 Opening browser for biometric authentication...", "INFO")
                await page.goto(biometric_url)
                self.log("Browser page loaded successfully", "SUCCESS")

                # Print instructions
                print("\n" + "="*60, file=sys.stderr)
                print("BIOMETRIC AUTHENTICATION REQUIRED", file=sys.stderr)
                print("="*60, file=sys.stderr)
                print("Browser window is open with biometric authentication page", file=sys.stderr)
                print("Complete the biometric authentication in the browser", file=sys.stderr)
                print("The system will automatically check when you're done...", file=sys.stderr)
                print("="*60, file=sys.stderr)

                # Keep checking until authentication is complete
                max_attempts = 60  # 5 minutes maximum (60 * 5 seconds)
                attempt = 0

                while attempt < max_attempts:
                    await asyncio.sleep(5)  # Check every 5 seconds
                    attempt += 1

                    # Check if authentication completed
                    check_response = await self._request('POST', biometric_url)
                    self.log(f"🔄 Checking authentication status (attempt {attempt}/{max_attempts}): {check_response.status_code}", "INFO")

                    if check_response.status_code == 201:
                        self.log("Biometric authentication successful!", "SUCCESS")

                        await browser.close()
                        
                        # Check JWT token
                        jwt_token = self.session.cookies.get('t')
                        if jwt_token:
                            self.log("JWT token received", "SUCCESS")
                        
                        # Return success response
                        return {
                            'user': {'email': email},
                            'status': 'authenticated',
                            'permissions': ['read', 'write'],
                            'message': 'Biometric authentication successful',
                            'status_code': check_response.status_code,
                            'has_jwt': jwt_token is not None
                        }
                
                await browser.close()
                raise Exception("Biometric authentication timed out")

        except Exception as e:
            self.log(f"❌ Biometric authentication failed: {str(e)}", "ERROR")
            raise
    
    async def is_authenticated(self) -> bool:
        """Check if currently authenticated using JWT token."""
        try:
            # Check if we have a JWT token in cookies
            jwt_token = self.session.cookies.get('t')
            if not jwt_token:
                self.log("❌ No JWT token found", "INFO")
                return False
            
            # Test authentication with a simple API call
            response = await self._request('GET', f"{self.base_url}/authentication")
            if response.status_code == 200:
                return True
            elif response.status_code == 401:
                self.log("❌ JWT token expired or invalid (401)", "INFO")
                return False
            else:
                self.log(f"⚠️ Unexpected status code during auth check: {response.status_code}", "WARNING")
                return False
        except (TimeoutError, ConnectionError) as e:
            self.log(f"❌ Network error checking authentication: {str(e)}", "ERROR")
            return False
        except Exception as e:
            self.log(f"❌ Unexpected error checking authentication: {str(e)}", "ERROR")
            return False
    
    async def ensure_authenticated(self):
        """Ensure authentication is valid, re-authenticate if needed."""
        if not await self.is_authenticated():
            if not self.auth_credentials:
                self.log("No credentials in memory, loading from config...", "INFO")
                config = load_config()
                creds = config.get("credentials", {})
                email = creds.get("email")
                password = creds.get("password")
                if not email or not password:
                    raise Exception("Authentication credentials not found in config. Please authenticate first.")
                self.auth_credentials = {'email': email, 'password': password}

            self.log("🔄 Re-authenticating...", "INFO")
            await self.authenticate(self.auth_credentials['email'], self.auth_credentials['password'])
    
    async def get_authentication_status(self) -> Optional[Dict[str, Any]]:
        """Get current authentication status and user info."""
        try:
            response = await self._request('GET', f"{self.base_url}/users/self")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get auth status: {str(e)}", "ERROR")
            return None
    
    async def create_simulation(self, simulation_data: SimulationData) -> Dict[str, str]:
        """Create a new simulation on BRAIN platform."""
        await self._create_simulation_semaphore.acquire()
        try:
            await self.ensure_authenticated()
        
            self.log("🚀 Creating simulation...", "INFO")
            
            # Prepare settings based on simulation type
            settings_dict = simulation_data.settings.model_dump()
            
            # Remove fields based on simulation type
            if simulation_data.type == "REGULAR":
                # Remove SUPER-specific fields for REGULAR
                settings_dict.pop('selectionHandling', None)
                settings_dict.pop('selectionLimit', None)
                settings_dict.pop('componentActivation', None)
            
            # Filter out None values from settings
            settings_dict = {k: v for k, v in settings_dict.items() if v is not None}
            
            # Prepare simulation payload
            payload = {
                'type': simulation_data.type,
                'settings': settings_dict
            }
            
            # Add type-specific fields
            if simulation_data.type == "REGULAR":
                if simulation_data.regular:
                    payload['regular'] = simulation_data.regular
            elif simulation_data.type == "SUPER":
                if simulation_data.combo:
                    payload['combo'] = simulation_data.combo
                if simulation_data.selection:
                    payload['selection'] = simulation_data.selection
            
            # Filter out None values from entire payload
            payload = {k: v for k, v in payload.items() if v is not None}
            
            response = await self._request('POST', f"{self.base_url}/simulations", json=payload)
            response.raise_for_status()
            
            location = response.headers.get('Location', '')
            location_url = self._to_absolute_url(location)
            simulation_id = location_url.split('/')[-1] if location_url else None
            
            self.log(f"Simulation created with ID: {simulation_id}", "SUCCESS")

            start_time = time.time()
            timeout_seconds = 1800  # 10 minutes
            max_poll_retries = 5  # Max retries for transient connection errors during polling
            poll_retry_delay = 3  # Initial delay between poll retries

            simulation_progress = None
            while True:
                # Check for timeout
                if time.time() - start_time > timeout_seconds:
                    raise TimeoutError(f"Simulation {simulation_id} timed out after {timeout_seconds} seconds")

                # Poll with retry logic for transient network errors
                poll_error = None
                for poll_attempt in range(max_poll_retries):
                    try:
                        simulation_progress = await self._request('GET', location_url)
                        poll_error = None
                        break  # Success, exit retry loop
                    except (ConnectionError, TimeoutError) as e:
                        poll_error = e
                        if poll_attempt < max_poll_retries - 1:
                            retry_wait = poll_retry_delay * (1.5 ** poll_attempt)
                            self.log(f"⚠️ Polling connection error for {simulation_id} (attempt {poll_attempt + 1}/{max_poll_retries}), retrying in {retry_wait:.1f}s: {str(e)}", "WARNING")
                            await asyncio.sleep(retry_wait)
                        else:
                            self.log(f"❌ Polling failed after {max_poll_retries} attempts for {simulation_id}: {str(e)}", "ERROR")
                
                if poll_error:
                    raise poll_error
                
                # Check if we need to wait
                retry_after = simulation_progress.headers.get("Retry-After")
                
                if not retry_after or float(retry_after) == 0:
                    break
                
                wait_time = float(retry_after)
                # Use asyncio.sleep instead of time.sleep to avoid blocking
                await asyncio.sleep(wait_time)

            self.log("Alpha done simulating, getting alpha details", "INFO")
            
            progress_data = simulation_progress.json()
            if "alpha" not in progress_data:
                # Handle error case where alpha ID is missing
                error_message = progress_data.get("message", "Unknown error")
                raise Exception(f"Simulation failed or returned no alpha ID. Details: {error_message}")
                
            alpha_id = progress_data["alpha"]
            
            # Fetch alpha details with retry logic
            alpha_response = None
            for alpha_attempt in range(max_poll_retries):
                try:
                    alpha_response = await self._request('GET', f"https://api.worldquantbrain.com/alphas/{alpha_id}")
                    break
                except (ConnectionError, TimeoutError) as e:
                    if alpha_attempt < max_poll_retries - 1:
                        retry_wait = poll_retry_delay * (1.5 ** alpha_attempt)
                        self.log(f"⚠️ Failed to fetch alpha details (attempt {alpha_attempt + 1}/{max_poll_retries}), retrying in {retry_wait:.1f}s: {str(e)}", "WARNING")
                        await asyncio.sleep(retry_wait)
                    else:
                        self.log(f"❌ Failed to fetch alpha details after {max_poll_retries} attempts: {str(e)}", "ERROR")
                        raise
            
            return alpha_response.json()
            
        except Exception as e:
            self.log(f"❌ Failed to create simulation: {str(e)}", "ERROR")
            raise
        finally:
            self._create_simulation_semaphore.release()
    
    async def get_alpha_details(self, alpha_id: str) -> Dict[str, Any]:
        """Get detailed information about an alpha."""
        await self.ensure_authenticated()
        
        try:
            response = await self._request('GET', f"{self.base_url}/alphas/{alpha_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get alpha details: {str(e)}", "ERROR")
            raise
    
    async def get_datasets(self, category: Optional[str] = None, region: str = "USA",
                          delay: int = 1, universe: str = "TOP3000", theme: str = "false", search: Optional[str] = None) -> Dict[str, Any]:
        """Get available datasets with Redis caching (1 day TTL) and fetch all data at once."""
        await self.ensure_authenticated()
        
        try:
            # Generate cache key from parameters (excluding search for cache key)
            cache_params = {
                'category': category,
                'region': region,
                'delay': delay,
                'universe': universe,
                'theme': theme
            }
            cache_key = self._generate_cache_key('datasets', cache_params)
            
            # Try to get from cache
            cached_data = self._get_cached_data(cache_key)
            if cached_data:
                # Apply search filter if needed
                if search:
                    filtered_results = [
                        item for item in cached_data.get('results', [])
                        if search.lower() in json.dumps(item).lower()
                    ]
                    return {
                        **cached_data,
                        'results': filtered_results,
                        'count': len(filtered_results),
                        'from_cache': True
                    }
                return {**cached_data, 'from_cache': True}
            
            # Fetch all data from API (pagination loop)
            all_results = []
            offset = 0
            limit = 50
            total_count = None
            
            while True:
                params = {
                    'category': category,
                    'region': region,
                    'delay': delay,
                    'universe': universe,
                    'theme': theme,
                    'limit': limit,
                    'offset': offset
                }
                
                while True:
                    response = await self._request('GET', f"{self.base_url}/data-sets", params=params)
                    if response.status_code == 429:
                        self.log(f"get_datasets 429 rate limit, retrying after 5s (offset={offset})", "WARNING")
                        await asyncio.sleep(5)
                        continue
                    response.raise_for_status()
                    break
                data = response.json()
                
                results = data.get('results', [])
                all_results.extend(results)
                
                if total_count is None:
                    total_count = data.get('count', 0)
                
                # Break if we've fetched all data
                if len(results) < limit or len(all_results) >= total_count:
                    break
                
                offset += limit
            
            # Prepare complete response
            complete_data = {
                'results': all_results,
                'count': len(all_results),
                'extraNote': "if your returned result is 0, you may want to check your parameter by using get_platform_setting_options tool to got correct parameter",
                'from_cache': False
            }
            
            # Cache the complete data (1 week TTL)
            self._set_cached_data(cache_key, complete_data, ttl=604800)
            
            # Apply search filter if needed
            if search:
                filtered_results = [
                    item for item in all_results
                    if search.lower() in json.dumps(item).lower()
                ]
                complete_data['results'] = filtered_results
                complete_data['count'] = len(filtered_results)
            
            return complete_data
            
        except Exception as e:
            self.log(f"Failed to get datasets: {str(e)}", "ERROR")
            raise
    
    async def get_datafields(self, instrument_type: str = "EQUITY", region: str = "USA",
                            delay: int = 1, universe: str = "TOP3000", theme: str = "false",
                            dataset_id: Optional[str] = None, data_type: str = "",
                            search: Optional[str] = None,
                            filter_sharpe: bool = True) -> Dict[str, Any]:
        """Get available data fields with Redis caching (1 day TTL) and fetch all data at once.
        
        Search supports fuzzy matching across multiple fields:
        - Searches in: name, description, dataset.name, dataset.vendor, id
        - Multiple keywords (space-separated) use AND logic
        - Case-insensitive matching
        
        OS/IS Sharpe filtering (filter_sharpe=True by default):
        - Filters out datafields whose OS/IS Sharpe ratio < 0 to improve field quality
        - Uses pre-aggregated statistics from WebDataScope info_data.bin
        - Matching is done at datafield level first, then dataset level as fallback
        
        Examples:
        - search="price" -> matches any field containing "price"
        - search="stock volume" -> matches fields containing both "stock" AND "volume"
        """
        await self.ensure_authenticated()
        
        # Redis-based distributed lock for concurrency control (limit to 1)
        lock_key = "lock:get_datafields"
        lock_acquired = False
        lock_timeout = 300  # Lock expires after 5 minutes to prevent deadlock
        max_wait_time = 600  # Maximum wait time for acquiring lock (10 minutes)
        wait_interval = 2  # Check every 2 seconds
        
        if self.redis_client:
            start_wait = time.time()
            while time.time() - start_wait < max_wait_time:
                try:
                    # Try to acquire lock with NX (only set if not exists) and EX (expiration)
                    lock_acquired = self.redis_client.set(lock_key, "locked", ex=lock_timeout, nx=True)
                    if lock_acquired:
                        self.log(f"Acquired Redis lock for get_datafields", "INFO")
                        break
                    else:
                        # Lock is held by another process, wait and retry
                        ttl = self.redis_client.ttl(lock_key)
                        self.log(f"Waiting for get_datafields lock (TTL: {ttl}s)...", "INFO")
                        await asyncio.sleep(wait_interval)
                except Exception as e:
                    self.log(f"Redis lock acquisition failed: {str(e)}, proceeding without lock", "WARNING")
                    break
            
            if not lock_acquired and self.redis_client:
                self.log(f"Could not acquire get_datafields lock after {max_wait_time}s, proceeding anyway", "WARNING")
        
        try:
            def fuzzy_search_filter(item: Dict[str, Any], search_term: str) -> bool:
                """Enhanced fuzzy search across key fields with multi-keyword support."""
                if not search_term:
                    return True
                
                # Split search term into keywords (space-separated) for AND logic
                keywords = [kw.strip().lower() for kw in search_term.split() if kw.strip()]
                if not keywords:
                    return True
                
                # Extract searchable fields
                searchable_text_parts = []
                
                # Add field name
                if item.get('name'):
                    searchable_text_parts.append(str(item['name']))
                
                # Add field description
                if item.get('description'):
                    searchable_text_parts.append(str(item['description']))
                
                # Add field ID
                if item.get('id'):
                    searchable_text_parts.append(str(item['id']))
                
                # Add dataset information
                dataset = item.get('dataset', {})
                if isinstance(dataset, dict):
                    if dataset.get('name'):
                        searchable_text_parts.append(str(dataset['name']))
                    if dataset.get('vendor'):
                        searchable_text_parts.append(str(dataset['vendor']))
                    if dataset.get('id'):
                        searchable_text_parts.append(str(dataset['id']))
                
                # Combine all searchable text
                combined_text = ' '.join(searchable_text_parts).lower()
                
                # Check if ALL keywords match (AND logic)
                return all(keyword in combined_text for keyword in keywords)
            
            # Generate cache key from parameters (excluding search for cache key)
            cache_params = {
                'instrumentType': instrument_type,
                'region': region,
                'delay': delay,
                'universe': universe,
                'theme': theme,
                'dataset_id': dataset_id,
                'data_type': data_type
            }
            cache_key = self._generate_cache_key('datafields', cache_params)
            
            def sharpe_filter(items: list, rgn: str, dly: int) -> tuple:
                """Filter out datafields with OS/IS sharpe < 0. Returns (filtered_items, removed_count, applied)."""
                if not self._isos_data:
                    return items, 0, False
                region_key = f"{rgn}_{dly}"
                isos_info = self._isos_data.get(region_key, {})
                isos_section = isos_info.get('isos', {})
                datafield_sharpe = isos_section.get('datafield', {})
                dataset_sharpe_map = isos_section.get('dataset', {})
                if not datafield_sharpe and not dataset_sharpe_map:
                    return items, 0, False
                filtered = []
                for item in items:
                    field_name = item.get('id', '') or item.get('name', '')
                    dataset_info = item.get('dataset', {})
                    ds_id = dataset_info.get('id', '') if isinstance(dataset_info, dict) else ''
                    df_stats = datafield_sharpe.get(field_name)
                    if df_stats is not None:
                        sr = df_stats.get('sharpe_ratio')
                        if sr is not None and sr < 0:
                            continue
                    if df_stats is None and ds_id:
                        ds_stats = dataset_sharpe_map.get(ds_id)
                        if ds_stats is not None:
                            sr = ds_stats.get('sharpe_ratio')
                            if sr is not None and sr < 0:
                                continue
                    filtered.append(item)
                return filtered, len(items) - len(filtered), True

            # Try to get from cache
            cached_data = self._get_cached_data(cache_key)
            if cached_data:
                result = {**cached_data, 'from_cache': True}
                results = result.get('results', [])
                # Apply fuzzy search filter if needed
                if search:
                    results = [item for item in results if fuzzy_search_filter(item, search)]
                # Apply OS/IS Sharpe filtering
                if filter_sharpe:
                    results, removed, applied = sharpe_filter(results, region, delay)
                    result['sharpe_filter_applied'] = applied
                    result['sharpe_filter_removed'] = removed
                result['results'] = results
                result['count'] = len(results)
                return result
            
            # Fetch all data from API (pagination loop)
            all_results = []
            offset = 0
            limit = 50
            total_count = None
            
            while True:
                params = {
                    'instrumentType': instrument_type,
                    'region': region,
                    'delay': delay,
                    'universe': universe,
                    'limit': limit,
                    'offset': offset
                }
                
                if data_type != 'ALL' and data_type:
                    params['type'] = data_type
                
                if dataset_id:
                    params['dataset.id'] = dataset_id
                
                while True:
                    response = await self._request('GET', f"{self.base_url}/data-fields", params=params)
                    if response.status_code == 429:
                        self.log(f"get_datafields 429 rate limit, retrying after 5s (offset={offset})", "WARNING")
                        await asyncio.sleep(5)
                        continue
                    response.raise_for_status()
                    break
                data = response.json()
                
                results = data.get('results', [])
                # 等待2秒
                await asyncio.sleep(2)
                all_results.extend(results)
                
                if total_count is None:
                    total_count = data.get('count', 0)
                
                # Break if we've fetched all data
                if len(results) < limit or len(all_results) >= total_count:
                    break
                
                offset += limit
            
            # Prepare complete response
            complete_data = {
                'results': all_results,
                'count': len(all_results),
                'extraNote': "if your returned result is 0, you may want to check your parameter by using get_platform_setting_options tool to got correct parameter. Search supports fuzzy matching with multiple keywords (space-separated, AND logic).",
                'from_cache': False
            }
            
            # Cache the complete data (1 day TTL)
            self._set_cached_data(cache_key, complete_data, ttl=604800)
            
            # Apply fuzzy search filter if needed
            if search:
                filtered_results = [
                    item for item in all_results
                    if fuzzy_search_filter(item, search)
                ]
                complete_data['results'] = filtered_results
                complete_data['count'] = len(filtered_results)
            
            # Apply OS/IS Sharpe ratio filtering
            if filter_sharpe:
                results, removed, applied = sharpe_filter(complete_data['results'], region, delay)
                complete_data['results'] = results
                complete_data['count'] = len(results)
                complete_data['sharpe_filter_applied'] = applied
                complete_data['sharpe_filter_removed'] = removed
                if applied:
                    self.log(f"Sharpe filter ({region}_{delay}): removed {removed}/{removed + len(results)} fields with OS/IS sharpe < 0", "INFO")
            
            return complete_data
            
        except Exception as e:
            self.log(f"Failed to get datafields: {str(e)}", "ERROR")
            raise
        finally:
            # Release Redis lock if acquired
            if lock_acquired and self.redis_client:
                try:
                    self.redis_client.delete(lock_key)
                    self.log(f"Released Redis lock for get_datafields", "INFO")
                except Exception as e:
                    self.log(f"Failed to release Redis lock: {str(e)}", "WARNING")
    
    async def get_alpha_pnl(self, alpha_id: str) -> Dict[str, Any]:
        """Get PnL data for an alpha with retry logic."""
        await self.ensure_authenticated()
        
        max_retries = 5
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                self.log(f"Attempting to get PnL for alpha {alpha_id} (attempt {attempt + 1}/{max_retries})", "INFO")
                
                response = await self._request('GET', f"{self.base_url}/alphas/{alpha_id}/recordsets/pnl")
                response.raise_for_status()
                
                text = (response.text or "").strip()
                if not text:
                    if attempt < max_retries - 1:
                        self.log(f"Empty PnL response for {alpha_id}, retrying in {retry_delay} seconds...", "WARNING")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 1.5
                        continue
                    else:
                        self.log(f"Empty PnL response after {max_retries} attempts for {alpha_id}", "WARNING")
                        return {}
                
                try:
                    pnl_data = response.json()
                    if pnl_data:
                        self.log(f"Successfully retrieved PnL data for alpha {alpha_id}", "SUCCESS")
                        return pnl_data
                    else:
                        if attempt < max_retries - 1:
                            self.log(f"Empty PnL JSON for {alpha_id}, retrying in {retry_delay} seconds...", "WARNING")
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 1.5
                            continue
                        else:
                            self.log(f"Empty PnL JSON after {max_retries} attempts for {alpha_id}", "WARNING")
                            return {}
                            
                except json.JSONDecodeError as parse_err:
                    if attempt < max_retries - 1:
                        self.log(f"PnL JSON parse failed for {alpha_id} (attempt {attempt + 1}), retrying in {retry_delay} seconds...", "WARNING")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 1.5
                        continue
                    else:
                        self.log(f"PnL JSON parse failed for {alpha_id} after {max_retries} attempts: {parse_err}", "WARNING")
                        return {}
                        
            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    self.log(f"Failed to get alpha PnL for {alpha_id} (attempt {attempt + 1}), retrying in {retry_delay} seconds: {str(e)}", "WARNING")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 1.5
                    continue
                else:
                    self.log(f"Failed to get alpha PnL for {alpha_id} after {max_retries} attempts: {str(e)}", "ERROR")
                    raise
        
        return {}
    
    def _match_alpha_filters(self, alpha: Dict[str, Any], region: Optional[str],
                             status: Optional[str], alpha_type: Optional[str],
                             is_super: Optional[bool]) -> bool:
        """Check if an alpha matches the client-side filters."""
        if region and alpha.get('settings', {}).get('region', '').upper() != region.upper():
            return False
        if status and alpha.get('status', '').upper() != status.upper():
            return False
        if alpha_type and alpha.get('type', '').upper() != alpha_type.upper():
            return False
        if is_super is not None:
            actual_is_super = alpha.get('type', '').upper() == 'SUPER'
            if actual_is_super != is_super:
                return False
        return True

    async def get_user_alphas(
        self,
        stage: str = "OS",
        limit: int = 30,
        offset: int = 0,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        submission_start_date: Optional[str] = None,
        submission_end_date: Optional[str] = None,
        order: Optional[str] = None,
        hidden: Optional[bool] = None,
        region: Optional[str] = None,
        status: Optional[str] = None,
        alpha_type: Optional[str] = None,
        is_super: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Get user's alphas with advanced filtering and Redis caching (1 day TTL).
        
        Note: The BRAIN API does not support region/status/type/is_super as query
        parameters. These are applied as client-side filters after fetching.
        """
        await self.ensure_authenticated()
        
        need_client_filter = any([region, status, is_super is not None])
        
        try:
            # Build API params (only params the API actually supports)
            api_params = {
                "stage": stage,
            }
            if start_date:
                api_params["dateCreated>"] = start_date
            if end_date:
                api_params["dateCreated<"] = end_date
            if submission_start_date:
                api_params["dateSubmitted>"] = submission_start_date
            if submission_end_date:
                api_params["dateSubmitted<"] = submission_end_date
            if order:
                api_params["order"] = order
            if hidden is not None:
                api_params["hidden"] = str(hidden).lower()
            # 'type' is supported server-side (REGULAR, SUPER, etc.)
            if alpha_type:
                api_params["type"] = alpha_type
            
            # Build full cache key including client-side filter params
            cache_params = {**api_params, "limit": limit, "offset": offset}
            if region:
                cache_params["_region"] = region
            if status:
                cache_params["_status"] = status
            if alpha_type:
                cache_params["_type"] = alpha_type
            if is_super is not None:
                cache_params["_is_super"] = str(is_super).lower()
            
            cache_key = self._generate_cache_key('user_alphas', cache_params)
            
            # Try to get from cache
            cached_data = self._get_cached_data(cache_key)
            if cached_data:
                return {**cached_data, 'from_cache': True}
            
            if not need_client_filter:
                # No client-side filtering needed — use simple server-side pagination
                api_params["limit"] = limit
                api_params["offset"] = offset
                response = await self._request('GET', f"{self.base_url}/users/self/alphas", params=api_params)
                response.raise_for_status()
                data = response.json()
            else:
                # Client-side filtering: fetch all matching alphas, then filter
                # Note: The API caps results at 100 per request regardless of limit param
                BATCH_SIZE = 100
                filtered_results = []
                api_offset = 0
                total_server_count = None
                
                while True:
                    api_params_batch = {**api_params, "limit": BATCH_SIZE, "offset": api_offset}
                    response = await self._request('GET', f"{self.base_url}/users/self/alphas", params=api_params_batch)
                    response.raise_for_status()
                    batch_data = response.json()
                    
                    if total_server_count is None:
                        total_server_count = batch_data.get('count', 0)
                    
                    batch_results = batch_data.get('results', [])
                    if not batch_results:
                        break
                    
                    for alpha in batch_results:
                        if self._match_alpha_filters(alpha, region, status, alpha_type, is_super):
                            filtered_results.append(alpha)
                    
                    api_offset += len(batch_results)
                    # Stop if we've exhausted all server-side results
                    if api_offset >= total_server_count:
                        break
                
                # Apply user's offset/limit to the filtered results
                total_filtered = len(filtered_results)
                page_results = filtered_results[offset:offset + limit]
                
                next_offset = offset + limit
                data = {
                    'count': total_filtered,
                    'next': f"offset={next_offset}&limit={limit}" if next_offset < total_filtered else None,
                    'previous': f"offset={max(0, offset - limit)}&limit={limit}" if offset > 0 else None,
                    'results': page_results,
                }
            
            # Add metadata
            data['from_cache'] = False
            
            # Cache the data (1 day TTL)
            self._set_cached_data(cache_key, data, ttl=604800)
            
            return data
            
        except Exception as e:
            self.log(f"Failed to get user alphas: {str(e)}", "ERROR")
            raise
    
    def pre_submit_check(self, alpha_details: Dict[str, Any]) -> Dict[str, Any]:
        """Check IS metrics against submission thresholds before submitting.

        Criteria:
        - Sharpe > 1.3 and Fitness > 0.75 (relaxed thresholds for pre-submission check)
        - Margin > 0.05% for USA, otherwise > 0.15% (hard floor 0.08%)
        - Turnover between 4% and 40%
        - Returns > 4%
        - All other IS checks must PASS (no FAIL)
        """
        is_data = alpha_details.get('is')
        if not is_data:
            return {'passed': False, 'reason': 'No IS data available for this alpha. Simulation may not be complete.', 'details': []}

        failures = []
        warnings = []

        sharpe = is_data.get('sharpe', 0)
        fitness = is_data.get('fitness', 0)
        margin = is_data.get('margin', 0)
        turnover = is_data.get('turnover', 0)
        returns = is_data.get('returns', 0)
        drawdown = is_data.get('drawdown', 0)
        settings = alpha_details.get('settings') or {}
        region = (settings.get('region') or alpha_details.get('region') or '').upper()

        # Sharpe > 1.3
        if sharpe <= 1.3:
            failures.append(f'Sharpe {sharpe} <= 1.3 (required > 1.3)')

        # Fitness > 0.75
        if fitness <= 0.75:
            failures.append(f'Fitness {fitness} <= 0.75 (required > 0.75)')

        # USA margin rule is relaxed to >5bp. Other regions keep the >15bp target with a 8bp hard floor.
        if region == 'USA':
            if margin <= 0.0005:
                failures.append(f'Margin {margin*100:.4f}% <= 5bp (required > 5bp for USA)')
        else:
            if margin <= 0.0008:
                failures.append(f'Margin {margin*100:.4f}% <= 8bp (hard floor, required > 15bp)')
            elif margin <= 0.0015:
                warnings.append(f'Margin {margin*100:.4f}% <= 15bp (recommended > 15bp, current above 8bp hard floor)')

        # Turnover between 4% and 40%
        if turnover < 0.04:
            failures.append(f'Turnover {turnover*100:.2f}% < 4% (required 4%-40%)')
        elif turnover > 0.40:
            failures.append(f'Turnover {turnover*100:.2f}% > 40% (required 4%-40%)')

        # Returns > 4%
        if returns <= 0.04:
            failures.append(f'Returns {returns*100:.2f}% <= 4% (required > 4%)')

        # # Returns > drawdown
        # if returns <= drawdown:
        #     failures.append(f'Returns {returns*100:.2f}% <= Drawdown {drawdown*100:.2f}% (required Returns > Drawdown)')

        # All other IS checks must not be FAIL
        checks = is_data.get('checks', [])
        for chk in checks:
            result = chk.get('result', '')
            name = chk.get('name', 'UNKNOWN')
            if result == 'FAIL':
                value = chk.get('value', 'N/A')
                limit = chk.get('limit', 'N/A')
                failures.append(f'IS check {name} FAILED (value={value}, limit={limit})')

        passed = len(failures) == 0
        return {
            'passed': passed,
            'failures': failures,
            'warnings': warnings,
            'metrics': {
                'region': region or None,
                'sharpe': sharpe,
                'fitness': fitness,
                'margin': margin,
                'margin_bp': round(margin * 10000, 2),
                'turnover': turnover,
                'returns': returns,
                'drawdown': drawdown,
            },
            'is_checks_summary': [
                {'name': c.get('name'), 'result': c.get('result'), 'value': c.get('value'), 'limit': c.get('limit')}
                for c in checks
            ],
        }

    async def submit_alpha(self, alpha_id: str) -> bool:
        """Submit an alpha for production.
        
        Implements the correct submit flow from submit.py:
        1. POST to /alphas/{alpha_id}/submit
        2. If response has Retry-After header, switch to GET polling until no more retry-after
        3. Non-200/403 responses retry after 2 minutes
        4. Parses response JSON to check IS checks for ALREADY_SUBMITTED and FAILs
        """
        await self.ensure_authenticated()
        
        submit_url = f"{self.base_url}/alphas/{alpha_id}/submit"
        attempt = 0

        while True:
            attempt += 1
            self.log(f"Submit attempt {attempt} for alpha {alpha_id}", "INFO")

            try:
                response = await self._request('POST', submit_url)
            except Exception as e:
                self.log(f"Submit POST failed for {alpha_id}: {e}", "ERROR")
                raise

            self.log(f"Alpha submit, alpha_id={alpha_id}, status_code={response.status_code}", "INFO")

            # Handle Retry-After header: switch to GET polling
            while 'retry-after' in {k.lower() for k in response.headers}:
                retry_after_raw = response.headers.get('Retry-After') or response.headers.get('retry-after', '5')
                try:
                    wait_time = float(retry_after_raw)
                except ValueError:
                    wait_time = 5.0
                # Match reference: 5x multiplier for short waits
                actual_wait = 5 * wait_time if wait_time < 60 else wait_time
                self.log(f"Rate limited (Retry-After={retry_after_raw}s), waiting {actual_wait:.0f}s then GET polling...", "INFO")
                await asyncio.sleep(actual_wait)
                try:
                    response = await self._request('GET', submit_url)
                    self.log(f"GET poll response, alpha_id={alpha_id}, status_code={response.status_code}", "INFO")
                except Exception as e:
                    self.log(f"Submit GET poll failed for {alpha_id}: {e}", "ERROR")
                    raise

            if response.status_code == 200:
                # Parse response JSON to validate IS checks
                try:
                    res_json = response.json()
                except (json.JSONDecodeError, ValueError):
                    self.log(f"Submit response for {alpha_id} is not valid JSON: {(response.text or '')[:200]}", "ERROR")
                    return False

                if not res_json:
                    return False

                if 'detail' in res_json and res_json['detail'] == 'Not found.':
                    self.log(f"Submit failed: alpha {alpha_id} not found", "ERROR")
                    return False

                # Check IS checks in response
                if 'is' in res_json and 'checks' in res_json['is']:
                    for item in res_json['is']['checks']:
                        if item.get('name') == 'ALREADY_SUBMITTED':
                            self.log(f"Alpha {alpha_id} already submitted", "WARNING")
                            return False
                        if item.get('result') == 'FAIL':
                            self.log(f"Alpha {alpha_id} IS check failed: {item.get('name')} limit={item.get('limit')} value={item.get('value')}", "ERROR")
                            return False

                self.log(f"Alpha {alpha_id} submission successful!", "INFO")
                return True

            elif response.status_code == 403:
                self.log(f"Submit forbidden (403) for alpha {alpha_id}", "ERROR")
                return False

            else:
                self.log(f"Submit failed status={response.status_code} for {alpha_id}, waiting 2 minutes before retry...", "WARNING")
                await asyncio.sleep(120)
    
    async def get_events(self) -> Dict[str, Any]:
        """Get available events and competitions."""
        await self.ensure_authenticated()
        
        try:
            response = await self._request('GET', f"{self.base_url}/events")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get events: {str(e)}", "ERROR")
            raise
    
    async def get_leaderboard(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get leaderboard data."""
        await self.ensure_authenticated()
        
        try:
            params = {}
            
            if user_id:
                params['user'] = user_id
            else:
                # Get current user ID if not specified
                user_response = await self._request('GET', f"{self.base_url}/users/self")
                if user_response.status_code == 200:
                    user_data = user_response.json()
                    params['user'] = user_data.get('id')
            
            response = await self._request('GET', f"{self.base_url}/consultant/boards/leader", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get leaderboard: {str(e)}", "ERROR")
            raise

    def _is_atom(self, detail: Optional[Dict[str, Any]]) -> bool:
        """Match atom detection used in extract_regular_alphas.py:
        - Primary signal: 'classifications' entries containing 'SINGLE_DATA_SET'
        - Fallbacks: tags list contains 'atom' or classification id/name contains 'ATOM'
        """
        if not detail or not isinstance(detail, dict):
            return False

        classifications = detail.get('classifications') or []
        for c in classifications:
            cid = (c.get('id') or c.get('name') or '')
            if isinstance(cid, str) and 'SINGLE_DATA_SET' in cid:
                return True

        # Fallbacks
        tags = detail.get('tags') or []
        if isinstance(tags, list):
            for t in tags:
                if isinstance(t, str) and t.strip().lower() == 'atom':
                    return True

        for c in classifications:
            cid = (c.get('id') or c.get('name') or '')
            if isinstance(cid, str) and 'ATOM' in cid.upper():
                return True

        return False

    async def value_factor_trendScore(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """Compute diversity score for regular alphas in a date range.

        Description:
        This function calculate the diversity of the users' submission, by checking the diversity, we can have a good understanding on the valuefactor's trend.
        value factor of a user is defiend by This diversity score, which measures three key aspects of work output: the proportion of works
        with the "Atom" tag (S_A), atom proportion, the breadth of pyramids covered (S_P), and how evenly works
        are distributed across those pyramids (S_H). Calculated as their product, it rewards
        strong performance across all three dimensions—encouraging more Atom-tagged works,
        wider pyramid coverage, and balanced distribution—with weaknesses in any area lowering
        the total score significantly.

        Inputs (hints for AI callers):
        - start_date (str): ISO UTC start datetime, e.g. '2025-08-14T00:00:00Z'
        - end_date (str): ISO UTC end datetime, e.g. '2025-08-18T23:59:59Z'
        - Note: this tool always uses 'OS' (submission dates) to define the window; callers do not need to supply a stage.
                - Note: P_max (total number of possible pyramids) is derived from the platform
                    pyramid-multipliers endpoint and not supplied by callers.

        Returns (compact JSON): {
            'diversity_score': float,
            'N': int,  # total regular alphas in window
            'A': int,  # number of Atom-tagged works (is_single_data_set)
            'P': int,  # pyramid coverage count in the sample
            'P_max': int, # used max for normalization
            'S_A': float, 'S_P': float, 'S_H': float,
            'per_pyramid_counts': {pyramid_name: count}
        }
        """
        # Fetch user alphas (always use OS / submission dates per product policy)
        await self.ensure_authenticated()
        alphas_resp = await self.get_user_alphas(stage='OS', limit=500, submission_start_date=start_date, submission_end_date=end_date)

        if not isinstance(alphas_resp, dict) or 'results' not in alphas_resp:
            return {'error': 'Unexpected response from get_user_alphas', 'raw': alphas_resp}

        alphas = alphas_resp['results']
        regular = [a for a in alphas if a.get('type') == 'REGULAR']

        # Fetch details for each regular alpha
        pyramid_list = []
        atom_count = 0
        per_pyramid = {}
        for a in regular:
            try:
                detail = await self.get_alpha_details(a.get('id'))
            except Exception:
                continue

            is_atom = self._is_atom(detail)
            if is_atom:
                atom_count += 1

            # Extract pyramids
            ps = []
            if isinstance(detail.get('pyramids'), list):
                ps = [p.get('name') for p in detail.get('pyramids') if p.get('name')]
            else:
                pt = detail.get('pyramidThemes') or {}
                pss = pt.get('pyramids') if isinstance(pt, dict) else None
                if pss and isinstance(pss, list):
                    ps = [p.get('name') for p in pss if p.get('name')]

            for p in ps:
                pyramid_list.append(p)
                per_pyramid[p] = per_pyramid.get(p, 0) + 1

        N = len(regular)
        A = atom_count
        P = len(per_pyramid)

        # Determine P_max similarly to the script: use pyramid multipliers if available
        P_max = None
        try:
            pm = await self.get_pyramid_multipliers()
            if isinstance(pm, dict) and 'pyramids' in pm:
                pyramids_list = pm.get('pyramids') or []
                P_max = len(pyramids_list)
        except Exception:
            P_max = None

        if not P_max or P_max <= 0:
            P_max = max(P, 1)

        # Component scores
        S_A = (A / N) if N > 0 else 0.0
        S_P = (P / P_max) if P_max > 0 else 0.0

        # Entropy
        S_H = 0.0
        if P <= 1 or not per_pyramid:
            S_H = 0.0
        else:
            total_occ = sum(per_pyramid.values())
            H = 0.0
            for cnt in per_pyramid.values():
                q = cnt / total_occ if total_occ > 0 else 0
                if q > 0:
                    H -= q * math.log2(q)
            max_H = math.log2(P) if P > 0 else 1
            S_H = (H / max_H) if max_H > 0 else 0.0

        diversity_score = S_A * S_P * S_H

        return {
            'diversity_score': diversity_score,
            'N': N,
            'A': A,
            'P': P,
            'P_max': P_max,
            'S_A': S_A,
            'S_P': S_P,
            'S_H': S_H,
            'per_pyramid_counts': per_pyramid
        }

    async def get_operators(self) -> Dict[str, Any]:
        """Get available operators for alpha creation."""
        await self.ensure_authenticated()
        
        try:
            response = await self._request('GET', f"{self.base_url}/operators")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get operators: {str(e)}", "ERROR")
            raise

    async def recommend_datasets(self, region: str = "USA", delay: int = 1,
                                  universe: str = "TOP3000", top_n: int = 20) -> Dict[str, Any]:
        """Recommend datasets based on pyramid lighting, submission distribution, and quality.

        Scoring logic (references WebDataScope distribution.js & dataFlag.js):
        1. Pyramid lighting: uses /users/self/activities/diversity API (dataDiversity.check).
           Categories not yet "PASS" (< 3 alphas) get a higher priority bonus.
           Among unlit categories, the one with fewest submissions gets the highest boost.
        2. Submission distribution: categories and datasets with fewer submitted alphas
           get a higher score to encourage diversification.
        3. Dataset quality: uses pre-aggregated OS/IS Sharpe from info_data.bin.
           Higher mean sharpe = higher quality score.

        Returns ranked list with per-dataset scores and breakdown.
        """
        await self.ensure_authenticated()

        # ---- 1. Fetch diversity / submission distribution ----
        diversity_data = {}
        try:
            response = await self._request(
                'GET',
                f"{self.base_url}/users/self/activities/diversity",
                params={'grouping': 'region,delay,dataCategory'}
            )
            response.raise_for_status()
            diversity_data = response.json()
        except Exception as e:
            self.log(f"Failed to fetch diversity data: {e}", "WARNING")

        # Parse alpha distribution per category for selected region & delay
        category_alpha_counts: Dict[str, int] = {}   # category_id -> alphaCount
        category_pass_status: Dict[str, bool] = {}    # category_id -> is_lit
        category_need_count: Dict[str, int] = {}      # category_id -> how many more needed to light

        for item in diversity_data.get('alphas', []):
            if item.get('region') != region or item.get('delay') != delay:
                continue
            cat_id = item.get('dataCategory', {}).get('id', '') if isinstance(item.get('dataCategory'), dict) else ''
            if not cat_id:
                continue
            alpha_count = item.get('alphaCount', 0)
            is_pass = item.get('dataDiversity', {}).get('check', '') == 'PASS'
            category_alpha_counts[cat_id] = alpha_count
            category_pass_status[cat_id] = is_pass
            # dataDiversity check: PASS means ≥3, else need more
            if not is_pass:
                category_need_count[cat_id] = max(0, 3 - alpha_count)
            else:
                category_need_count[cat_id] = 0

        # Total submitted alphas count for normalization
        total_submitted = sum(category_alpha_counts.values()) or 1

        # ---- 2. Fetch available datasets for this region/delay ----
        datasets_resp = await self.get_datasets(region=region, delay=delay, universe=universe)
        all_datasets = datasets_resp.get('results', [])
        if not all_datasets:
            return {'error': 'No datasets available for the given region/delay/universe'}

        # ---- 3. Load dataset quality from OS/IS Sharpe (info_data.bin) ----
        region_key = f"{region}_{delay}"
        isos_info = self._isos_data.get(region_key, {}).get('isos', {}) if self._isos_data else {}
        dataset_sharpe_map = isos_info.get('dataset', {})
        mean_sharpe = isos_info.get('mean', {}).get('sharpe_ratio', 0.0)

        # ---- 4. Fetch OS alphas to analyze dataset-level submission spread ----
        dataset_submission_counts: Dict[str, int] = {}
        try:
            now = datetime.utcnow()
            q_start_month = (now.month - 1) // 3 * 3 + 1
            quarter_start = datetime(now.year, q_start_month, 1)
            os_alphas = await self.get_user_alphas(
                stage='OS', limit=200,
                submission_start_date=quarter_start.strftime("%Y-%m-%dT00:00:00Z")
            )
            for a in os_alphas.get('results', []):
                ds_id = ''
                if isinstance(a.get('datanodes'), dict):
                    ds_id = a['datanodes'].get('dataset', {}).get('id', '')
                elif isinstance(a.get('settings'), dict):
                    pass  # some alpha formats differ
                # Try to extract from regular code or other fields
                if not ds_id:
                    detail_region = a.get('settings', {}).get('region', '') if isinstance(a.get('settings'), dict) else ''
                    if detail_region and detail_region != region:
                        continue
                dataset_submission_counts[ds_id] = dataset_submission_counts.get(ds_id, 0) + 1
        except Exception as e:
            self.log(f"Failed to fetch OS alphas for dataset spread: {e}", "WARNING")

        # ---- 5. Score each dataset ----
        scored_datasets = []
        for ds in all_datasets:
            ds_id = ds.get('id', '')
            ds_name = ds.get('name', ds_id)
            cat_obj = ds.get('category', {})
            ds_category = cat_obj.get('id', '') if isinstance(cat_obj, dict) else str(cat_obj)
            sub_obj = ds.get('subcategory', {})
            ds_subcategory = sub_obj.get('id', '') if isinstance(sub_obj, dict) else str(sub_obj)

            # --- Pyramid lighting score (0~40 points) ---
            pyramid_score = 0.0
            cat_lit = category_pass_status.get(ds_category, True)  # default True = already lit
            cat_count = category_alpha_counts.get(ds_category, 0)
            need = category_need_count.get(ds_category, 0)

            if not cat_lit:
                # Unlit category gets big bonus; fewer submissions = higher priority
                pyramid_score = 40.0 * (1.0 / (1.0 + cat_count))
            else:
                # Already lit: small bonus for categories with fewer alphas (encourage evenness)
                max_cat_count = max(category_alpha_counts.values()) if category_alpha_counts else 1
                pyramid_score = 5.0 * (1.0 - cat_count / max(max_cat_count, 1))

            # --- Submission distribution score (0~30 points) ---
            dist_score = 0.0
            ds_sub_count = dataset_submission_counts.get(ds_id, 0)
            # Favor datasets with zero or few submissions
            dist_score = 30.0 * (1.0 / (1.0 + ds_sub_count))

            # --- Quality score from OS/IS Sharpe (0~30 points) ---
            quality_score = 0.0
            ds_sharpe_info = dataset_sharpe_map.get(ds_id, {})
            ds_sharpe = ds_sharpe_info.get('sharpe_ratio') if ds_sharpe_info else None
            ds_os_count = ds_sharpe_info.get('count', 0) if ds_sharpe_info else 0

            if ds_sharpe is not None:
                if ds_sharpe < 0:
                    quality_score = 0.0  # negative sharpe = poor quality
                elif mean_sharpe > 0 and ds_sharpe > mean_sharpe:
                    quality_score = 30.0  # above average = excellent
                elif mean_sharpe > 0:
                    quality_score = 15.0 * (ds_sharpe / mean_sharpe)  # proportional
                else:
                    quality_score = 15.0  # mean is 0 or negative, any positive sharpe is decent

            total_score = pyramid_score + dist_score + quality_score

            scored_datasets.append({
                'dataset_id': ds_id,
                'dataset_name': ds_name,
                'category': ds_category,
                'subcategory': ds_subcategory,
                'total_score': round(total_score, 2),
                'pyramid_score': round(pyramid_score, 2),
                'distribution_score': round(dist_score, 2),
                'quality_score': round(quality_score, 2),
                'category_lit': cat_lit,
                'category_alpha_count': cat_count,
                'category_need_to_light': need,
                'dataset_submissions_this_quarter': ds_sub_count,
                'os_is_sharpe': round(ds_sharpe, 4) if ds_sharpe is not None else None,
                'os_is_count': ds_os_count,
            })

        # Sort by total_score descending
        scored_datasets.sort(key=lambda x: x['total_score'], reverse=True)

        # Build category summary
        category_summary = {}
        for cat_id, is_lit in category_pass_status.items():
            category_summary[cat_id] = {
                'lit': is_lit,
                'alpha_count': category_alpha_counts.get(cat_id, 0),
                'need_to_light': category_need_count.get(cat_id, 0),
            }

        # Count unlit categories
        unlit_count = sum(1 for v in category_pass_status.values() if not v)
        lit_count = sum(1 for v in category_pass_status.values() if v)

        return {
            'region': region,
            'delay': delay,
            'universe': universe,
            'recommendations': scored_datasets[:top_n],
            'total_datasets_scored': len(scored_datasets),
            'category_summary': category_summary,
            'pyramid_status': {
                'lit_categories': lit_count,
                'unlit_categories': unlit_count,
                'total_categories': lit_count + unlit_count,
            },
            'scoring_weights': {
                'pyramid_lighting': '0~40 pts (unlit categories get priority)',
                'submission_distribution': '0~30 pts (less-used datasets get priority)',
                'dataset_quality': '0~30 pts (higher OS/IS Sharpe = better)',
            }
        }
            
    async def run_selection(
        self,
        selection: str,
        instrument_type: str = "EQUITY",
        region: str = "USA",
        delay: int = 1,
        selection_limit: int = 1000,
        selection_handling: str = "POSITIVE"
    ) -> Dict[str, Any]:
        """Run a selection query to filter instruments."""
        await self.ensure_authenticated()
        
        try:
            selection_data = {
                "selection": selection,
                "instrumentType": instrument_type,
                "region": region,
                "delay": delay,
                "selectionLimit": selection_limit,
                "selectionHandling": selection_handling
            }
            
            response = await self._request('GET', f"{self.base_url}/simulations/super-selection", params=selection_data)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to run selection: {str(e)}", "ERROR")
            raise

    async def get_user_profile(self, user_id: str = "self") -> Dict[str, Any]:
        """Get user profile information."""
        await self.ensure_authenticated()
        
        try:
            response = await self._request('GET', f"{self.base_url}/users/{user_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get user profile: {str(e)}", "ERROR")
            raise
            
    async def get_documentations(self) -> Dict[str, Any]:
        """Get available documentations and learning materials."""
        await self.ensure_authenticated()
        
        try:
            response = await self._request('GET', f"{self.base_url}/tutorials")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get documentations: {str(e)}", "ERROR")
            raise
            
    async def get_messages(self, limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
        """Get messages for the current user with optional pagination.
        
        This function retrieves messages, processes their descriptions to extract
        and format embedded JSON, and handles file attachments by saving them locally.
        """
        from typing import Tuple
        
        def process_description(desc: str, message_id: str) -> Tuple[str, List[str]]:
            """
            Processes message description to handle HTML, embedded images, and JSON.
            """
            attachments = []
            
            # Handle embedded images
            soup = BeautifulSoup(desc, 'html.parser')
            for idx, img_tag in enumerate(soup.find_all('img')):
                src = img_tag.get('src', '')
                if src.startswith('data:image'):
                    try:
                        # Extract image data
                        header, encoded = src.split(',', 1)
                        ext = header.split(';')[0].split('/')[1]
                        safe_ext = re.sub(r'[^a-zA-Z0-9]', '', ext)
                        
                        # Decode and save image
                        content = base64.b64decode(encoded)
                        file_name = f"{message_id}_img_{idx}.{safe_ext}"
                        with open(file_name, "wb") as f:
                            f.write(content)
                        
                        # Update HTML and add attachment info
                        img_tag['src'] = file_name
                        attachments.append(f"Saved embedded image to ./{file_name}")
                        
                    except Exception as e:
                        attachments.append(f"Could not process embedded image: {e}")
            
            desc = str(soup)

            # Handle JSON content
            try:
                json_part_match = re.search(r'```json\n({.*?})\n```', desc, re.DOTALL)
                if json_part_match:
                    json_str = json_part_match.group(1)
                    desc = desc.replace(json_part_match.group(0), "").strip()
                    
                    try:
                        data = json.loads(json_str)
                        formatted_json = json.dumps(data, indent=2)
                        desc += f"\n\n---\n**Details**\n```json\n{formatted_json}\n```"
                    except json.JSONDecodeError:
                        desc += f"\n\n---\n**Details (raw)**\n{json_str}"
            except Exception:
                pass
                
            return desc, attachments

        await self.ensure_authenticated()
        
        try:
            params = {"limit": limit, "offset": offset}
            params = {k: v for k, v in params.items() if v is not None}
            
            response = await self._request('GET', f"{self.base_url}/users/self/messages", params=params)
            response.raise_for_status()
            messages_data = response.json()
            
            # Process descriptions and attachments
            for msg in messages_data.get("results", []):
                try:
                    msg_id = msg.get("id", "unknown_id")
                    new_desc, attachments = process_description(msg.get("description", ""), msg_id)
                    msg["description"] = new_desc
                    if attachments:
                        msg["attachments_info"] = attachments
                except Exception as e:
                    self.log(f"Error processing message {msg.get('id')}: {e}", "ERROR")

            return messages_data
            
        except Exception as e:
            self.log(f"Failed to get messages: {str(e)}", "ERROR")
            raise

    async def get_glossary_terms(self, email: str, password: str) -> List[Dict[str, str]]:
        """Get glossary terms from forum."""
        try:
            return await forum_client.get_glossary_terms(email, password)
        except Exception as e:
            self.log(f"Failed to get glossary terms: {str(e)}", "ERROR")
            raise

    async def search_forum_posts(self, email: str, password: str, search_query: str, 
                                 max_results: int = 50) -> Dict[str, Any]:
        """Search forum posts."""
        try:
            rate_limited = await self._rate_limit_forum_op("search_forum_posts")
            if rate_limited:
                return {
                    **rate_limited,
                    'operation': 'search_forum_posts',
                    'search_query': search_query,
                    'max_results': max_results,
                }
            return await forum_client.search_forum_posts(email, password, search_query, max_results)
        except Exception as e:
            self.log(f"Failed to search forum posts: {str(e)}", "ERROR")
            raise


    async def read_forum_post(self, email: str, password: str, article_id: str, 
                              include_comments: bool = True) -> Dict[str, Any]:
        """Get forum post."""
        try:
            rate_limited = await self._rate_limit_forum_op("read_forum_post")
            if rate_limited:
                return {
                    **rate_limited,
                    'operation': 'read_forum_post',
                    'article_id': article_id,
                    'include_comments': include_comments,
                }
            return await forum_client.read_full_forum_post(email, password, article_id, include_comments)
        except Exception as e:
            self.log(f"Failed to read forum post: {str(e)}", "ERROR")
            raise
    
    async def get_alpha_yearly_stats(self, alpha_id: str) -> Dict[str, Any]:
        """Get yearly statistics for an alpha."""
        await self.ensure_authenticated()
        
        max_retries = 5
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                self.log(f"Attempting to get yearly stats for alpha {alpha_id} (attempt {attempt + 1}/{max_retries})", "INFO")
                
                response = await self._request('GET', f"{self.base_url}/alphas/{alpha_id}/recordsets/yearly-stats")
                response.raise_for_status()
                
                text = (response.text or "").strip()
                if not text:
                    if attempt < max_retries - 1:
                        self.log(f"Empty yearly stats response for {alpha_id}, retrying...", "WARNING")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 1.5
                        continue
                    else:
                        return {}
                
                try:
                    stats_data = response.json()
                    if stats_data:
                        return stats_data
                    else:
                        if attempt < max_retries - 1:
                            self.log(f"Empty yearly stats JSON for {alpha_id}, retrying...", "WARNING")
                            await asyncio.sleep(retry_delay)
                            retry_delay *= 1.5
                            continue
                        else:
                            return {}
                            
                except json.JSONDecodeError as parse_err:
                    if attempt < max_retries - 1:
                        self.log(f"Yearly stats JSON parse failed for {alpha_id}, retrying...", "WARNING")
                        await asyncio.sleep(retry_delay)
                        retry_delay *= 1.5
                        continue
                    else:
                        raise
                        
            except requests.RequestException as e:
                if attempt < max_retries - 1:
                    self.log(f"Failed to get yearly stats for {alpha_id}, retrying: {e}", "WARNING")
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 1.5
                    continue
                else:
                    raise
        
        return {}
        
    async def get_production_correlation(self, alpha_id: str) -> Dict[str, Any]:
        """Get production correlation data for an alpha.
        
        Polls every 30 seconds for up to 1 hour to handle platform rate-limiting.
        For super alphas, the platform may return an empty body (HTTP 200) for a few
        minutes after simulation completes while it computes the correlation data.
        The polling loop handles this by retrying until data is available.
        Returns {'status': 'pending', ...} after max_wait_seconds if data never arrives.
        """
        await self.ensure_authenticated()
        
        max_wait_seconds = 3600  # 1 hour total
        poll_interval = 30       # 30 seconds per attempt (matches reference implementation)
        start_time = time.time()
        attempt = 0
        consecutive_empty = 0    # track consecutive empty-body responses
        
        while True:
            elapsed = time.time() - start_time
            if elapsed >= max_wait_seconds:
                self.log(f"Production correlation timeout after {int(elapsed)}s for {alpha_id}", "WARNING")
                return {
                    'status': 'pending',
                    'message': (
                        f"Production correlation data for alpha {alpha_id} was not available "
                        f"after {int(elapsed)}s of polling. The platform may still be computing "
                        "it. Please retry check_correlation in a few minutes."
                    ),
                    'max': None,
                    'records': [],
                }
            
            attempt += 1
            try:
                if attempt % 5 == 1:
                    self.log(f"[PC等待] 正在等待 Alpha {alpha_id} 的 PC 数据 (第 {attempt} 次查询, 已等待 {int(elapsed)}s)", "INFO")
                
                response = await self._request('GET', f"{self.base_url}/alphas/{alpha_id}/correlations/prod")
                response.raise_for_status()
                
                text = (response.text or "").strip()
                if not text:
                    consecutive_empty += 1
                    if consecutive_empty == 3:
                        # Platform is still computing — log once so users understand the wait
                        self.log(
                            f"[PC计算中] Alpha {alpha_id} 的生产相关性数据尚未就绪 "
                            f"(已收到 {consecutive_empty} 次空响应). "
                            "平台正在计算中，通常需要 1-5 分钟，请耐心等待...",
                            "INFO"
                        )
                    await asyncio.sleep(poll_interval)
                    continue
                
                # Got a non-empty response — reset empty counter
                consecutive_empty = 0
                try:
                    corr_data = response.json()
                    if corr_data and corr_data.get('max') is not None:
                        self.log(f"[PC成功] Alpha {alpha_id} PC={corr_data['max']} (第 {attempt} 次查询, 耗时 {int(elapsed)}s)", "INFO")
                        return corr_data
                except json.JSONDecodeError:
                    pass
                    
            except requests.RequestException as e:
                self.log(f"Failed to get production correlation for {alpha_id}: {e}", "WARNING")
            
            await asyncio.sleep(poll_interval)

    async def get_self_correlation(self, alpha_id: str) -> Dict[str, Any]:
        """Calculate self-correlation locally by comparing target alpha PnL against OS alpha pool.
        
        Avoids calling /correlations/self platform API by computing correlation locally.
        Follows the same approach as calculate_sc_locally in the reference implementation:
        - Fetches PnL for target alpha and all OS alphas
        - Computes pairwise correlation on the last 4 years of daily returns
        - Returns max correlation against the OS pool
        """
        await self.ensure_authenticated()

        def pnl_response_to_series(aid: str, pnl_data: dict) -> Optional[pd.Series]:
            """Convert a raw PnL API response dict to a pandas Series indexed by date."""
            try:
                records = pnl_data.get('records', [])
                schema = pnl_data.get('schema', {}).get('properties', [])
                if not records or not schema:
                    return None
                cols = [p['name'] for p in schema]
                df = pd.DataFrame(records, columns=cols)
                df['date'] = pd.to_datetime(df['date'])
                df.set_index('date', inplace=True)
                if 'pnl' not in df.columns:
                    return None
                return df['pnl'].rename(aid)
            except Exception:
                return None

        try:
            # Fetch target alpha PnL
            target_pnl_data = await self.get_alpha_pnl(alpha_id)
            target_series = pnl_response_to_series(alpha_id, target_pnl_data)
            if target_series is None:
                self.log(f"Could not parse PnL for target alpha {alpha_id}, falling back to empty result", "WARNING")
                return {}

            # Fetch OS alphas list
            os_alphas_data = await self.get_user_alphas(stage='OS', limit=100)
            os_ids = [a['id'] for a in os_alphas_data.get('results', []) if a.get('id') != alpha_id]

            if not os_ids:
                self.log(f"No OS alphas found; self-correlation for {alpha_id} is 0", "INFO")
                return {'max': 0.0, 'records': [], 'local_calculation': True, 'pool_size': 0}

            # Fetch PnL for OS alphas concurrently (rate-limited to 5 parallel)
            fetch_sem = asyncio.Semaphore(5)

            async def fetch_pnl_safe(oid: str):
                async with fetch_sem:
                    try:
                        data = await self.get_alpha_pnl(oid)
                        return oid, pnl_response_to_series(oid, data)
                    except Exception:
                        return oid, None

            tasks = [fetch_pnl_safe(oid) for oid in os_ids[:100]]
            fetch_results = await asyncio.gather(*tasks)

            pool_series = [target_series]
            for res in fetch_results:
                if isinstance(res, tuple) and res[1] is not None:
                    pool_series.append(res[1])

            if len(pool_series) < 2:
                self.log(f"Insufficient PnL data for self-correlation pool (got {len(pool_series)-1} OS alphas)", "INFO")
                return {'max': 0.0, 'records': [], 'local_calculation': True, 'pool_size': 0}

            # Combine PnL series, forward-fill gaps, diff to get daily returns
            combined = pd.concat(pool_series, axis=1).ffill()
            rets = combined.diff()

            # Use last 4 years of returns (mirrors reference calculate_sc_locally)
            last_date = rets.index.max()
            rets = rets[rets.index > last_date - pd.DateOffset(years=4)]

            corr_matrix = rets.corr()
            if alpha_id not in corr_matrix.columns:
                return {'max': 0.0, 'records': [], 'local_calculation': True, 'pool_size': len(pool_series) - 1}

            sc_series = corr_matrix[alpha_id].drop(alpha_id).dropna()
            max_corr = float(sc_series.max()) if not sc_series.empty else 0.0

            # Top-10 correlated alphas as records (mirrors API response structure)
            records = [
                {'id': oid, 'correlation': float(val)}
                for oid, val in sc_series.nlargest(10).items()
            ]

            self.log(f"[SC本地] Alpha {alpha_id}: max_self_corr={max_corr:.4f} (pool={len(pool_series)-1} OS alphas)", "INFO")
            return {
                'max': max_corr,
                'records': records,
                'local_calculation': True,
                'pool_size': len(pool_series) - 1
            }

        except Exception as e:
            self.log(f"Failed to calculate self-correlation locally: {str(e)}", "ERROR")
            raise

    async def check_correlation(self, alpha_id: str, correlation_type: str = "production", threshold: float = 0.7) -> Dict[str, Any]:
        """ Only where all IS metrics PASS to Check alpha correlation, Check alpha correlation against production alphas, self alphas, or both."""
        await self.ensure_authenticated()
        
        if self.redis_client:
            try:
                lock_key = "rate_limit:check_correlation"
                if not self.redis_client.set(lock_key, "locked", ex=180, nx=True):
                    ttl = self.redis_client.ttl(lock_key)
                    return {
                        'alpha_id': alpha_id,
                        'status': 'rate_limited',
                        'message': f"Rate limit exceeded. Please wait {ttl} seconds before trying again.",
                        'retry_after': ttl,
                        'correlation_type': correlation_type,
                        'threshold': threshold
                    }
            except Exception as e:
                self.log(f"Rate limiting for check_correlation failed, proceeding without rate limit: {str(e)}", "WARNING")
        
        try:
            results = {
                'alpha_id': alpha_id,
                'threshold': threshold,
                'correlation_type': correlation_type,
                'checks': {}
            }
            
            # Determine which correlations to check
            check_types = []
            if correlation_type == "both":
                check_types = ["production", "self"]
            else:
                check_types = [correlation_type]
            
            all_passed = True
            
            for check_type in check_types:
                if check_type == "production":
                    correlation_data = await self.get_production_correlation(alpha_id)
                    
                    # Handle pending/data-not-yet-available case (super alphas, fresh simulations)
                    if correlation_data and correlation_data.get('status') == 'pending':
                        results['checks'][check_type] = {
                            'max_correlation': None,
                            'passes_check': None,
                            'status': 'pending',
                            'message': correlation_data.get('message', ''),
                            'correlation_data': correlation_data,
                        }
                        results['all_passed'] = None
                        results['status'] = 'pending'
                        results['message'] = correlation_data.get('message', '')
                        return results

                    if (
                        correlation_data
                        and isinstance(correlation_data.get('records'), list)
                        and len(correlation_data['records']) > 0
                        and correlation_data.get('max') is not None
                    ):
                        max_correlation = correlation_data['max']
                        passes_check = max_correlation < threshold
                        results['checks'][check_type] = {
                            'max_correlation': max_correlation,
                            'passes_check': passes_check,
                            'correlation_data': correlation_data
                        }
                        if not passes_check:
                            all_passed = False
                            results["all_passed"] = all_passed
                            return results
                    else:
                        # Data returned but has no usable records/max (empty or malformed).
                        # Return None to signal "data unavailable" rather than faking max=0.
                        results['checks'][check_type] = {
                            'max_correlation': None,
                            'passes_check': None,
                            'status': 'data_unavailable',
                            'message': (
                                'Production correlation data is unavailable for this alpha. '
                                'This may be a newly-created super alpha where the platform '
                                'has not yet computed the correlation. Please retry in a few minutes.'
                            ),
                            'correlation_data': correlation_data,
                        }
                        results['all_passed'] = None
                        results['status'] = 'data_unavailable'
                        return results
                elif check_type == "self":
                    correlation_data = await self.get_self_correlation(alpha_id)
                else:
                    continue
                
                # Analyze correlation data (self-correlation path)
                if (
                    correlation_data
                    and isinstance(correlation_data.get('records'), list)
                    and len(correlation_data['records']) > 0
                    and correlation_data.get('max') is not None
                ):
                    max_correlation = correlation_data['max']
                    passes_check = max_correlation < threshold
                else:
                    max_correlation = 0
                    passes_check = False
                
                results['checks'][check_type] = {
                    'max_correlation': max_correlation,
                    'passes_check': passes_check,
                    'correlation_data': correlation_data
                }
                
                if not passes_check:
                    all_passed = False
            
            results['all_passed'] = all_passed
            
            return results
            
        except Exception as e:
            self.log(f"Failed to check correlation: {str(e)}", "ERROR")
            raise

    async def get_submission_check(self, alpha_id: str) -> Dict[str, Any]:
        """Comprehensive pre-submission check."""
        await self.ensure_authenticated()
        
        try:
            # This endpoint might not exist, so we simulate it by calling other functions
            # In a real scenario, this would be a single API call
            
            pnl_data = await self.get_alpha_pnl(alpha_id)
            yearly_stats = await self.get_alpha_yearly_stats(alpha_id)
            correlation = await self.check_correlation(alpha_id)
            
            return {
                "pnl_summary": pnl_data.get("pnlSummary", {}),
                "yearly_stats": yearly_stats,
                "correlation": correlation
            }
        except Exception as e:
            self.log(f"Failed submission check: {str(e)}", "ERROR")
            raise

    async def set_alpha_properties(self, alpha_id: str, name: Optional[str] = None, 
                                   color: Optional[str] = None, tags: Optional[List[str]] = None,
                                   descriptions: str = "None",
                                   selection_description: Optional[str] = None,
                                   combo_description: Optional[str] = None
                                   ) -> Dict[str, Any]:
        """Update alpha properties (name, color, tags, descriptions)."""
        await self.ensure_authenticated()
        
        try:
            payload = {
                "color": color,
                "name": name,
                "tags": tags if tags is not None else [],
                "regular": {"description": descriptions}
            }
            if selection_description is not None:
                payload["selection"] = {"description": selection_description}
            if combo_description is not None:
                payload["combo"] = {"description": combo_description}
            
            response = await self._request('PATCH', f"{self.base_url}/alphas/{alpha_id}", json=payload)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to set alpha properties: {str(e)}", "ERROR")
            raise

    async def get_record_sets(self, alpha_id: str) -> Dict[str, Any]:
        """List available record sets for an alpha."""
        await self.ensure_authenticated()
        
        try:
            response = await self._request('GET', f"{self.base_url}/alphas/{alpha_id}/recordsets")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get record sets: {str(e)}", "ERROR")
            raise

    async def get_record_set_data(self, alpha_id: str, record_set_name: str) -> Dict[str, Any]:
        """Get data from a specific record set."""
        await self.ensure_authenticated()
        
        try:
            response = await self._request('GET', f"{self.base_url}/alphas/{alpha_id}/recordsets/{record_set_name}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get record set data: {str(e)}", "ERROR")
            raise

    async def get_user_activities(self, user_id: str, grouping: Optional[str] = None) -> Dict[str, Any]:
        """Get user activity diversity data."""
        await self.ensure_authenticated()
        
        try:
            params = {}
            if grouping:
                params['grouping'] = grouping
            
            response = await self._request('GET', f"{self.base_url}/users/{user_id}/activities", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get user activities: {str(e)}", "ERROR")
            raise

    async def get_pyramid_multipliers(self) -> Dict[str, Any]:
        """Get current pyramid multipliers showing BRAIN's encouragement levels."""
        await self.ensure_authenticated()
        
        try:
            response = await self._request('GET', f"{self.base_url}/users/self/activities/pyramid-multipliers")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get pyramid multipliers: {str(e)}", "ERROR")
            raise

    async def get_pyramid_alphas(self, start_date: Optional[str] = None,
                               end_date: Optional[str] = None) -> Dict[str, Any]:
        """Get user's current alpha distribution across pyramid categories.
        Defaults to the current quarter if no dates are provided."""
        await self.ensure_authenticated()
        
        try:
            # Default to current quarter boundaries
            if not start_date or not end_date:
                now = datetime.utcnow()
                q_start_month = (now.month - 1) // 3 * 3 + 1
                quarter_start = datetime(now.year, q_start_month, 1)
                if q_start_month + 3 > 12:
                    quarter_end = datetime(now.year + 1, 1, 1)
                else:
                    quarter_end = datetime(now.year, q_start_month + 3, 1)
                if not start_date:
                    start_date = quarter_start.strftime("%Y-%m-%d")
                if not end_date:
                    end_date = quarter_end.strftime("%Y-%m-%d")

            params = {}
            if start_date:
                params["startDate"] = start_date
            if end_date:
                params["endDate"] = end_date
                
            response = await self._request('GET', f"{self.base_url}/users/self/activities/pyramid-alphas", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get pyramid alphas: {str(e)}", "ERROR")
            raise
            
    async def get_user_competitions(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """Get list of competitions that the user is participating in."""
        await self.ensure_authenticated()
        
        try:
            if not user_id:
                # Get current user ID if not specified
                user_response = await self._request('GET', f"{self.base_url}/users/self")
                if user_response.status_code == 200:
                    user_data = user_response.json()
                    user_id = user_data.get('id')
                else:
                    user_id = 'self'
            
            response = await self._request('GET', f"{self.base_url}/users/{user_id}/competitions")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get user competitions: {str(e)}", "ERROR")
            raise
            
    async def get_competition_details(self, competition_id: str) -> Dict[str, Any]:
        """Get detailed information about a specific competition."""
        await self.ensure_authenticated()
        
        try:
            response = await self._request('GET', f"{self.base_url}/competitions/{competition_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get competition details: {str(e)}", "ERROR")
            raise
            
    async def get_competition_agreement(self, competition_id: str) -> Dict[str, Any]:
        """Get the rules, terms, and agreement for a specific competition."""
        await self.ensure_authenticated()
        
        try:
            response = await self._request('GET', f"{self.base_url}/competitions/{competition_id}/agreement")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get competition agreement: {str(e)}", "ERROR")
            raise

    async def get_platform_setting_options(self) -> Dict[str, Any]:
        """Get available instrument types, regions, delays, and universes with Redis caching (1 day TTL)."""
        await self.ensure_authenticated()
        
        try:
            # Generate cache key (no parameters needed as this endpoint returns fixed platform settings)
            cache_key = self._generate_cache_key('platform_settings', {})
            
            # Try to get from cache
            cached_data = self._get_cached_data(cache_key)
            if cached_data:
                return {**cached_data, 'from_cache': True}
            
            # Use OPTIONS method on simulations endpoint to get configuration options
            response = await self._request('OPTIONS', f"{self.base_url}/simulations")
            response.raise_for_status()
            
            # Parse the settings structure from the response
            settings_data = response.json()
            settings_options = settings_data['actions']['POST']['settings']['children']
            
            # Extract instrument configuration options
            instrument_type_data = {}
            region_data = {}
            universe_data = {}
            delay_data = {}
            neutralization_data = {}
            
            # Parse each setting type
            for key, setting in settings_options.items():
                if setting['type'] == 'choice':
                    if setting['label'] == 'Instrument type':
                        instrument_type_data = setting['choices']
                    elif setting['label'] == 'Region':
                        region_data = setting['choices']['instrumentType']
                    elif setting['label'] == 'Universe':
                        universe_data = setting['choices']['instrumentType']
                    elif setting['label'] == 'Delay':
                        delay_data = setting['choices']['instrumentType']
                    elif setting['label'] == 'Neutralization':
                        neutralization_data = setting['choices']['instrumentType']
            
            # Build comprehensive instrument options
            data_list = []
            
            for instrument_type in instrument_type_data:
                for region in region_data[instrument_type['value']]:
                    for delay in delay_data[instrument_type['value']]['region'][region['value']]:
                        row = {
                            'InstrumentType': instrument_type['value'],
                            'Region': region['value'],
                            'Delay': delay['value']
                        }
                        row['Universe'] = [
                            item['value'] for item in universe_data[instrument_type['value']]['region'][region['value']]
                        ]
                        row['Neutralization'] = [
                            item['value'] for item in neutralization_data[instrument_type['value']]['region'][region['value']]
                        ]
                        data_list.append(row)
            
            # Return structured data
            result = {
                'instrument_options': data_list,
                'total_combinations': len(data_list),
                'instrument_types': [item['value'] for item in instrument_type_data],
                'regions_by_type': {
                    item['value']: [r['value'] for r in region_data[item['value']]]
                    for item in instrument_type_data
                },
                'from_cache': False
            }
            
            # Cache the data (1 day TTL)
            self._set_cached_data(cache_key, result, ttl=604800)
            
            return result
            
        except Exception as e:
            self.log(f"Failed to get instrument options: {str(e)}", "ERROR")
            raise
            
    async def performance_comparison(self, alpha_id: str, team_id: Optional[str] = None, 
                                     competition: Optional[str] = None) -> Dict[str, Any]:
        """Get performance comparison data for an alpha."""
        await self.ensure_authenticated()
        
        try:
            params = {"teamId": team_id, "competition": competition}
            params = {k: v for k, v in params.items() if v is not None}
            
            response = await self._request('GET', f"{self.base_url}/alphas/{alpha_id}/performance-comparison", params=params)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get performance comparison: {str(e)}", "ERROR")
            raise
            
    # --- Helper function for data flattening ---
    
    async def expand_nested_data(self, data: List[Dict[str, Any]], preserve_original: bool = True) -> List[Dict[str, Any]]:
        """Flatten complex nested data structures into tabular format."""
        try:
            df = pd.json_normalize(data, sep='_')
            if preserve_original:
                original_df = pd.DataFrame(data)
                df = pd.concat([original_df, df], axis=1)
                df = df.loc[:,~df.columns.duplicated()]
            return df.to_dict(orient='records')
        except Exception as e:
            self.log(f"Failed to expand nested data: {str(e)}", "ERROR")
            raise
            
    # --- New documentation endpoint ---
    
    async def get_documentation_page(self, page_id: str) -> Dict[str, Any]:
        """Retrieve detailed content of a specific documentation page/article."""
        await self.ensure_authenticated()
        
        try:
            response = await self._request('GET', f"{self.base_url}/tutorial-pages/{page_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            self.log(f"Failed to get documentation page: {str(e)}", "ERROR")
            raise

brain_client = BrainApiClient()

# --- Configuration Management ---

def _resolve_config_path(for_write: bool = False) -> str:
    """
    Resolve the configuration file path.
    
    Checks for a file specified by the MCP_CONFIG_FILE environment variable,
    then falls back to ~/.brain_mcp_config.json. If for_write is True,
    it ensures the directory exists.
    """
    if 'MCP_CONFIG_FILE' in os.environ:
        return os.environ['MCP_CONFIG_FILE']
    
    config_path = Path(__file__).parent / "user_config.json"
    
    if for_write:
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
        except (IOError, OSError) as e:
            logger.warning(f"Could not create config directory {config_path.parent}: {e}")
            # Fallback to a temporary file if home is not writable
            import tempfile
            return tempfile.NamedTemporaryFile(delete=False).name
            
    return str(config_path)

def _load_dotenv_into_environ():
    """Load .env into environment using python-dotenv if available; fallback to simple parser."""
    try:
        from dotenv import load_dotenv, find_dotenv
        env_path = find_dotenv(usecwd=True)
        if env_path:
            load_dotenv(env_path, override=False)
        else:
            # Try repo root relative to this file
            candidate = Path(__file__).parent / ".env"
            if candidate.exists():
                load_dotenv(candidate, override=False)
    except Exception:
        # Fallback: very simple .env parser (KEY=VALUE, no export, ignores quotes)
        try:
            candidate = Path(__file__).parent / ".env"
            if candidate.exists():
                for line in candidate.read_text().splitlines():
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if '=' not in line:
                        continue
                    k, v = line.split('=', 1)
                    k = k.strip()
                    v = v.strip().strip('"').strip("'")
                    os.environ.setdefault(k, v)
        except Exception:
            pass

def load_config() -> Dict[str, Any]:
    """Load configuration from file and overlay environment variables (from .env if present)."""
    config: Dict[str, Any] = {}
    config_file = _resolve_config_path()
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r') as f:
                config = json.load(f) or {}
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Error loading config file {config_file}: {e}")

    # Load .env into environment (no override of already-set env)
    _load_dotenv_into_environ()

    # Overlay credentials from env if available
    env_email = os.getenv("CREDENTIALS_EMAIL")
    env_password = os.getenv("CREDENTIALS_PASSWORD")
    if env_email or env_password:
        creds = dict(config.get("credentials", {}))
        if env_email:
            creds["email"] = env_email
        if env_password:
            creds["password"] = env_password
        config["credentials"] = creds

    return config

def save_config(config: Dict[str, Any]):
    """Save configuration to file using the resolved config path.
    
    This function now uses the write-enabled path resolver to handle
    cases where the default home directory is not writable.
    """
    config_file = _resolve_config_path(for_write=True)
    try:
        with open(config_file, 'w') as f:
            json.dump(config, f, indent=2)
    except IOError as e:
        logger.error(f"Error saving config file to {config_file}: {e}")

# --- MCP Tool Definitions ---

_MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
try:
    _MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
except Exception:
    _MCP_PORT = 8000
_MCP_STREAMABLE_HTTP_PATH = os.getenv("MCP_STREAMABLE_HTTP_PATH", "/mcp")

mcp = FastMCP(
    "brain-platform-mcp",
    "A server for interacting with the WorldQuant BRAIN platform",
    host=_MCP_HOST,
    port=_MCP_PORT,
    streamable_http_path=_MCP_STREAMABLE_HTTP_PATH,
)

# Add health check endpoint for container monitoring
from mcp.server.fastmcp import Context
from starlette.responses import JSONResponse

@mcp.custom_route('/health', methods=['GET'])
async def health_check(context: Context):
    """Health check endpoint for Docker container monitoring."""
    return JSONResponse({
        "status": "healthy",
        "service": "brain-platform-mcp",
        "timestamp": datetime.utcnow().isoformat(),
        "redis_connected": brain_client.redis_client is not None
    })

@mcp.tool()
async def authenticate() -> Dict[str, Any]:
    """
    Authenticate with WorldQuant BRAIN platform.
    
    This is the first step in any BRAIN workflow. You must authenticate before using any other tools.
    
    Args:
        None
    Returns:
        Authentication result with user info and permissions
    """
    try:
        # Load config to get credentials if not provided
        config = load_config()
        credentials = config.get("credentials", {})
        email = credentials.get("email")
        password = credentials.get("password")
        
        auth_result = await brain_client.authenticate(email, password)
        
        # # Save successful credentials
        # if auth_result.get('status') == 'authenticated':
        #     if 'credentials' not in config:
        #         config['credentials'] = {}
        #     config['credentials']['email'] = email
        #     config['credentials']['password'] = password
        #     save_config(config)
            
        return auth_result
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def manage_config(action: str = "get", settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Manage configuration settings - get or update configuration.
    
    Args:
        action: Action to perform ("get" to retrieve config, "set" to update config)
        settings: Configuration settings to update (required when action="set")
    
    Returns:
        Current or updated configuration including authentication status
    """
    config = load_config()
    
    if action == "set" and settings:
        config.update(settings)
        save_config(config)
        
    is_authed = await brain_client.is_authenticated()
    config['isAuthenticated'] = is_authed
    
    # Mask password for security
    if 'password' in config:
        config['password'] = '********'
        
    return config

# --- Simulation Tools ---

@mcp.tool()
async def create_simulation(
    type: str = "REGULAR",
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
    decay: int = 4,
    neutralization: str = "SUBINDUSTRY",
    truncation: float = 0.08,
    test_period: str = "P0Y0M",
    nan_handling: str = "ON",
    alpha_expression: Optional[str] = None,
    combo: Optional[str] = None,
    selection: Optional[str] = None,
    pasteurization: str = "ON",
    max_trade: str = "OFF",
    selection_handling: str = "POSITIVE",
    selection_limit: int = 1000,
    component_activation: str = "IS",
) -> Dict[str, Any]:
    """
    Create a new simulation on BRAIN platform.
    
    This tool creates and starts a simulation with your alpha code. Use this after you have your alpha formula ready.
    if field type=VECTOR should deal with vec_ suffer vec_*(FIELD)
    Args:
        type: Simulation type ("REGULAR" or "SUPER")
        region: Market region (e.g., "USA")
        universe: Universe of stocks (e.g., "TOP3000")
        delay: Data delay (0 or 1)
        decay: Decay value for the simulation
        neutralization: Neutralization method
        truncation: Truncation value
        test_period: Test period (e.g., "P0Y0M" for 1 year 6 months)
        nan_handling: NaN handling method
        alpha_expression: Alpha expression code (for REGULAR type)
        combo: Combo code (for SUPER type)
        selection: Selection code (for SUPER type)
    
    Returns:
        Simulation creation result with ID and location
    """
    unit_handling = "VERIFY"
    instrument_type = "EQUITY"
    visualization = False
    language = "FASTEXPR"
    try:
        settings = SimulationSettings(
            instrumentType=instrument_type,
            region=region,
            universe=universe,
            delay=delay,
            decay=decay,
            neutralization=neutralization,
            truncation=truncation,
            testPeriod=test_period,
            unitHandling=unit_handling,
            nanHandling=nan_handling,
            language=language,
            visualization=visualization,
            pasteurization=pasteurization,
            maxTrade=max_trade, 
            selectionHandling=selection_handling,
            selectionLimit=selection_limit,
            componentActivation=component_activation,
        )
        
        sim_data = SimulationData(
            type=type,
            settings=settings,
            regular=alpha_expression,
            combo=combo,
            selection=selection
        )
        
        return await brain_client.create_simulation(sim_data)
    except Exception as e:
        extra_info = ""
        error_msg = str(e)
        if error_msg and "does not support event inputs" in error_msg:
            extra_info = "If fields is vector type  should use vec_* operator with event input"
            return {"error": f"An unexpected error occurred: {str(e)}. {extra_info}"}
        return {"error": f"An unexpected error occurred: {str(e)}"}

# --- Alpha and Data Retrieval Tools ---

@mcp.tool()
async def get_alpha_details(alpha_id: str) -> Dict[str, Any]:
    """
    Get detailed information about an alpha.
    
    Args:
        alpha_id: The ID of the alpha to retrieve
    
    Returns:
        Detailed alpha information
    """
    try:
        return await brain_client.get_alpha_details(alpha_id)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_datasets(
    category: Optional[str] = None,
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
    theme: str = "false",
    search: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Get available datasets for research.
    
    Use this to discover what data is available for your alpha research.
    
    Args:
        category: Type of datesets (e.g., "news","sentiment","option")
        region: Market region (e.g., "USA")
        delay: Data delay (0 or 1)
        universe: Universe of stocks (e.g., "TOP3000")
        theme: Theme filter
    
    Returns:
        Available datasets
    """
    try:
        return await brain_client.get_datasets(category, region, delay, universe, theme, search)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_datafields(
    region: str,
    dataset_id: Optional[str],
    universe: str,
    delay: int = 1,
    data_type: str = "",
    search: Optional[str] = None,
    filter_sharpe: bool = True,
) -> Dict[str, Any]:
    """
    Get available data fields for alpha construction.
    
    Use this to find specific data fields you can use in your alpha formulas.
    By default, fields with OS/IS Sharpe ratio < 0 are filtered out to improve quality.
    
    Args:
        region: Market region (e.g., "USA"、"GLB"、"IND"、"ASI"、"CHN")
        delay: Data delay (0 or 1)
        universe: Universe of stocks (e.g., USA和GLB默认"TOP3000"、IND默认"TOP500"、ASI默认"MINVOL1M"、CHN默认"TOP2000U")
        dataset_id: Specific dataset ID to filter by
        data_type: Type of data (e.g., "MATRIX",'VECTOR','GROUP')
        search: Search term to filter fields
        filter_sharpe: Filter out fields with OS/IS Sharpe < 0 (default: True)
    
    Returns:
        Available data fields
    """
    instrument_type = "EQUITY"
    theme = "false"
    try:
        return await brain_client.get_datafields(instrument_type, region, delay, universe, theme, dataset_id, data_type, search, filter_sharpe)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_alpha_pnl(alpha_id: str) -> Dict[str, Any]:
    """
    Get PnL (Profit and Loss) data for an alpha.
    
    Args:
        alpha_id: The ID of the alpha
    
    Returns:
        PnL data for the alpha
    """
    try:
        return await brain_client.get_alpha_pnl(alpha_id)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_user_alphas(
    stage: str = "IS",
    limit: int = 30,
    offset: int = 0,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    submission_start_date: Optional[str] = None,
    submission_end_date: Optional[str] = None,
    order: Optional[str] = None,
    hidden: Optional[bool] = None,
    region: Optional[str] = None,
    status: Optional[str] = None,
    type: Optional[str] = None,
    is_super: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    Get user's alphas with advanced filtering, pagination, and sorting.

    This tool retrieves a list of your alphas, allowing for detailed filtering based on stage,
    creation date, submission date, visibility, region, status, type, and super alpha flag.
    It also supports pagination and custom sorting.

    Args:
        stage (str): The stage of the alphas to retrieve.
            - "IS": In-Sample (alphas that have not been submitted).
            - "OS": Out-of-Sample (alphas that have been submitted).
            Defaults to "IS".
        limit (int): The maximum number of alphas to return in a single request.
            For example, `limit=50` will return at most 50 alphas. Defaults to 30.
        offset (int): The number of alphas to skip from the beginning of the list.
            Used for pagination. For example, `limit=50, offset=50` will retrieve alphas 51-100.
            Defaults to 0.
        start_date (Optional[str]): The earliest creation date for the alphas to be included.
            Filters for alphas created on or after this date.
            Example format: "2023-01-01T00:00:00Z".
        end_date (Optional[str]): The latest creation date for the alphas to be included.
            Filters for alphas created before this date.
            Example format: "2023-12-31T23:59:59Z".
        submission_start_date (Optional[str]): The earliest submission date for the alphas.
            Only applies to "OS" alphas. Filters for alphas submitted on or after this date.
            Example format: "2024-01-01T00:00:00Z".
        submission_end_date (Optional[str]): The latest submission date for the alphas.
            Only applies to "OS" alphas. Filters for alphas submitted before this date.
            Example format: "2024-06-30T23:59:59Z".
        order (Optional[str]): The sorting order for the returned alphas.
            Prefix with a hyphen (-) for descending order.
            Examples: "name" (sort by name ascending), "-dateSubmitted" (sort by submission date descending).
        hidden (Optional[bool]): Filter alphas based on their visibility.
            - `True`: Only return hidden alphas.
            - `False`: Only return non-hidden alphas.
            If not provided, both hidden and non-hidden alphas are returned.
        region (Optional[str]): Filter alphas by region.
            Common values: "USA", "EUR", "ASI", "GLB", etc.
            If not provided, alphas from all regions are returned.
        status (Optional[str]): Filter alphas by their OS status.
            Common values: "ACTIVE", "SUPERSEDED", "UNSUBMITTED", etc.
            If not provided, alphas with any status are returned.
        type (Optional[str]): Filter alphas by their expression type.
            Common values: "REGULAR", "SUPER", etc.
            If not provided, alphas of all types are returned.
        is_super (Optional[bool]): Filter to only super alphas (True) or non-super alphas (False).
            If not provided, both super and non-super alphas are returned.

    Returns:
        Dict[str, Any]: A dictionary containing a list of alpha details under the 'results' key,
        along with pagination information. If an error occurs, it returns a dictionary with an 'error' key.
    """
    try:
        return await brain_client.get_user_alphas(
            stage=stage, limit=limit, offset=offset, start_date=start_date,
            end_date=end_date, submission_start_date=submission_start_date,
            submission_end_date=submission_end_date, order=order, hidden=hidden,
            region=region, status=status, alpha_type=type, is_super=is_super,
        )
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def submit_alpha(alpha_id: str) -> Dict[str, Any]:
    """
    Submit an alpha for production with pre-submission IS metrics check.
    
    Before submitting, this tool automatically checks the alpha's IS metrics against
    the following thresholds:
    - Sharpe > 1.58, Fitness > 1
    - Margin > 5bp for USA, otherwise > 15bp (hard floor 10bp)
    - Turnover between 5% and 20%
    - Returns > 5% and Returns > Drawdown
    - All IS checks must PASS (no FAIL)
    
    If the check fails, submission is blocked and failure details are returned.
    
    Args:
        alpha_id: The ID of the alpha to submit    
    Returns:
        Submission result including pre-check details
    """
    force = False
    try:
        if not force:
            # Fetch alpha details for IS metrics check
            alpha_details = await brain_client.get_alpha_details(alpha_id)
            check_result = brain_client.pre_submit_check(alpha_details)
            
            if not check_result['passed']:
                return {
                    "success": False,
                    "blocked": True,
                    "reason": "Pre-submission IS metrics check failed. Alpha does not meet submission thresholds.",
                    "check_result": check_result,
                }
            
            # Passed check — proceed to submit
            success = await brain_client.submit_alpha(alpha_id)
            return {
                "success": success,
                "blocked": False,
                "check_result": check_result,
            }
        else:
            success = await brain_client.submit_alpha(alpha_id)
            return {"success": success, "forced": True}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def value_factor_trendScore(start_date: str, end_date: str) -> Dict[str, Any]:
    """Compute and return the diversity score for REGULAR alphas in a submission-date window.
    This function calculate the diversity of the users' submission, by checking the diversity, we can have a good understanding on the valuefactor's trend.
    This MCP tool wraps BrainApiClient.value_factor_trendScore and always uses submission dates (OS).

    Inputs:
        - start_date: ISO UTC start datetime (e.g. '2025-08-14T00:00:00Z')
        - end_date: ISO UTC end datetime (e.g. '2025-08-18T23:59:59Z')
        - p_max: optional integer total number of pyramid categories for normalization

    Returns: compact JSON with diversity_score, N, A, P, P_max, S_A, S_P, S_H, per_pyramid_counts
    """
    try:
        return await brain_client.value_factor_trendScore(start_date=start_date, end_date=end_date)
    except Exception as e:
        return {"error": str(e)}

# --- Community and Events Tools ---

@mcp.tool()
async def get_events() -> Dict[str, Any]:
    """
    Get available events and competitions.
    
    Returns:
        Available events and competitions
    """
    try:
        return await brain_client.get_events()
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_leaderboard(user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Get leaderboard data.
    
    Args:
        user_id: Optional user ID to filter results
    
    Returns:
        Leaderboard data
    """
    try:
        return await brain_client.get_leaderboard(user_id)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}


# --- Forum Tools ---

@mcp.tool()
async def get_operators() -> Dict[str, Any]:
    """
    Get available operators for alpha creation.
    
    Returns:
        Dictionary containing operators list and count
    """
    try:
        operators = await brain_client.get_operators()
        if isinstance(operators, list):
            return {"results": operators, "count": len(operators)}
        return operators
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def run_selection(
    selection: str,
    instrument_type: str = "EQUITY",
    region: str = "USA",
    delay: int = 1,
    selection_limit: int = 1000,
    selection_handling: str = "POSITIVE",
) -> Dict[str, Any]:
    """
    Run a selection query to filter instruments.
    
    Args:
        selection: Selection criteria
        instrument_type: Type of instruments
        region: Geographic region
        delay: Delay setting
        selection_limit: Maximum number of results
        selection_handling: How to handle selection results
    
    Returns:
        Selection results
    """
    try:
        return await brain_client.run_selection(
            selection, instrument_type, region, delay, selection_limit, selection_handling
        )
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_user_profile(user_id: str = "self") -> Dict[str, Any]:
    """
    Get user profile information.
    
    Args:
        user_id: User ID (default: "self" for current user)
    
    Returns:
        User profile data
    """
    try:
        return await brain_client.get_user_profile(user_id)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_documentations() -> Dict[str, Any]:
    """
    Get available documentations and learning materials.
    
    Returns:
        List of documentations
    """
    try:
        return await brain_client.get_documentations()
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

# --- Message and Forum Tools ---

@mcp.tool()
async def get_messages(limit: Optional[int] = None, offset: int = 0) -> Dict[str, Any]:
    """
    Get messages for the current user with optional pagination.
    
    Args:
        limit: Maximum number of messages to return (e.g., 10 for top 10 messages)
        offset: Number of messages to skip (for pagination)
    
    Returns:
        Messages for the current user, optionally limited by count
    """
    try:
        return await brain_client.get_messages(limit, offset)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_glossary_terms(email: str = "", password: str = "") -> List[Dict[str, str]]:
    """
    Get glossary terms from WorldQuant BRAIN forum.
    
    Note: This uses Playwright and is implemented in forum_functions.py
    
    Args:
        email: Your BRAIN platform email address (optional if in config)
        password: Your BRAIN platform password (optional if in config)
    
    Returns:
        A list of glossary terms with definitions
    """
    try:
        config = load_config()
        credentials = config.get("credentials", {})
        email = email or credentials.get("email")
        password = password or credentials.get("password")
        if not email or not password:
            raise ValueError("Authentication credentials not provided or found in config.")
        
        return await brain_client.get_glossary_terms(email, password)
    except Exception as e:
        logger.error(f"Error in get_glossary_terms tool: {e}")
        return [{"error": str(e)}]

@mcp.tool()
async def search_forum_posts(search_query: str, email: str = "", password: str = "", 
                             max_results: int = 50) -> Dict[str, Any]:
    """
    Search forum posts on WorldQuant BRAIN support site.
    
    Note: This uses Playwright and is implemented in forum_functions.py
    
    Args:
        search_query: Search term or phrase
        email: Your BRAIN platform email address (optional if in config)
        password: Your BRAIN platform password (optional if in config)
        max_results: Maximum number of results to return (default: 50)
    
    Returns:
        Search results with analysis
    """
    try:
        config = load_config()
        credentials = config.get("credentials", {})
        email = email or credentials.get("email")
        password = password or credentials.get("password")
        if not email or not password:
            return {"error": "Authentication credentials not provided or found in config."}
            
        return await brain_client.search_forum_posts(email, password, search_query, max_results)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def read_forum_post(article_id: str, email: str = "", password: str = "", 
                          include_comments: bool = True) -> Dict[str, Any]:
    """
    Get a specific forum post by article ID.
    
    Note: This uses Playwright and is implemented in forum_functions.py
    
    Args:
        article_id: The article ID to retrieve (e.g., "32984819083415-新人求模板")
        email: Your BRAIN platform email address (optional if in config)
        password: Your BRAIN platform password (optional if in config)
    
    Returns:
        Forum post content with comments
    """
    try:
        config = load_config()
        credentials = config.get("credentials", {})
        email = email or credentials.get("email")
        password = password or credentials.get("password")
        if not email or not password:
            return {"error": "Authentication credentials not provided or found in config."}

        return await brain_client.read_forum_post(email, password, article_id, include_comments)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_alpha_yearly_stats(alpha_id: str) -> Dict[str, Any]:
    """Get yearly statistics for an alpha."""
    try:
        return await brain_client.get_alpha_yearly_stats(alpha_id)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def check_correlation(alpha_id: str) -> Dict[str, Any]:
    """Check alpha correlation against production alphas, self alphas, or both."""
    correlation_type = "both"
    threshold = 0.7
    try:
        return await brain_client.check_correlation(alpha_id, correlation_type, threshold)
    except Exception as e:
        return {"error": str(e)}

@mcp.tool()
async def set_alpha_properties(alpha_id: str, name: Optional[str] = None, 
                               color: Optional[str] = None, tags: Optional[List[str]] = None,
                               descriptions: str = "None",
                               selection_description: Optional[str] = None,
                               combo_description: Optional[str] = None) -> Dict[str, Any]:
    """
      Note: Update alpha properties (name, color, tags, descriptions).
      For SUPER alphas, selection_description and combo_description are also required and must
      each be at least 100 English characters.
      Args:
        color: may be one of `RED` `GREEN` `YELLOW` `BLUE` `PURPLE`；
        name: 不能带空格；tags 至少包含 `PowerPoolSelected`；
        descriptions: Write in English, <=100 words. The three sections MUST be separated by
        actual newline characters (i.e. use the JSON escape sequence \\n\\n between sections,
        NOT the literal text "\\n\\n"). Example value:
        "Idea: <your idea here>\\n\\nRationale for data used: <your rationale>\\n\\nRationale for operators used: <your rationale>"
        The three section headers must appear exactly as:
        - Idea:
        - Rationale for data used:
        - Rationale for operators used:
        selection_description: (SUPER alpha only) Description of the selection expression logic.
        Must be at least 100 English characters. Write in English.
        combo_description: (SUPER alpha only) Description of the combo expression logic.
        Must be at least 100 English characters. Write in English.
    """
    try:
        # Normalize literal \n sequences to actual newlines in case the LLM emits
        # backslash-n as two characters rather than a true newline escape.
        if descriptions and descriptions != "None":
            descriptions = descriptions.replace('\\n', '\n')
        return await brain_client.set_alpha_properties(alpha_id, name, color, tags, descriptions,
                                                       selection_description, combo_description)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_record_sets(alpha_id: str) -> Dict[str, Any]:
    """List available record sets for an alpha."""
    try:
        return await brain_client.get_record_sets(alpha_id)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_record_set_data(alpha_id: str, record_set_name: str) -> Dict[str, Any]:
    """Get data from a specific record set."""
    try:
        return await brain_client.get_record_set_data(alpha_id, record_set_name)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_user_activities(user_id: str, grouping: Optional[str] = None) -> Dict[str, Any]:
    """Get user activity diversity data."""
    try:
        return await brain_client.get_user_activities(user_id, grouping)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_pyramid_multipliers() -> Dict[str, Any]:
    """Get current pyramid multipliers showing BRAIN's encouragement levels."""
    try:
        return await brain_client.get_pyramid_multipliers()
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_pyramid_alphas(start_date: Optional[str] = None,
                               end_date: Optional[str] = None) -> Dict[str, Any]:
    """Get user's current alpha distribution across pyramid categories.
    Defaults to the current quarter if no dates are provided."""
    try:
        return await brain_client.get_pyramid_alphas(start_date, end_date)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def recommend_datasets(
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
    top_n: int = 20,
) -> Dict[str, Any]:
    """
    Recommend datasets for alpha construction based on three dimensions:
    
    1. **Pyramid lighting (点塔)**: Prioritizes data categories not yet lit (need 3 alphas per category
       to light up). Encourages uniform coverage across all categories.
    2. **Submission distribution**: Favors datasets with fewer submitted alphas this quarter,
       encouraging diversification across different datasets.
    3. **Dataset quality**: Ranks by OS/IS Sharpe ratio from historical aggregated data.
       Higher Sharpe = better quality dataset.
    
    Each dataset gets a score (0~100):
    - Pyramid lighting: 0~40 pts (unlit categories get highest priority)
    - Submission distribution: 0~30 pts (less-used datasets get priority)
    - Dataset quality: 0~30 pts (higher OS/IS Sharpe = better)
    
    Args:
        region: Market region (e.g., "USA", "CHN", "EUR", "ASI", "GLB")
        delay: Data delay (0 or 1)
        universe: Stock universe (e.g., "TOP3000")
        top_n: Number of top recommendations to return (default 20)
    
    Returns:
        Ranked dataset recommendations with scores and pyramid status summary
    """
    try:
        return await brain_client.recommend_datasets(region, delay, universe, top_n)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}
        
@mcp.tool()
async def get_user_competitions(user_id: Optional[str] = None) -> Dict[str, Any]:
    """Get list of competitions that the user is participating in."""
    try:
        return await brain_client.get_user_competitions(user_id)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_competition_details(competition_id: str) -> Dict[str, Any]:
    """Get detailed information about a specific competition."""
    try:
        return await brain_client.get_competition_details(competition_id)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_competition_agreement(competition_id: str) -> Dict[str, Any]:
    """Get the rules, terms, and agreement for a specific competition."""
    try:
        return await brain_client.get_competition_agreement(competition_id)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def get_platform_setting_options() -> Dict[str, Any]:
    """Discover valid simulation setting options (instrument types, regions, delays, universes, neutralization).

    Use this when a simulation request might contain an invalid/mismatched setting. If an AI or user supplies
    incorrect parameters (e.g., wrong region for an instrument type), call this tool to retrieve the authoritative
    option sets and correct the inputs before proceeding.

    Returns:
        A structured list of valid combinations and choice lists to validate or fix simulation settings.
    """
    try:
        return await brain_client.get_platform_setting_options()
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

@mcp.tool()
async def performance_comparison(alpha_id: str, team_id: Optional[str] = None, 
                                 competition: Optional[str] = None) -> Dict[str, Any]:
    """Get performance comparison data for an alpha."""
    try:
        return await brain_client.performance_comparison(alpha_id, team_id, competition)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}
        
# --- Dataframe Tool ---

@mcp.tool()
async def expand_nested_data(data: List[Dict[str, Any]], preserve_original: bool = True) -> List[Dict[str, Any]]:
    """Flatten complex nested data structures into tabular format."""
    try:
        return await brain_client.expand_nested_data(data, preserve_original)
    except Exception as e:
        return [{"error": f"An unexpected error occurred: {str(e)}"}]
        
# --- Documentation Tool ---

@mcp.tool()
async def get_documentation_page(page_id: str) -> Dict[str, Any]:
    """Retrieve detailed content of a specific documentation page/article."""
    try:
        return await brain_client.get_documentation_page(page_id)
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

# --- Advanced Simulation Tools ---

@mcp.tool()
async def create_multi_simulation(
    alpha_expressions: List[str],
    instrument_type: str = "EQUITY",
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
    decay: int = 4,
    neutralization: str = "INDUSTRY",
    truncation: float = 0.0,
    test_period: str = "P0Y0M",
    unit_handling: str = "VERIFY",
    nan_handling: str = "OFF",
    language: str = "FASTEXPR",
    visualization: bool = False,
    pasteurization: str = "ON",
    max_trade: str = "OFF"
) -> Dict[str, Any]:
    """
    🚀 Create multiple regular alpha simulations on BRAIN platform in a single request.
    
    This tool creates a multisimulation with multiple regular alpha expressions,
    waits for all simulations to complete, and returns detailed results for each alpha.
    
    ⏰ NOTE: Multisimulations can take 8+ minutes to complete. This tool will wait
    for the entire process and return comprehensive results.
    Call get_platform_setting_options to get the valid options for the simulation.
    Args:
        alpha_expressions: List of alpha expressions (2-8 expressions required)
        instrument_type: Type of instruments (default: "EQUITY")
        region: Market region (default: "USA")
        universe: Universe of stocks (default: "TOP3000")
        delay: Data delay (default: 1)
        decay: Decay value (default: 4)
        neutralization: Neutralization method (default: "NONE")
        truncation: Truncation value (default: 0.0)
        test_period: Test period (default: "P0Y0M")
        unit_handling: Unit handling method (default: "VERIFY")
        nan_handling: NaN handling method (default: "OFF")
        language: Expression language (default: "FASTEXPR")
        visualization: Enable visualization (default: False)
        pasteurization: Pasteurization setting (default: "ON")
        max_trade: Max trade setting (default: "OFF")
    
    Returns:
        Dictionary containing multisimulation results and individual alpha details
    """
    try:
        # Validate input
        if len(alpha_expressions) < 2:
            return {"error": "At least 2 alpha expressions are required"}
        if len(alpha_expressions) > 8:
            return {"error": "Maximum 8 alpha expressions allowed per request"}
        
        # Create multisimulation data
        multisimulation_data = []
        for alpha_expr in alpha_expressions:
            simulation_item = {
                'type': 'REGULAR',
                'settings': {
                    'instrumentType': instrument_type,
                    'region': region,
                    'universe': universe,
                    'delay': delay,
                    'decay': decay,
                    'neutralization': neutralization,
                    'truncation': truncation,
                    'pasteurization': pasteurization,
                    'unitHandling': unit_handling,
                    'nanHandling': nan_handling,
                    'language': language,
                    'visualization': visualization,
                    'testPeriod': test_period,
                    'maxTrade': max_trade 
                },
                'regular': alpha_expr
            }
            multisimulation_data.append(simulation_item)
        
        # Send multisimulation request
        response = brain_client.session.post(f"{brain_client.base_url}/simulations", json=multisimulation_data)
        
        if response.status_code != 201:
            return {"error": f"Failed to create multisimulation. Status: {response.status_code}"}
        
        # Get multisimulation location
        location = response.headers.get('Location', '')
        if not location:
            return {"error": "No location header in multisimulation response"}
        
        # Wait for children to appear and get results
        return await _wait_for_multisimulation_completion(location, len(alpha_expressions))
        
    except Exception as e:
        return {"error": f"Error creating multisimulation: {str(e)}"}

async def _wait_for_multisimulation_completion(location: str, expected_children: int) -> Dict[str, Any]:
    """Wait for multisimulation to complete and return results"""
    try:
        # Simple progress indicator for users
        print(f"Waiting for multisimulation to complete... (this may take several minutes)", file=sys.stderr)
        print(f"Expected {expected_children} alpha simulations", file=sys.stderr)
        print("", file=sys.stderr)
        # Wait for children to appear - much more tolerant for 8+ minute multisimulations
        children = []
        max_wait_attempts = 200  # Increased significantly for 8+ minute multisimulations
        wait_attempt = 0
        
        while wait_attempt < max_wait_attempts and len(children) == 0:
            wait_attempt += 1
            
            try:
                multisim_response = await brain_client._request('GET', location)
                if multisim_response.status_code == 200:
                    multisim_data = multisim_response.json()
                    children = multisim_data.get('children', [])
                    
                    if children:
                        break
                    else:
                        # Wait before next attempt - use longer intervals for multisimulations
                        retry_after = multisim_response.headers.get("Retry-After", 5)
                        wait_time = float(retry_after)
                        await asyncio.sleep(wait_time)
            except Exception as e:
                await asyncio.sleep(5)
        
        if not children:
            return {"error": f"Children did not appear within {max_wait_attempts} attempts (multisimulation may still be processing)"}
        
        # Process each child to get alpha results
        alpha_results = []
        for i, child_id in enumerate(children):
            try:
                # The children are full URLs, not just IDs
                child_url = child_id if child_id.startswith('http') else f"{brain_client.base_url}/simulations/{child_id}"
                
                # Wait for this alpha to complete - more tolerant timing
                finished = False
                max_alpha_attempts = 100  # Increased for longer alpha processing
                alpha_attempt = 0
                
                while not finished and alpha_attempt < max_alpha_attempts:
                    alpha_attempt += 1
                    
                    try:
                        alpha_progress = await brain_client._request('GET', child_url)
                        if alpha_progress.status_code == 200:
                            alpha_data = alpha_progress.json()
                            retry_after = alpha_progress.headers.get("Retry-After", 0)
                            
                            if retry_after == 0:
                                finished = True
                                break
                            else:
                                wait_time = float(retry_after)
                                await asyncio.sleep(wait_time)
                        else:
                            await asyncio.sleep(5)
                    except Exception as e:
                        await asyncio.sleep(5)
                
                if finished:
                    # Get alpha details from the completed simulation
                    alpha_id = alpha_data.get("alpha")
                    if alpha_id:
                        # Now get the actual alpha details from the alpha endpoint
                        alpha_details = await brain_client._request('GET', f"{brain_client.base_url}/alphas/{alpha_id}")
                        if alpha_details.status_code == 200:
                            alpha_detail_data = alpha_details.json()
                            alpha_results.append({
                                'alpha_id': alpha_id,
                                'location': child_url,
                                'details': alpha_detail_data
                            })
                        else:
                            alpha_results.append({
                                'alpha_id': alpha_id,
                                'location': child_url,
                                'error': f'Failed to get alpha details: {alpha_details.status_code}'
                            })
                    else:
                        alpha_results.append({
                            'location': child_url,
                            'error': 'No alpha ID found in completed simulation'
                        })
                else:
                    alpha_results.append({
                        'location': f"child_{i+1}",
                        'error': f'Alpha simulation did not complete within {max_alpha_attempts} attempts'
                    })
                    
            except Exception as e:
                alpha_results.append({
                    'location': f"child_{i+1}",
                    'error': str(e)
                })
        
        # Return comprehensive results
        print(f"Multisimulation completed! Retrieved {len(alpha_results)} alpha results", file=sys.stderr)
        return {
            'success': True,
            'message': f'Successfully created {expected_children} regular alpha simulations',
            'total_requested': expected_children,
            'total_created': len(alpha_results),
            'multisimulation_id': location.split('/')[-1],
            'multisimulation_location': location,
            'alpha_results': alpha_results
        }
        
    except Exception as e:
        return {"error": f"Error waiting for multisimulation completion: {str(e)}"}

# --- SUPER Alpha Multi-Simulation Tools ---

@mcp.tool()
async def create_multi_super_simulation(
    combo_expressions: List[str],
    selection: str,
    region: str = "USA",
    universe: str = "TOP3000",
    delay: int = 1,
    decay: int = 5,
    neutralization: str = "INDUSTRY",
    neutralizations: Optional[List[str]] = None,
    truncation: float = 0.08,
    test_period: str = "P0Y",
    nan_handling: str = "OFF",
    pasteurization: str = "ON",
    max_trade: str = "OFF",
    selection_handling: str = "POSITIVE",
    selection_limit: int = 100,
    max_simulations: int = 50,
) -> Dict[str, Any]:
    """
    Create multiple SUPER alpha simulations on BRAIN platform in parallel.

    SUPER alphas combine multiple submitted REGULAR alphas using combo expressions
    and selection criteria. This tool creates multiple SUPER alpha simulations with
    different combo expressions (and optionally different neutralizations).

    If 'neutralizations' list is provided, creates all combinations of
    (combo_expression x neutralization). Otherwise uses the single 'neutralization'
    for all combos.

    The total number of simulations is capped by 'max_simulations'.

    Args:
        combo_expressions: List of SUPER alpha combo expressions (e.g., ["1", "combo_a(alpha)"])
        selection: Selection expression to pick component alphas (e.g., "(own)&&(name == 'group_1')")
        region: Market region (e.g., "USA", "EUR", "GLB", "ASI", "IND", "CHN")
        universe: Universe of stocks (e.g., "TOP3000")
        delay: Data delay (0 or 1)
        decay: Decay value (default: 5)
        neutralization: Single neutralization method (used when 'neutralizations' is not provided)
        neutralizations: Optional list of neutralization methods to create combinations
            (e.g., ["INDUSTRY", "SUBINDUSTRY", "MARKET", "SECTOR"])
        truncation: Truncation value (default: 0.08)
        test_period: Test period (default: "P0Y")
        nan_handling: NaN handling (default: "OFF")
        pasteurization: Pasteurization setting (default: "ON")
        max_trade: Max trade setting (default: "OFF")
        selection_handling: Selection handling (default: "POSITIVE")
        selection_limit: Number of component alphas to select (default: 100)
        max_simulations: Maximum number of simulations to create (default: 50)

    Returns:
        Dictionary containing results for each SUPER alpha simulation
    """
    try:
        if not combo_expressions:
            return {"error": "At least 1 combo expression is required"}
        if not selection:
            return {"error": "Selection expression is required for SUPER alpha"}

        # Build neutralization list
        neut_list = neutralizations if neutralizations else [neutralization]

        # Generate all simulation configs (combo x neutralization)
        sim_configs = []
        for combo_expr in combo_expressions:
            for neut in neut_list:
                sim_configs.append({
                    "combo": combo_expr,
                    "neutralization": neut,
                })

        # Cap the number of simulations
        if len(sim_configs) > max_simulations:
            import random as _rand
            sim_configs = _rand.sample(sim_configs, max_simulations)

        brain_client.log(
            f"Creating {len(sim_configs)} SUPER alpha simulations "
            f"(region={region}, selection_limit={selection_limit})...",
            "INFO",
        )

        # Create simulation tasks
        async def _run_one(cfg: dict, index: int) -> dict:
            try:
                settings = SimulationSettings(
                    instrumentType="EQUITY",
                    region=region,
                    universe=universe,
                    delay=delay,
                    decay=decay,
                    neutralization=cfg["neutralization"],
                    truncation=truncation,
                    testPeriod=test_period,
                    unitHandling="VERIFY",
                    nanHandling=nan_handling,
                    language="FASTEXPR",
                    visualization=False,
                    pasteurization=pasteurization,
                    maxTrade=max_trade,
                    selectionHandling=selection_handling,
                    selectionLimit=selection_limit,
                )
                sim_data = SimulationData(
                    type="SUPER",
                    settings=settings,
                    combo=cfg["combo"],
                    selection=selection,
                )
                result = await brain_client.create_simulation(sim_data)
                alpha_id = result.get("id", "unknown")
                return {
                    "index": index,
                    "alpha_id": alpha_id,
                    "combo": cfg["combo"],
                    "neutralization": cfg["neutralization"],
                    "details": result,
                }
            except Exception as exc:
                return {
                    "index": index,
                    "combo": cfg["combo"],
                    "neutralization": cfg["neutralization"],
                    "error": str(exc),
                }

        # Run all simulations concurrently
        tasks = [_run_one(cfg, i) for i, cfg in enumerate(sim_configs)]
        results = await asyncio.gather(*tasks, return_exceptions=False)

        succeeded = [r for r in results if "error" not in r]
        failed = [r for r in results if "error" in r]

        return {
            "success": True,
            "message": f"Created {len(succeeded)} SUPER alpha simulations ({len(failed)} failed)",
            "total_requested": len(sim_configs),
            "total_succeeded": len(succeeded),
            "total_failed": len(failed),
            "region": region,
            "selection": selection,
            "selection_limit": selection_limit,
            "alpha_results": succeeded,
            "errors": failed if failed else None,
        }

    except Exception as e:
        return {"error": f"Error creating multi SUPER simulation: {str(e)}"}

# --- Payment and Financial Tools ---

@mcp.tool()
async def get_daily_and_quarterly_payment(email: str = "", password: str = "") -> Dict[str, Any]:
    """
    Get daily and quarterly payment information from WorldQuant BRAIN platform.
    
    This function retrieves both base payments (daily alpha performance payments) and 
    other payments (competition rewards, quarterly payments, referrals, etc.).
    
    Args:
        email: Your BRAIN platform email address (optional if in config)
        password: Your BRAIN platform password (optional if in config)
    
    Returns:
        Dictionary containing base payment and other payment data with summaries and detailed records
    """
    try:
        config = load_config()
        credentials = config.get("credentials", {})
        email = email or credentials.get("email")
        password = password or credentials.get("password")
        if not email or not password:
            return {"error": "Authentication credentials not provided or found in config."}
            
        await brain_client.authenticate(email, password)
        
        # Get base payments
        try:
            base_response = await brain_client._request('GET', f"{brain_client.base_url}/users/self/activities/base-payment")
            base_response.raise_for_status()
            base_payments = base_response.json()
        except:
            base_payments = "no data"
            
        try:
            # Get other payments
            other_response = await brain_client._request('GET', f"{brain_client.base_url}/users/self/activities/other-payment")
            other_response.raise_for_status()
            other_payments = other_response.json()
        except:
            other_payments = "no data"    
        return {
            "base_payments": base_payments,
            "other_payments": other_payments
        }
        
    except Exception as e:
        return {"error": f"An unexpected error occurred: {str(e)}"}

from typing import Sequence
@mcp.tool()
async def lookINTO_SimError_message(locations: Sequence[str]) -> dict:
    """
    Fetch and parse error/status from multiple simulation locations (URLs).
    Args:
        locations: List of simulation result URLs (e.g., /simulations/{id})
    Returns:
        List of dicts with location, error message, and raw response
    """
    results = []
    for loc in locations:
        try:
            resp = await brain_client._request('GET', loc)
            if resp.status_code != 200:
                results.append({
                    "location": loc,
                    "error": f"HTTP {resp.status_code}",
                    "raw": resp.text
                })
                continue
            data = resp.json() if resp.text else {}
            # Try to extract error message or status
            error_msg = data.get("error") or data.get("message")
            # If alpha ID is missing, include that info
            if not data.get("alpha"):
                error_msg = error_msg or "Simulation did not get through, if you are running a multisimulation, check the other children location in your request"
            extra_info = ""
            if error_msg and "does not support event inputs" in error_msg:
                extra_info = "Operator xxx does not support event inputs : If fields is vector type  should use vec_* operator with event input"
            results.append({
                "location": loc,
                "error": error_msg,
                "raw": data,
                "extra_info": extra_info
            })
        except Exception as e:
            results.append({
                "location": loc,
                "error": str(e),
                "raw": None
            })
    return {"results": results}

# --- Main entry point ---
if __name__ == "__main__":
    print("running the server", file=sys.stderr)
    
    # Validate critical environment setup
    config = load_config()
    creds = config.get("credentials", {})
    if not creds.get("email") or not creds.get("password"):
        print("[WARNING] No BRAIN credentials found in config. Authentication will fail until credentials are provided.", file=sys.stderr)
    
    # Verify Redis connectivity
    if brain_client.redis_client:
        print("[INFO] Redis connection established successfully", file=sys.stderr)
    else:
        print("[WARNING] Redis connection failed - caching disabled", file=sys.stderr)
    
    # Run using Streamable HTTP transport in container environment so the server remains
    # running and accessible over HTTP (not stdio which exits in non-interactive containers).
    try:
        mcp.run(transport='streamable-http')
    except TypeError:
        # Fallback if signature differs
        mcp.run('streamable-http')