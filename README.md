# freeread 📰

Read paywalled articles directly in your terminal. No browser. No ads. No friction.

```
freeread https://www.nytimes.com/some-article
```

## How it works

`freeread` tries multiple bypass methods in order until one works:

| Method | How |
|---|---|
| **Wayback Machine** | Finds cached snapshot via archive.org API |
| **archive.ph** | Fetches newest cached version from archive.ph |
| **12ft.io** | Routes through 12ft.io proxy |
| **Googlebot UA** | Fetches directly with Googlebot user-agent (many publishers whitelist Google) |

## Install

**Requirements:** Python 3.10+

```bash
pip install freeread
```

Or run directly from source:

```bash
git clone https://github.com/GHesericsu/freeread
cd freeread
pip install -e .
freeread <url>
```

## Usage

```bash
# Read any article
freeread https://www.wsj.com/some-article

# Force a specific method
freeread https://ft.com/article --method wb    # Wayback Machine
freeread https://ft.com/article --method ph    # archive.ph
freeread https://ft.com/article --method 12ft  # 12ft.io
freeread https://ft.com/article --method bot   # Googlebot UA

# Raw text output (no rich formatting, good for piping)
freeread https://nytimes.com/article --raw

# List available methods
freeread --list-methods
```

## Limitations

- Hard server-side paywalls (FT, WSJ 2024+) may not work if no archive exists
- Some sites actively block archive services
- Brand new articles may not have an archive yet — try `--method bot` first

## Notes

This tool only accesses publicly cached or archived versions of articles. It does not circumvent authentication or access content that was never publicly available.

## License

MIT
