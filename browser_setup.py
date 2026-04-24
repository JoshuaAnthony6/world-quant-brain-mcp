#!/usr/bin/env python3
"""
浏览器设置和下载模块
优先使用环境变量或系统已安装的 Chromium/Chrome，仅在确实不可用时才回退到
Playwright 内置浏览器或本地下载的 Chrome。
"""

import os
import sys
import subprocess
import platform
import requests
from pathlib import Path
import shutil


def log(message: str, level: str = "INFO"):
    print(f"[{level}] {message}", file=sys.stderr)


def get_system_info():
    system = platform.system()
    machine = platform.machine().lower()
    return system, machine


def _is_valid_browser(path: str | None) -> bool:
    return bool(path and os.path.exists(path) and os.access(path, os.X_OK))


def find_system_browser():
    system, _ = get_system_info()

    for env_name in ("BROWSER_PATH", "CHROME_PATH", "CHROMIUM_PATH"):
        candidate = os.environ.get(env_name)
        if _is_valid_browser(candidate):
            log(f"从环境变量 {env_name} 找到浏览器: {candidate}", "SUCCESS")
            return candidate

    if system == "Linux":
        for binary in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
            candidate = shutil.which(binary)
            if _is_valid_browser(candidate):
                log(f"找到系统浏览器: {candidate}", "SUCCESS")
                return candidate

        for candidate in (
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/google-chrome",
            "/opt/google/chrome/chrome",
        ):
            if _is_valid_browser(candidate):
                log(f"找到系统浏览器: {candidate}", "SUCCESS")
                return candidate

    if system == "Windows":
        for candidate in (
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expanduser(r"~\AppData\Local\Google\Chrome\Application\chrome.exe"),
        ):
            if _is_valid_browser(candidate):
                log(f"找到系统浏览器: {candidate}", "SUCCESS")
                return candidate

    return None


def download_chrome_package():
    system, machine = get_system_info()

    if system == "Linux" and machine in ["x86_64", "amd64"]:
        log("检测到 Linux x86_64 系统，下载 Chrome .deb 包...", "INFO")

        download_dir = Path("./downloads")
        download_dir.mkdir(exist_ok=True)

        deb_file = download_dir / "google-chrome-stable_current_amd64.deb"

        if not deb_file.exists():
            log("下载 Chrome .deb 包...", "INFO")
            try:
                url = "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb"
                response = requests.get(url, stream=True, timeout=60)
                response.raise_for_status()

                with open(deb_file, "wb") as f:
                    shutil.copyfileobj(response.raw, f)
                log("Chrome .deb 包下载完成", "SUCCESS")
            except Exception as e:
                log(f"下载失败: {e}", "ERROR")
                return None
        else:
            log("Chrome .deb 包已存在", "INFO")

        log("提取 Chrome .deb 包...", "INFO")
        try:
            subprocess.run(["ar", "x", str(deb_file)], cwd=download_dir, check=True)

            data_file = download_dir / "data.tar.xz"
            if data_file.exists():
                subprocess.run(["tar", "-xf", str(data_file)], cwd=download_dir, check=True)
                log("Chrome .deb 包提取完成", "SUCCESS")

            chrome_path = download_dir / "opt" / "google" / "chrome" / "chrome"
            if _is_valid_browser(str(chrome_path)):
                return str(chrome_path.absolute())

            log("未找到 Chrome 可执行文件", "ERROR")
            return None
        except Exception as e:
            log(f"提取失败: {e}", "ERROR")
            return None

    if system == "Windows":
        log("检测到 Windows 系统，检查系统 Chrome 浏览器...", "INFO")
        candidate = find_system_browser()
        if candidate:
            return candidate

        log("未找到 Chrome 浏览器，请手动安装或从以下地址下载:", "WARNING")
        log("https://www.google.com/chrome/", "INFO")
        return None

    log(f"不支持的系统: {system} {machine}", "ERROR")
    return None


def setup_chrome_for_playwright():
    log("开始设置 Chrome 浏览器用于 Playwright...", "INFO")
    chrome_path = download_chrome_package()
    if chrome_path:
        log(f"Chrome 浏览器路径: {chrome_path}", "SUCCESS")
        return chrome_path

    log("无法设置 Chrome 浏览器，将使用默认 Playwright 浏览器", "WARNING")
    return None


def get_playwright_chrome_path():
    try:
        import asyncio
        from playwright.async_api import async_playwright

        async def get_path():
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                executable_path = browser.executable_path
                await browser.close()
                return executable_path

        return asyncio.run(get_path())
    except Exception as e:
        log(f"获取 Playwright Chrome 路径失败: {e}", "WARNING")
        return None


def ensure_browser_available():
    log("检查浏览器可用性...", "INFO")

    system_browser = find_system_browser()
    if system_browser:
        return system_browser

    playwright_path = get_playwright_chrome_path()
    if _is_valid_browser(playwright_path):
        log(f"Playwright Chromium 可用: {playwright_path}", "SUCCESS")
        return playwright_path

    log("默认 Playwright 浏览器不可用，尝试设置本地 Chrome...", "WARNING")
    local_chrome_path = setup_chrome_for_playwright()
    if _is_valid_browser(local_chrome_path):
        log(f"本地 Chrome 可用: {local_chrome_path}", "SUCCESS")
        return local_chrome_path

    log("无可用浏览器，将使用默认 Playwright 设置", "WARNING")
    return None


if __name__ == "__main__":
    browser_path = ensure_browser_available()
    if browser_path:
        print(f"可用浏览器路径: {browser_path}")
    else:
        print("无法确定浏览器路径")
