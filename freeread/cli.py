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
import threading
import warnings
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional
from urllib.parse import urlparse

# Suppress harmless requests/urllib3 version mismatch warnings
warnings.filterwarnings("ignore", message="urllib3.*doesn't match a supported version")

import html2text
import requests
from readability import Document
from rich import print as rprint
from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape as rich_escape
from rich.panel import Panel
from rich.table import Table

from . import __version__

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


def check_for_updates():
    """Check PyPI for the latest version of freeread."""
    try:
        from packaging.version import Version
    except ImportError:
        # packaging is usually available, but fall back to tuple comparison
        Version = None

    try:
        response = requests.get("https://pypi.org/pypi/freeread/json", timeout=2)
        if response.status_code == 200:
            latest_version = response.json()["info"]["version"]
            if latest_version == __version__:
                return
            # Only notify if PyPI version is actually newer
            if Version is not None:
                if Version(latest_version) <= Version(__version__):
                    return
            else:
                if tuple(int(x) for x in latest_version.split(".")) <= tuple(
                    int(x) for x in __version__.split(".")
                ):
                    return
            console.print(
                f"\n[bold yellow]New version available: {latest_version}[/bold yellow] (current: {__version__})"
            )
            console.print(f"[dim]Run 'pipx upgrade freeread' to update[/dim]\n")
    except Exception:
        pass


def parse_cookies(cookie_file: str):
    """Parse Netscape cookie file or text cookies."""
    cj = http.cookiejar.MozillaCookieJar(cookie_file)
    try:
        cj.load(ignore_discard=True, ignore_expires=True)
    except Exception:
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
        return cookies, []

    cookies = {}
    cookies_raw = []
    for cookie in cj:
        cookies[cookie.name] = cookie.value
        cookies_raw.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path,
            }
        )
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


NEWS_SOURCES = {
    "mix": {
        "name": "Top Headlines",
        "type": "mix",
    },
    "google": {
        "name": "Google News",
        "url": "https://news.google.com/rss?hl=en&gl=US&ceid=US:en",
        "type": "rss",
    },
    "bbc": {
        "name": "BBC World",
        "url": "https://feeds.bbci.co.uk/news/world/rss.xml",
        "type": "rss",
    },
    "aljazeera": {
        "name": "Al Jazeera",
        "url": "https://www.aljazeera.com/xml/rss/all.xml",
        "type": "rss",
    },
    "npr": {
        "name": "NPR News",
        "url": "https://feeds.npr.org/1001/rss.xml",
        "type": "rss",
    },
    "hn": {
        "name": "Hacker News",
        "url": "https://hacker-news.firebaseio.com/v0/topstories.json",
        "type": "hn_api",
    },
    "reddit": {
        "name": "Reddit World News",
        "url": "https://www.reddit.com/r/worldnews/.rss?limit=25",
        "type": "reddit_rss",
    },
}

DEFAULT_NEWS_SOURCE = "mix"


def _parse_rss_items(content: bytes, count: int) -> list[dict]:
    """Parse RSS XML and return headline dicts."""
    root = ET.fromstring(content)
    items = root.findall(".//item")

    headlines = []
    for item in items[:count]:
        title = item.findtext("title", "").strip()
        link = item.findtext("link", "").strip()
        source = item.findtext("source", "").strip()
        pub_date = item.findtext("pubDate", "").strip()
        # Shorten: "Sat, 14 Mar 2026 10:00:00 GMT" → "Sat, 14 Mar 2026"
        short_date = pub_date.rsplit(" ", 2)[0] if pub_date else ""
        headlines.append(
            {"title": title, "link": link, "source": source, "date": short_date}
        )
    return headlines


def _fetch_hn_items(count: int) -> list[dict]:
    """Fetch top stories from Hacker News Firebase API."""
    r = requests.get(
        "https://hacker-news.firebaseio.com/v0/topstories.json",
        headers=HEADERS_NORMAL,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    story_ids = r.json()[:count]

    headlines = []
    for sid in story_ids:
        try:
            sr = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{sid}.json",
                headers=HEADERS_NORMAL,
                timeout=10,
            )
            sr.raise_for_status()
            story = sr.json()
            score = story.get("score", 0)
            headlines.append(
                {
                    "title": story.get("title", ""),
                    "link": f"https://news.ycombinator.com/item?id={sid}",
                    "source": f"↑{score}",
                    "date": "",
                }
            )
        except Exception:
            continue
    return headlines


def _parse_reddit_rss(content: bytes, count: int) -> list[dict]:
    """Parse Reddit Atom RSS feed."""
    ATOM = "{http://www.w3.org/2005/Atom}"
    root = ET.fromstring(content)
    entries = root.findall(f".//{ATOM}entry")

    headlines = []
    for entry in entries:
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        # Skip sticky/megathread posts
        if title.startswith("/r/") and ("Live Thread" in title or "Discussion Thread" in title):
            continue
        link_el = entry.find(f"{ATOM}link")
        link = link_el.get("href", "") if link_el is not None else ""
        updated = (entry.findtext(f"{ATOM}updated") or "").strip()
        short_date = updated[:10] if updated else ""
        headlines.append(
            {
                "title": title,
                "link": link,
                "source": "r/worldnews",
                "date": short_date,
            }
        )
        if len(headlines) >= count:
            break
    return headlines


def _fetch_single_source(source_key: str, count: int) -> list[dict]:
    """Fetch headlines from a single source. Returns [] on failure."""
    src = NEWS_SOURCES[source_key]
    try:
        if src["type"] == "hn_api":
            return _fetch_hn_items(count)
        else:
            if src["type"] == "reddit_rss":
                headers = {"User-Agent": "freeread/1.0", "Accept": "application/rss+xml"}
            else:
                headers = HEADERS_NORMAL
            r = requests.get(src["url"], headers=headers, timeout=TIMEOUT)
            r.raise_for_status()
            if src["type"] == "reddit_rss":
                return _parse_reddit_rss(r.content, count)
            return _parse_rss_items(r.content, count)
    except Exception:
        return []


def _fetch_mix() -> list[dict]:
    """Fetch a mixed feed from BBC, NPR, and Al Jazeera."""
    headlines = []

    for key in ("bbc", "npr", "aljazeera"):
        items = _fetch_single_source(key, 4)
        for item in items:
            if not item["source"]:
                item["source"] = NEWS_SOURCES[key]["name"]
        headlines.extend(items)

    return headlines


def fetch_news(source_key: str = DEFAULT_NEWS_SOURCE, count: int = 10) -> list[dict]:
    """Fetch top headlines from the specified source."""
    src = NEWS_SOURCES.get(source_key)
    if not src:
        console.print(f"[bold red]✗ Unknown source:[/bold red] {source_key}")
        console.print(f"[dim]Available: {', '.join(NEWS_SOURCES.keys())}[/dim]")
        sys.exit(1)

    if src["type"] == "mix":
        return _fetch_mix()

    result = _fetch_single_source(source_key, count)
    if not result:
        console.print(f"[bold red]✗ Failed to fetch from {src['name']}[/bold red]")
        sys.exit(1)
    return result


def render_news(headlines: list[dict], source_name: str, mode: str):
    """Render news headlines in the requested output mode."""
    if mode == "raw":
        for i, h in enumerate(headlines, 1):
            print(f"{i}. {h['title']}")
            if h["source"]:
                print(f"   Source: {h['source']}")
            print(f"   {h['link']}")
            print()
        return

    if mode == "md":
        print(f"# Top Headlines — {source_name}\n")
        for i, h in enumerate(headlines, 1):
            src = f" — *{h['source']}*" if h["source"] else ""
            print(f"{i}. [{h['title']}]({h['link']}){src}")
        return

    # Rich terminal output
    console.print()
    console.print(
        Panel(
            f"[bold]📰 Top Headlines — {source_name}[/bold]",
            expand=False,
            border_style="green",
        )
    )
    console.print()

    for i, h in enumerate(headlines, 1):
        src_tag = f"  [cyan]{h['source']}[/cyan]" if h["source"] else ""
        date_tag = f"  [dim]{h['date']}[/dim]" if h["date"] else ""
        # Extract domain for short display: "https://www.bbc.com/news/..." → "bbc.com"
        try:
            domain = urlparse(h["link"]).netloc.removeprefix("www.")
        except Exception:
            domain = h["link"]
        safe_url = rich_escape(h["link"])
        safe_title = rich_escape(h["title"])
        console.print(f"  [bold]{i:>2}.[/bold] [link={safe_url}]{safe_title}[/link]{src_tag}{date_tag}")
        console.print(f"      [dim][link={safe_url}]🔗 {rich_escape(domain)}[/link][/dim]")
        console.print()

    console.print("[dim]Read any article: freeread <url>[/dim]")
    console.print(
        f"[dim]Sources: {', '.join(NEWS_SOURCES.keys())} · freeread news --source <name>[/dim]"
    )
    console.print()


def main():
    # Start update check in a background thread to avoid blocking startup
    update_thread = threading.Thread(target=check_for_updates, daemon=True)
    update_thread.start()

    parser = argparse.ArgumentParser(
        description="freeread — read paywalled articles in your terminal",
        epilog="Update:\n  pipx upgrade freeread\n  uv pip install --upgrade freeread\n  pip install --upgrade freeread",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", nargs="?", help="Article URL or 'news' for top headlines")
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
    parser.add_argument(
        "--source",
        "-s",
        default=None,
        choices=list(NEWS_SOURCES.keys()),
        help="News source for 'freeread news' (default: mix)",
    )
    parser.add_argument(
        "--version", "-v", action="version", version=f"%(prog)s {__version__}"
    )

    args = parser.parse_args()

    if args.list_methods:
        rprint("\n[bold]Bypass methods:[/bold]")
        for key, (name, _) in METHODS.items():
            rprint(f"  [cyan]{key:8}[/cyan] {name}")
        rprint()
        return

    # Handle "freeread news" command
    if args.url and args.url.lower() == "news":
        output_mode = "md" if args.md else ("raw" if args.raw else "rich")
        if output_mode in ("md", "raw"):
            console.quiet = True
        source_key = getattr(args, "source", DEFAULT_NEWS_SOURCE) or DEFAULT_NEWS_SOURCE
        src = NEWS_SOURCES.get(source_key)
        if not src:
            console.print(f"[bold red]✗ Unknown source:[/bold red] {source_key}")
            console.print(f"[dim]Available: {', '.join(NEWS_SOURCES.keys())}[/dim]")
            sys.exit(1)
        headlines = fetch_news(source_key=source_key, count=10)
        render_news(headlines, source_name=src["name"], mode=output_mode)
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
