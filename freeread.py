#!/usr/bin/env python3
"""
freeread — bypass paywalls and read articles in your terminal.

Tries multiple methods in order:
  1. archive.ph (newest cached version)
  2. 12ft.io proxy (Googlebot spoof via proxy)
  3. Google referer trick (direct fetch with Google referer header)
  4. Wayback Machine (archive.org — slower, used as last resort)

Usage:
  freeread <url>
  freeread <url> --raw           # plain text, no formatting (good for piping)
  freeread <url> --method ph     # force a specific method
"""

import sys
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
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
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


def clean_html_to_text(html: str) -> tuple[str, str]:
    """Extract title and clean article text from HTML."""
    doc = Document(html)
    title = doc.title()
    content_html = doc.summary()

    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = True
    converter.body_width = 0
    text = converter.handle(content_html)

    # Clean up excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return title, text


def try_archive_ph(url: str) -> tuple[str, str, str] | None:
    """Try archive.ph newest cached version."""
    archive_url = f"https://archive.ph/newest/{url}"
    try:
        r = requests.get(
            archive_url, timeout=TIMEOUT, headers=HEADERS_NORMAL, allow_redirects=True
        )
        if r.status_code == 429:
            # Rate limited — try the base archive.ph search
            r = requests.get(
                f"https://archive.ph/{url}", timeout=TIMEOUT,
                headers=HEADERS_NORMAL, allow_redirects=True
            )
        if r.status_code not in (200,):
            return None
        title, text = clean_html_to_text(r.text)
        if len(text) < MIN_CONTENT_LENGTH:
            return None
        return title, text, f"archive.ph ({r.url})"
    except Exception:
        return None


def try_12ft(url: str) -> tuple[str, str, str] | None:
    """Try 12ft.io proxy (routes request via Googlebot)."""
    proxy_url = f"https://12ft.io/proxy?q={url}"
    try:
        r = requests.get(proxy_url, timeout=TIMEOUT, headers=HEADERS_NORMAL)
        if r.status_code != 200:
            return None
        title, text = clean_html_to_text(r.text)
        if len(text) < MIN_CONTENT_LENGTH:
            return None
        return title, text, "12ft.io"
    except Exception:
        return None


def try_google_referer(url: str) -> tuple[str, str, str] | None:
    """Fetch directly with Google referer header — many publishers whitelist Google traffic."""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS_GOOGLE_REFERER)
        if r.status_code != 200:
            return None
        title, text = clean_html_to_text(r.text)
        if len(text) < MIN_CONTENT_LENGTH:
            return None
        return title, text, "Google referer"
    except Exception:
        return None


def try_googlebot(url: str) -> tuple[str, str, str] | None:
    """Fetch directly with Googlebot user-agent."""
    try:
        r = requests.get(url, timeout=TIMEOUT, headers=HEADERS_GOOGLEBOT)
        if r.status_code != 200:
            return None
        title, text = clean_html_to_text(r.text)
        if len(text) < MIN_CONTENT_LENGTH:
            return None
        return title, text, "Googlebot UA"
    except Exception:
        return None


def try_wayback(url: str) -> tuple[str, str, str] | None:
    """Try Wayback Machine (archive.org) — slower, used as fallback."""
    api = f"https://archive.org/wayback/available?url={url}"
    try:
        r = requests.get(api, timeout=TIMEOUT, headers=HEADERS_NORMAL)
        data = r.json()
        snapshot = data.get("archived_snapshots", {}).get("closest", {})
        if not snapshot or snapshot.get("status") != "200":
            return None
        archived_url = snapshot["url"]
        r2 = requests.get(archived_url, timeout=TIMEOUT, headers=HEADERS_NORMAL)
        r2.raise_for_status()
        title, text = clean_html_to_text(r2.text)
        if len(text) < MIN_CONTENT_LENGTH:
            return None
        return title, text, f"Wayback Machine"
    except Exception:
        return None


METHODS = {
    "ph":   ("archive.ph",      try_archive_ph),
    "12ft": ("12ft.io",         try_12ft),
    "ref":  ("Google Referer",  try_google_referer),
    "bot":  ("Googlebot UA",    try_googlebot),
    "wb":   ("Wayback Machine", try_wayback),
}

METHOD_ORDER = ["ph", "12ft", "ref", "bot", "wb"]


def fetch_article(url: str, method: str | None = None) -> tuple[str, str, str] | None:
    methods = [method] if method else METHOD_ORDER
    for key in methods:
        name, fn = METHODS[key]
        console.print(f"  [dim]→ trying {name}...[/dim]", end="\r")
        result = fn(url)
        if result:
            console.print(" " * 50, end="\r")
            return result
    return None


def render_article(title: str, text: str, source: str, raw: bool = False):
    if raw:
        print(f"# {title}\n\n{text}")
        return

    console.print()
    console.print(Panel(
        f"[bold]{title}[/bold]\n[dim]via {source}[/dim]",
        expand=False,
        border_style="green"
    ))
    console.print()
    console.print(Markdown(text))


def main():
    parser = argparse.ArgumentParser(
        description="freeread — read paywalled articles in your terminal",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
methods:
  ph    archive.ph (fastest — checks public cache)
  12ft  12ft.io proxy
  ref   Google Referer header trick
  bot   Googlebot user-agent spoof
  wb    Wayback Machine (slowest — last resort)

examples:
  freeread https://www.nytimes.com/some-article
  freeread https://ft.com/article --method wb
  freeread https://wsj.com/article --raw | less
        """
    )
    parser.add_argument("url", nargs="?", help="Article URL to fetch")
    parser.add_argument("--raw", action="store_true", help="Plain text output (no rich formatting)")
    parser.add_argument("--method", "-m", choices=list(METHODS.keys()), help="Force a specific method")
    parser.add_argument("--list-methods", action="store_true", help="List available bypass methods")

    args = parser.parse_args()

    if args.list_methods:
        rprint("\n[bold]Available methods:[/bold]")
        for key, (name, _) in METHODS.items():
            rprint(f"  [cyan]{key:6}[/cyan] {name}")
        rprint()
        sys.exit(0)

    if not args.url:
        parser.print_help()
        sys.exit(1)

    console.print(f"\n[bold green]freeread[/bold green] [dim]{args.url}[/dim]\n")

    result = fetch_article(args.url, args.method)

    if not result:
        console.print("[bold red]✗ Failed[/bold red] — no archive found via any method.")
        console.print("[dim]Try a different --method, or the paywall may be fully server-side.[/dim]\n")
        sys.exit(1)

    title, text, source = result
    console.print(f"[bold green]✓[/bold green] Retrieved via [cyan]{source}[/cyan]")
    render_article(title, text, source, raw=args.raw)


if __name__ == "__main__":
    main()
