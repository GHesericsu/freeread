# freeread 📰

Read paywalled articles directly in your terminal. No browser. No ads. No friction.

```
freeread https://www.nytimes.com/some-article
```

## How it works

`freeread` tries multiple bypass methods in order until one works:

- `ph` archive.ph (public cache)
- `12ft` 12ft.io proxy
- `ref` Google Referer header trick
- `cookie` cookie-clearing (metered paywalls)
- `http` Scrapling HTTP fetch (curl_cffi impersonation)
- `bot` Googlebot user-agent spoof
- `stealth` Scrapling headless browser plus Cloudflare bypass
- `dynamic` Scrapling Playwright render (JS-heavy)
- `wb` Wayback Machine (slowest)

Scrapling methods are optional. Install with `pip install freeread[scrapling]`.

## Install

**Requirements:** Python 3.10+

### pipx (recommended for most users)

Installs `freeread` as an isolated CLI tool. No virtualenv setup, no conflicts.

```bash
pipx install freeread

# With Scrapling-powered methods
pipx install "freeread[scrapling]"
```

> Install pipx first if needed: `pip install pipx` or `brew install pipx`

### uv (recommended for developers)

[uv](https://github.com/astral-sh/uv) is a fast Rust-based Python package manager.

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install freeread
uv pip install freeread

# With Scrapling-powered methods
uv pip install "freeread[scrapling]"

# Or run directly without installing
uv run freeread <url>
```

### pip (classic)

```bash
pip install freeread

# With Scrapling-powered methods
pip install "freeread[scrapling]"
```

> If you hit `externally-managed-environment` on Ubuntu/Debian, add `--break-system-packages` or use pipx/uv instead.

### Run from source

```bash
git clone https://github.com/GHesericsu/freeread
cd freeread
pip install -e .
freeread <url>
```

## Update

```bash
# pipx
pipx upgrade freeread

# uv
uv pip install --upgrade freeread

# pip
pip install --upgrade freeread
```

## Usage

```bash
# Read any article
freeread https://www.wsj.com/some-article

# Clean markdown output (good for piping to an LLM)
freeread https://www.theatlantic.com/... --md > article.md

# Force a specific method
freeread https://ft.com/article --method ph
freeread https://ft.com/article --method cookie
freeread https://ft.com/article --method stealth

# Use a proxy (optional)
freeread https://ft.com/article --proxy "$DECODO_MOBILE_PROXY"

# Use your own session cookies (for hard paywalls like WSJ Live)
freeread https://wsj.com/... --cookies ./wsj-cookies.txt

# Raw text output (no rich formatting, good for piping)
freeread https://nytimes.com/article --raw

# List available methods
freeread --list-methods

# Top headlines from multiple sources (default)
freeread news

# Pick a single source
freeread news --source bbc
freeread news --source hn
freeread news -s aljazeera --raw

# Available sources: mix (default), google, bbc, aljazeera, npr, hn, reddit
```

## Limitations

- Hard server-side paywalls (FT, WSJ 2024+) may not work if no archive exists
- Some sites actively block archive services
- Brand new articles may not have an archive yet — try `--method bot` first

## Notes

This tool only accesses publicly cached or archived versions of articles. It does not circumvent authentication or access content that was never publicly available.

## License

MIT
