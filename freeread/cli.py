#!/usr/bin/env python3
"""freeread CLI.

Purpose: pull readable article text for a URL by trying multiple public cache / reader methods.

Notes:
- Some methods may fail depending on site, region, and IP reputation.
- Optional Scrapling methods (http/stealth/dynamic) require installing extras.
"""

import argparse
import http.cookiejar
import os
import re
import sys
from typing import Dict, List, Optional

import html2text
import requests
from readability import Document
from rich import print as rprint
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

console = Console()

HEADERS_NORMAL = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

HEADERS_GOOGLE_REFERER = {
    **HEADERS_NORMAL,
    "Referer": "https://www.google.com/",
}

HEADERS_GOOGLEBOT = {
    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
}

TIMEOUT = 20
MIN_CONTENT_LENGTH = 300

RUNTIME = {
    "proxy": None,
    "cookies": None,  # Dict of cookie names/values
    "cookies_raw": None,  # List of dicts for Scrapling
}


def parse_cookies(cookie_file: str):
    """Parse Netscape cookie file or text cookies."""
    cj = http.cookiejar.MozillaCookieJar(cookie_file)
    try:
        cj.load(ignore_discard=True, ignore_expires=True)
    except Exception as e:
        # Try parsing as simple semicolon-separated string if file load fails
        if os.path.exists(cookie_file):
            with open(cookie_file, "r") as f:
                content = f.read()
        else:
            content = cookie_file

        cookies = {}
        for item in content.split(";"):
            if "=" in item:
                name, value = item.strip().split("=", 1)
                cookies[name] = value
        return cookies

    cookies = {}
    cookies_raw = []
    for cookie in cj:
        cookies[cookie.name] = cookie.value
        cookies_raw.append({
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
        })
    return cookies, cookies_raw


def clean_html_to_text(html: str) -> tuple[str, str]:
    """Extract title and clean markdown-ish text from HTML."""
    doc = Document(html)
    title = doc.title()
    content_html = doc.summary()

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0

    text = converter.handle(content_html)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return title, text


def is_sufficient(text: str) -> bool:
    return len(text) >= MIN_CONTENT_LENGTH


def _requests_proxies():
    if not RUNTIME.get("proxy"):
        return None
    return {"http": RUNTIME["proxy"], "https": RUNTIME["proxy"]}


def _get_kwargs(headers=None):
    kwargs = {
        "timeout": TIMEOUT,
        "headers": headers or HEADERS_NORMAL,
        "proxies": _requests_proxies(),
    }
    if RUNTIME.get("cookies"):
        kwargs["cookies"] = RUNTIME["cookies"]
    return kwargs


def try_archive_ph(url: str):
    kwargs = _get_kwargs(HEADERS_NORMAL)
    for archive_url in [f"https://archive.ph/newest/{url}", f"https://archive.ph/{url}"]:
        try:
            r = requests.get(archive_url, allow_redirects=True, **kwargs)
            if r.status_code == 200:
                title, text = clean_html_to_text(r.text)
                if is_sufficient(text):
                    return title, text, "archive.ph"
        except Exception:
            continue
    return None


def try_12ft(url: str):
    kwargs = _get_kwargs(HEADERS_NORMAL)
    try:
        r = requests.get(f"https://12ft.io/proxy?q={url}", **kwargs)
        if r.status_code == 200:
            title, text = clean_html_to_text(r.text)
            if is_sufficient(text):
                return title, text, "12ft.io"
    except Exception:
        pass
    return None


def try_google_referer(url: str):
    kwargs = _get_kwargs(HEADERS_GOOGLE_REFERER)
    try:
        r = requests.get(url, **kwargs)
        if r.status_code == 200:
            title, text = clean_html_to_text(r.text)
            if is_sufficient(text):
                return title, text, "Google referer"
    except Exception:
        pass
    return None


def try_cookie_clear(url: str):
    """
    Cookie-clearing bypass. If cookies are provided via CLI, this is skipped
    as it would clear the user's provided session.
    """
    if RUNTIME.get("cookies"):
        return None

    proxies = _requests_proxies()
    try:
        session = requests.Session()
        session.cookies.clear()
        r = session.get(url, timeout=TIMEOUT, headers=HEADERS_GOOGLE_REFERER, proxies=proxies)
        if r.status_code == 200:
            title, text = clean_html_to_text(r.text)
            if is_sufficient(text):
                return title, text, "cookie-clear"
    except Exception:
        pass
    return None


def try_googlebot(url: str):
    kwargs = _get_kwargs(HEADERS_GOOGLEBOT)
    try:
        r = requests.get(url, **kwargs)
        if r.status_code == 200:
            title, text = clean_html_to_text(r.text)
            if is_sufficient(text):
                return title, text, "Googlebot UA"
    except Exception:
        pass
    return None


def try_wayback(url: str):
    kwargs = _get_kwargs(HEADERS_NORMAL)
    try:
        r = requests.get(f"https://archive.org/wayback/available?url={url}", **kwargs)
        data = r.json()
        snapshot = data.get("archived_snapshots", {}).get("closest", {})
        if not snapshot or snapshot.get("status") != "200":
            return None
        r2 = requests.get(snapshot["url"], **kwargs)
        r2.raise_for_status()
        title, text = clean_html_to_text(r2.text)
        if is_sufficient(text):
            return title, text, "Wayback Machine"
    except Exception:
        pass
    return None


def try_scrapling_http(url: str):
    """Scrapling Fetcher (curl_cffi). Requires: pip install freeread[scrapling]"""
    try:
        from scrapling.fetchers import Fetcher

        f = Fetcher()
        kwargs = dict(
            headers=HEADERS_GOOGLE_REFERER,
            timeout=TIMEOUT,
            retries=2,
            retry_delay=1,
            follow_redirects=True,
            stealthy_headers=True,
            impersonate="chrome",
        )
        if RUNTIME.get("proxy"):
            kwargs["proxy"] = RUNTIME["proxy"]
        if RUNTIME.get("cookies"):
            kwargs["cookies"] = RUNTIME["cookies"]

        r = f.get(url, **kwargs)
        if getattr(r, "status", None) == 200 and getattr(r, "html_content", None):
            title, text = clean_html_to_text(r.html_content)
            if is_sufficient(text):
                return title, text, "Scrapling http"
    except Exception:
        pass
    return None


def try_scrapling_stealth(url: str):
    """Scrapling StealthyFetcher. Requires: pip install freeread[scrapling]"""
    try:
        from scrapling.fetchers import StealthyFetcher

        fetcher = StealthyFetcher()
        kwargs = dict(
            headless=True,
            disable_resources=True,
            network_idle=True,
            extra_headers=HEADERS_GOOGLE_REFERER,
            solve_cloudflare=True,
            real_chrome=False,
            wait=1,
        )
        if RUNTIME.get("proxy"):
            kwargs["proxy"] = RUNTIME["proxy"]
        if RUNTIME.get("cookies_raw"):
            kwargs["cookies"] = RUNTIME["cookies_raw"]

        page = fetcher.fetch(url, **kwargs)
        if page and page.html_content:
            title, text = clean_html_to_text(page.html_content)
            if is_sufficient(text):
                return title, text, "Scrapling stealth"
    except Exception:
        pass
    return None


def try_scrapling_dynamic(url: str):
    """Scrapling DynamicFetcher (Playwright). Requires: pip install freeread[scrapling]"""
    try:
        from scrapling.fetchers import DynamicFetcher

        fetcher = DynamicFetcher()
        kwargs = dict(
            headless=True,
            disable_resources=True,
            network_idle=True,
            extra_headers=HEADERS_GOOGLE_REFERER,
            wait=2,
        )
        if RUNTIME.get("proxy"):
            kwargs["proxy"] = RUNTIME["proxy"]
        if RUNTIME.get("cookies_raw"):
            kwargs["cookies"] = RUNTIME["cookies_raw"]

        page = fetcher.fetch(url, **kwargs)
        if page and page.html_content:
            title, text = clean_html_to_text(page.html_content)
            if is_sufficient(text):
                return title, text, "Scrapling dynamic"
    except Exception:
        pass
    return None


METHODS = {
    "ph": ("archive.ph", try_archive_ph),
    "12ft": ("12ft.io", try_12ft),
    "ref": ("Google Referer", try_google_referer),
    "cookie": ("Cookie-clear", try_cookie_clear),
    "http": ("Scrapling http", try_scrapling_http),
    "bot": ("Googlebot UA", try_googlebot),
    "stealth": ("Scrapling stealth", try_scrapling_stealth),
    "dynamic": ("Scrapling dynamic", try_scrapling_dynamic),
    "wb": ("Wayback Machine", try_wayback),
}

METHOD_ORDER = ["ph", "12ft", "ref", "cookie", "http", "bot", "stealth", "dynamic", "wb"]


def fetch_article(url: str, method: str | None = None):
    methods = [method] if method else METHOD_ORDER
    for key in methods:
        name, fn = METHODS[key]
        console.print(f"  [dim]→ {name}...[/dim]", end="\r")
        result = fn(url)
        if result:
            console.print(" " * 60, end="\r")
            return result
    return None


def render_article(title: str, text: str, source: str, mode: str):
    if mode == "raw":
        print(f"# {title}\n\n{text}")
        return

    if mode == "md":
        print(f"# {title}\n\n> *via {source}*\n\n{text}")
        return

    console.print()
    console.print(
        Panel(
            f"[bold]{title}[/bold]\n[dim]via {source}[/dim]",
            expand=False,
            border_style="green",
        )
    )
    console.print()
    console.print(Markdown(text))


def main():
    parser = argparse.ArgumentParser(
        description="freeread — read paywalled articles in your terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", nargs="?", help="Article URL")
    parser.add_argument("--method", "-m", choices=list(METHODS.keys()), help="Force a bypass method")
    parser.add_argument("--md", action="store_true", help="Output clean markdown")
    parser.add_argument("--raw", action="store_true", help="Output plain text")
    parser.add_argument("--list-methods", action="store_true", help="List bypass methods")
    parser.add_argument(
        "--proxy",
        default=None,
        help="Proxy URL. Also reads FREEREAD_PROXY or DECODO_MOBILE_PROXY env vars",
    )
    parser.add_argument(
        "--cookies",
        "-c",
        default=None,
        help="Path to a Netscape cookie file or a cookie string",
    )

    args = parser.parse_args()

    if args.list_methods:
        rprint("\n[bold]Bypass methods:[/bold]")
        for key, (name, _) in METHODS.items():
            rprint(f"  [cyan]{key:8}[/cyan] {name}")
        rprint()
        return

    if not args.url:
        parser.print_help()
        sys.exit(1)

    proxy = args.proxy or os.environ.get("FREEREAD_PROXY") or os.environ.get("DECODO_MOBILE_PROXY")
    if proxy:
        RUNTIME["proxy"] = proxy

    if args.cookies:
        cookies, cookies_raw = parse_cookies(args.cookies)
        RUNTIME["cookies"] = cookies
        RUNTIME["cookies_raw"] = cookies_raw

    output_mode = "md" if args.md else ("raw" if args.raw else "rich")
    if output_mode in ("md", "raw"):
        console.quiet = True

    result = fetch_article(args.url, method=args.method)
    if not result:
        if output_mode in ("md", "raw"):
            print("ERROR: Could not retrieve article.", file=sys.stderr)
        else:
            console.print("[bold red]✗ Failed[/bold red] — no content found via any method.")
            console.print("[dim]Try --method stealth or --method dynamic for JS-heavy sites.[/dim]")
        sys.exit(1)

    title, text, source = result
    if output_mode == "rich":
        console.print(f"[bold green]✓[/bold green] Retrieved via [cyan]{source}[/cyan]")

    render_article(title, text, source, mode=output_mode)


if __name__ == "__main__":
    main()
