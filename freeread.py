#!/usr/bin/env python3
"""
freeread — bypass paywalls and read articles in your terminal.

Bypass methods (tried in order by default):
  ph     archive.ph (public cache)
  12ft   12ft.io proxy
  ref    Google Referer header trick
  cookie Cookie-clearing (beats metered paywalls like NYT, WaPo)
  bot    Googlebot user-agent spoof
  stealth Scrapling StealthyFetcher (Cloudflare bypass, headless)
  wb     Wayback Machine (slowest, last resort)

Output modes:
  default  Rich terminal rendering
  --md     Clean markdown (great for piping to an LLM)
  --raw    Plain text, no formatting
"""

import sys
import os
import argparse
import requests
from readability import Document
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich import print as rprint
import html2text
import re

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

# Runtime options set by CLI
RUNTIME = {
    "proxy": None,
}


# ── HTML → clean text ────────────────────────────────────────────────────────

def clean_html_to_text(html: str) -> tuple[str, str]:
    """Extract title + clean markdown from raw HTML via readability + html2text."""
    doc = Document(html)
    title = doc.title()
    content_html = doc.summary()

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0
    text = converter.handle(content_html)
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return title, text


def is_sufficient(text: str) -> bool:
    return len(text) >= MIN_CONTENT_LENGTH


# ── Bypass methods ────────────────────────────────────────────────────────────

def try_archive_ph(url: str):
    """archive.ph — public cache, fastest."""
    proxies = None
    if RUNTIME.get("proxy"):
        proxies = {"http": RUNTIME["proxy"], "https": RUNTIME["proxy"]}

    for archive_url in [f"https://archive.ph/newest/{url}", f"https://archive.ph/{url}"]:
        try:
            r = requests.get(
                archive_url,
                timeout=TIMEOUT,
                headers=HEADERS_NORMAL,
                allow_redirects=True,
                proxies=proxies,
            )
            if r.status_code == 200:
                title, text = clean_html_to_text(r.text)
                if is_sufficient(text):
                    return title, text, "archive.ph"
        except Exception:
            continue
    return None


def try_12ft(url: str):
    """12ft.io proxy."""
    try:
        proxies = None
        if RUNTIME.get("proxy"):
            proxies = {"http": RUNTIME["proxy"], "https": RUNTIME["proxy"]}
        r = requests.get(f"https://12ft.io/proxy?q={url}", timeout=TIMEOUT, headers=HEADERS_NORMAL, proxies=proxies)
        if r.status_code == 200:
            title, text = clean_html_to_text(r.text)
            if is_sufficient(text):
                return title, text, "12ft.io"
    except Exception:
        pass
    return None


def try_google_referer(url: str):
    """Google Referer trick — publishers whitelist Google traffic."""
    try:
        proxies = None
        if RUNTIME.get("proxy"):
            proxies = {"http": RUNTIME["proxy"], "https": RUNTIME["proxy"]}
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS_GOOGLE_REFERER, proxies=proxies)
        if r.status_code == 200:
            title, text = clean_html_to_text(r.text)
            if is_sufficient(text):
                return title, text, "Google referer"
    except Exception:
        pass
    return None


def try_cookie_clear(url: str):
    """
    Cookie-clearing bypass — works on metered paywalls (NYT, WaPo, The Atlantic).
    These sites track article count via cookies. Fresh session = full access.
    Technique inspired by Bypass Paywalls Clean (BPC).
    """
    try:
        session = requests.Session()
        session.cookies.clear()
        proxies = None
        if RUNTIME.get("proxy"):
            proxies = {"http": RUNTIME["proxy"], "https": RUNTIME["proxy"]}

        # Fresh session, no cookies, Google referer
        r = session.get(url, timeout=TIMEOUT, headers=HEADERS_GOOGLE_REFERER, proxies=proxies)
        if r.status_code == 200:
            title, text = clean_html_to_text(r.text)
            if is_sufficient(text):
                return title, text, "cookie-clear"
    except Exception:
        pass
    return None


def try_googlebot(url: str):
    """Googlebot user-agent spoof."""
    try:
        proxies = None
        if RUNTIME.get("proxy"):
            proxies = {"http": RUNTIME["proxy"], "https": RUNTIME["proxy"]}
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS_GOOGLEBOT, proxies=proxies)
        if r.status_code == 200:
            title, text = clean_html_to_text(r.text)
            if is_sufficient(text):
                return title, text, "Googlebot UA"
    except Exception:
        pass
    return None


def try_scrapling_http(url: str):
    """Scrapling Fetcher (curl_cffi) — better browser impersonation than requests."""
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
        r = f.get(url, **kwargs)
        if getattr(r, "status", None) == 200 and getattr(r, "html_content", None):
            title, text = clean_html_to_text(r.html_content)
            if is_sufficient(text):
                return title, text, "Scrapling http"
    except Exception:
        pass
    return None


def try_stealth(url: str):
    """
    Scrapling StealthyFetcher — headless browser with Cloudflare bypass.
    Works on JS-rendered sites and aggressive bot-detection paywalls.
    Slower but more powerful than simple HTTP methods.
    """
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
        page = fetcher.fetch(url, **kwargs)
        if page and page.html_content:
            title, text = clean_html_to_text(page.html_content)
            if is_sufficient(text):
                return title, text, "Scrapling stealth"
    except Exception:
        pass
    return None


def try_dynamic(url: str):
    """Scrapling DynamicFetcher (Playwright) — for JS-heavy pages."""
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
        page = fetcher.fetch(url, **kwargs)
        if page and page.html_content:
            title, text = clean_html_to_text(page.html_content)
            if is_sufficient(text):
                return title, text, "Scrapling dynamic"
    except Exception:
        pass
    return None


def try_wayback(url: str):
    """Wayback Machine — slower, comprehensive archive."""
    try:
        proxies = None
        if RUNTIME.get("proxy"):
            proxies = {"http": RUNTIME["proxy"], "https": RUNTIME["proxy"]}

        r = requests.get(
            f"https://archive.org/wayback/available?url={url}",
            timeout=TIMEOUT,
            headers=HEADERS_NORMAL,
            proxies=proxies,
        )
        data = r.json()
        snapshot = data.get("archived_snapshots", {}).get("closest", {})
        if not snapshot or snapshot.get("status") != "200":
            return None
        r2 = requests.get(snapshot["url"], timeout=TIMEOUT, headers=HEADERS_NORMAL, proxies=proxies)
        r2.raise_for_status()
        title, text = clean_html_to_text(r2.text)
        if is_sufficient(text):
            return title, text, "Wayback Machine"
    except Exception:
        pass
    return None


# ── Method registry ───────────────────────────────────────────────────────────

METHODS = {
    "ph":      ("archive.ph",        try_archive_ph),
    "12ft":    ("12ft.io",           try_12ft),
    "ref":     ("Google Referer",    try_google_referer),
    "cookie":  ("Cookie-clear",      try_cookie_clear),
    "http":    ("Scrapling http",    try_scrapling_http),
    "bot":     ("Googlebot UA",      try_googlebot),
    "stealth": ("Scrapling stealth", try_stealth),
    "dynamic": ("Scrapling dynamic", try_dynamic),
    "wb":      ("Wayback Machine",   try_wayback),
}

METHOD_ORDER = ["ph", "12ft", "ref", "cookie", "http", "bot", "stealth", "dynamic", "wb"]


def fetch_article(url: str, method: str | None = None):
    methods = [method] if method else METHOD_ORDER
    for key in methods:
        name, fn = METHODS[key]
        console.print(f"  [dim]→ {name}...[/dim]", end="\r")
        result = fn(url)
        if result:
            console.print(" " * 50, end="\r")
            return result
    return None


# ── Output rendering ──────────────────────────────────────────────────────────

def render_article(title: str, text: str, source: str, mode: str = "rich"):
    """
    mode: "rich" (default), "md" (clean markdown for LLMs), "raw" (plain text)
    """
    if mode == "raw":
        print(f"# {title}\n\n{text}")
        return

    if mode == "md":
        # Clean markdown — no ANSI, perfect for piping to an LLM
        print(f"# {title}\n\n> *via {source}*\n\n{text}")
        return

    # Rich terminal rendering
    console.print()
    console.print(Panel(
        f"[bold]{title}[/bold]\n[dim]via {source}[/dim]",
        expand=False,
        border_style="green"
    ))
    console.print()
    console.print(Markdown(text))


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="freeread — read paywalled articles in your terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
methods (default order: ph → 12ft → ref → cookie → http → bot → stealth → dynamic → wb):
  ph       archive.ph public cache
  12ft     12ft.io proxy
  ref      Google Referer header trick
  cookie   Cookie-clearing (metered paywalls: NYT, WaPo, The Atlantic)
  http     Scrapling HTTP fetch (curl_cffi impersonation)
  bot      Googlebot user-agent spoof
  stealth  Scrapling headless browser + Cloudflare bypass
  dynamic  Scrapling Playwright fetch (JS-heavy)
  wb       Wayback Machine (slowest)

output modes:
  (default)   Rich terminal with markdown rendering
  --md        Clean markdown — pipe to an LLM or save to file
  --raw       Plain text

examples:
  freeread https://www.nytimes.com/some-article
  freeread https://ft.com/article --method cookie
  freeread https://wsj.com/article --md | llm "summarize this"
  freeread https://wsj.com/article --md > article.md
        """
    )
    parser.add_argument("url", nargs="?", help="Article URL to fetch")
    parser.add_argument(
        "--method", "-m", choices=list(METHODS.keys()),
        help="Force a specific bypass method"
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="Optional proxy URL (also reads FREEREAD_PROXY or DECODO_MOBILE_PROXY env vars)"
    )
    parser.add_argument(
        "--md", action="store_true",
        help="Output clean markdown (ideal for LLM consumption)"
    )
    parser.add_argument(
        "--raw", action="store_true",
        help="Output plain text (no formatting)"
    )
    parser.add_argument(
        "--list-methods", action="store_true",
        help="List available bypass methods and exit"
    )

    args = parser.parse_args()

    if args.list_methods:
        rprint("\n[bold]Bypass methods:[/bold]")
        for key, (name, _) in METHODS.items():
            rprint(f"  [cyan]{key:8}[/cyan] {name}")
        rprint()
        sys.exit(0)

    if not args.url:
        parser.print_help()
        sys.exit(1)

    # Proxy resolution
    proxy = args.proxy or os.environ.get("FREEREAD_PROXY") or os.environ.get("DECODO_MOBILE_PROXY")
    if proxy:
        RUNTIME["proxy"] = proxy

    output_mode = "md" if args.md else ("raw" if args.raw else "rich")

    # Suppress status output for clean pipe modes
    if output_mode in ("md", "raw"):
        console.quiet = True

    console.print(f"\n[bold green]freeread[/bold green] [dim]{args.url}[/dim]\n")

    result = fetch_article(args.url, args.method)

    if not result:
        if output_mode not in ("md", "raw"):
            console.print("[bold red]✗ Failed[/bold red] — no content found via any method.")
            console.print("[dim]Try --method stealth for JS-heavy sites, or the paywall may be fully server-side.[/dim]\n")
        else:
            print("ERROR: Could not retrieve article.", file=sys.stderr)
        sys.exit(1)

    title, text, source = result

    if output_mode == "rich":
        console.print(f"[bold green]✓[/bold green] Retrieved via [cyan]{source}[/cyan]")

    render_article(title, text, source, mode=output_mode)


if __name__ == "__main__":
    main()
