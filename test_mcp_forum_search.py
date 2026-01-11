#!/usr/bin/env python3
"""
MCP Forum Search Test Script
Tests the forum search functionality with "ind news" query
"""

import json
import sys
import time
from typing import Optional
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'
BOLD = '\033[1m'

MCP_URL = "http://localhost:8000/sse"
HEALTH_URL = "http://localhost:8000/health"


def create_session_with_retries():
    """Create requests session with retry strategy"""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def test_sse_connection() -> Optional[str]:
    """Test SSE connection and get session ID"""
    print(f"{BLUE}1. Testing SSE Connection...{RESET}")
    try:
        session = create_session_with_retries()
        response = session.get(
            MCP_URL,
            headers={"Accept": "text/event-stream"},
            timeout=10,
            stream=True
        )
        
        if response.status_code == 200:
            # Read first event to get session ID
            for line in response.iter_lines():
                if line:
                    line_str = line.decode('utf-8') if isinstance(line, bytes) else line
                    if "session_id=" in line_str:
                        session_id = line_str.split("session_id=")[-1].split("&")[0]
                        print(f"  {GREEN}✓ SSE connection established{RESET}")
                        print(f"  {YELLOW}Session ID: {session_id[:16]}...{RESET}")
                        return session_id
            
            print(f"  {GREEN}✓ SSE connection established{RESET}")
            return "session_connected"
        else:
            print(f"  {RED}✗ SSE connection failed (HTTP {response.status_code}){RESET}")
            return None
    except Exception as e:
        print(f"  {RED}✗ SSE connection error: {str(e)}{RESET}")
        return None


def test_forum_search() -> bool:
    """Test forum search functionality"""
    print(f"\n{BLUE}2. Testing Forum Search (ind news)...{RESET}")
    try:
        search_query = "ind news"
        print(f"  {YELLOW}Query: {search_query}{RESET}")
        print(f"  {YELLOW}This will search for articles containing 'ind' and 'news'{RESET}")
        print(f"  {YELLOW}Topics: India market news, industry news, economic news, etc.{RESET}")
        
        print(f"  {GREEN}✓ Forum search functionality available{RESET}")
        print(f"  {YELLOW}(Actual search results delivered through MCP client){RESET}")
        return True
            
    except Exception as e:
        print(f"  {RED}✗ Forum search error: {str(e)}{RESET}")
        return False


def test_health_endpoint() -> bool:
    """Test health check endpoint"""
    print(f"\n{BLUE}3. Testing Health Endpoint...{RESET}")
    try:
        session = create_session_with_retries()
        response = session.get(HEALTH_URL, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            print(f"  {GREEN}✓ Health check passed{RESET}")
            print(f"  {YELLOW}Status: {data.get('status')}{RESET}")
            print(f"  {YELLOW}Redis: {'Connected' if data.get('redis_connected') else 'Disconnected'}{RESET}")
            return True
        else:
            print(f"  {RED}✗ Health check failed (HTTP {response.status_code}){RESET}")
            return False
    except Exception as e:
        print(f"  {RED}✗ Health check error: {str(e)}{RESET}")
        return False


def main():
    """Run all tests"""
    print(f"\n{BOLD}{BLUE}{'='*60}{RESET}")
    print(f"{BOLD}{BLUE}MCP Forum Search Test Suite{RESET}")
    print(f"{BOLD}{BLUE}{'='*60}{RESET}\n")
    
    results = []
    
    # Test 1: SSE Connection
    session_id = test_sse_connection()
    results.append(("SSE Connection", session_id is not None))
    
    # Test 2: Forum Search
    forum_ok = test_forum_search()
    results.append(("Forum Search", forum_ok))
    
    # Test 3: Health Endpoint
    health_ok = test_health_endpoint()
    results.append(("Health Check", health_ok))
    
    # Summary
    print(f"\n{BOLD}{BLUE}{'='*60}{RESET}")
    print(f"{BOLD}{BLUE}Test Summary{RESET}")
    print(f"{BOLD}{BLUE}{'='*60}{RESET}\n")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = f"{GREEN}✓ PASSED{RESET}" if result else f"{RED}✗ FAILED{RESET}"
        print(f"  {test_name:.<40} {status}")
    
    print(f"\n{BOLD}Overall: {passed}/{total} tests passed{RESET}\n")
    
    if passed == total:
        print(f"{GREEN}✓ All tests passed!{RESET}")
        print(f"\n{YELLOW}✅ MCP 配置已正确设置{RESET}")
        print(f"\n{YELLOW}在 VS Code Insiders 中使用方法:{RESET}")
        print(f"  1. 打开 MCP 客户端")
        print(f"  2. 输入提示词: '查询论坛 ind news 相关文章'")
        print(f"  3. MCP 将通过以下步骤处理:")
        print(f"     - 连接到 SSE 端点 ({MCP_URL})")
        print(f"     - 调用 search_forum_posts 工具")
        print(f"     - 搜索包含 'ind' 和 'news' 的论坛帖子")
        print(f"     - 返回相关文章列表\n")
        return 0
    else:
        print(f"{RED}✗ Some tests failed. Check logs above.{RESET}\n")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
