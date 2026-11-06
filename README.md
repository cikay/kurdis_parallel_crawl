# Kurdish Parallel Scrapy

A [Scrapy](https://www.scrapy.org/)-based crawler for building a **parallel /
multilingual news corpus**. It walks each configured domain **completely** —
every language section — and stores each page's **raw HTML** as-is.

It does **no extraction and no filtering**. Each record is exactly four fields:

| Field | Description |
|-------|-------------|
| `url` | The page URL (final URL after redirects). |
| `html` | The raw HTML of the page, unchanged. |
| `content_type` | The response's `Content-Type` header (e.g. `text/html; charset=utf-8`). |
| `fetched_at` | ISO 8601 UTC timestamp of when the page was fetched. |

This is the **source dataset** you publish and keep intact. All later work —
text extraction, metadata parsing, and aligning documents to sentences in another
dataset — is done downstream against this raw HTML, so the source is never lost
and extraction can be redone any time without re-crawling. `url` is the key that
ties every derived record back to its source.

## Prerequisites

- Python 3.10
- [Pipenv](https://pipenv.pypa.io/en/latest/)

## Installation

```bash
pipenv --python 3.10
pipenv shell
pipenv install
```

Optional `.env`:

```bash
# SCRAPEOPS_API_KEY="..."    # optional user-agent rotation
```

## Configure target domains

Domains live in `parallel_data_domains.json`, a flat list of URLs:

```json
[
  "https://www.basnews.com",
  "http://jinnews.net",
  "https://jinhaagency.com",
  "https://www.botantimes.com",
  "https://mezopotamyaajansi.com",
  "https://anf-news.com",
  "https://www.rupelanu.com",
  "https://kurdpa.net"
]
```

## Usage

```bash
# Use .jsonl — content is large raw HTML; one JSON object per line.
python main.py --output output.jsonl

# Production: log to a file
python main.py --output output.jsonl --log-file logs/crawler.log --log-level INFO
```

Flags:

| Flag | Description |
|------|-------------|
| `-o, --output` | Output file (`.jsonl`, `.json`, `.csv`). `.jsonl` strongly recommended. |
| `--domains` | Domains file (default `parallel_data_domains.json`). |
| `--no-resolve` | Skip following redirects to resolve final domains first. |
| `--log-file` / `--log-level` | Logging destination / verbosity. |

For each domain the runner uses `SitemapSpider` when a sitemap is discoverable
(via `robots.txt` then common sitemap paths), otherwise `RecursiveSpider`, which
follows every in-domain link. Media/binary URLs are skipped; only `text/html`
responses are stored.

## Run on Modal (cloud)

The crawler is CPU-only and I/O-bound, so it runs cheaply on
[Modal](https://modal.com/). `modal_app/app.py` fans out **one container per
domain** (all in parallel), and each writes its raw-HTML shard to a persistent
Modal Volume (`kurdish_parallel_crawl`, mounted at `/vol`) so output survives the
container ending. No GPU is requested anywhere.

`modal` is installed with the other dependencies (`pipenv install`). Authenticate
once per machine with `modal setup`.

```bash
# Full run — resolve redirects, then crawl every domain in the file (detached)
modal run --detach modal_app/app.py::crawl

# Smoke test: crawl only the URL(s) you pass, capped per domain (skips resolution)
modal run modal_app/app.py::crawl --urls "http://jinnews.net" --max-pages 20

# Cap every domain in the file (quick end-to-end check of all sites)
modal run modal_app/app.py::crawl --max-pages 5

# Merge a run type's per-domain shards into one output.jsonl on the Volume
modal run modal_app/app.py::merge --run-type full
```

`crawl` flags:

| Flag | Description |
|------|-------------|
| `--urls` | Comma-separated URL(s) to crawl instead of the domains file. Passed as-is (redirect resolution skipped). |
| `--max-pages` | Cap on **stored pages per domain** (`0` = full-domain walk). Soft stop — in-flight requests still finish, so a shard may hold a few extra. |
| `--domains-file` | Domains file to read (default `parallel_data_domains.json`). |
| `--no-resolve` | Skip following redirects to resolve final domains first. |

Output is organised by **run type**, not a timestamp: a full-domain walk writes
under `output/full/<host>.jsonl` (with a matching `.log`), and a capped smoke test
under `output/max_pages_<n>/` (`<n>` = the `--max-pages` value). Feeds append
(`overwrite: False`) and each domain's set of
**stored** article IDs persists beside its shard as `<host>_seen_ids.json`, so
re-running a `full` crawl is **incremental** — only articles not already stored in
an earlier run are fetched and appended. (Only stored IDs are saved, so a capped or
interrupted run never marks an un-fetched article as done.)

**Resume after interruption (full runs):** every full-run domain also gets a
Scrapy [JOBDIR](https://docs.scrapy.org/en/latest/topics/jobs.html) at
`output/full/<host>_job/`, where Scrapy persists the pending request queue and
seen-request fingerprints to the Volume. If a container is cut short (the 12-hour
timeout, or a Modal preemption), the crawl resumes each domain exactly where it
stopped — the pending URLs continue and nothing already fetched is re-fetched. Progress
(shard + JOBDIR state) is **flushed to the Volume every ~2 minutes** during the
crawl, so a kill between flushes loses only seconds of work. A
**timeout is retried automatically** (the `crawl_domain` function has `retries=5`),
and each retry re-runs against the same JOBDIR, so progress accumulates across
attempts until the domain finishes — no manual re-run needed. (You can still re-run
the `full` crawl by hand to continue too.) Once a domain finishes, its queue is
empty, so a later re-run does nothing; delete its `<host>_job/` folder to re-scan
the site for new articles from scratch. (Capped smoke tests get no JOBDIR — they're
disposable.)

### Download a run into a local `data/` folder

Runs live on the Modal Volume until you pull them down. Use the Modal CLI to
copy a run-type folder into a local `data/` directory (git-ignored, so large HTML
never gets committed).

```bash
# 1. List what's on the Volume for a run type.
modal volume ls kurdish_parallel_crawl output/full

# 2. (Recommended) Merge the run type's per-domain shards into one output.jsonl first.
modal run modal_app/app.py::merge --run-type full

# 3. Download the whole folder into data/full/ on your machine (trailing slash =
#    directory destination, so the shards don't collapse onto one path).
mkdir -p data
modal volume get kurdish_parallel_crawl output/full data/
```

You now have `data/full/` locally, containing each domain's `<host>.jsonl`
shard, its `<host>.log`, and (if you merged) `output.jsonl`. Point the stats
script at the merged file to check it:

```bash
python stats.py --file-name data/full/output.jsonl
```

Other handy `modal volume` commands:

```bash
# Download just the merged file instead of the whole folder.
modal volume get kurdish_parallel_crawl output/full/output.jsonl data/full/output.jsonl

# Download one domain's shard only.
modal volume get kurdish_parallel_crawl output/full/<host>.jsonl data/full/<host>.jsonl
```

## Dataset statistics

```bash
python stats.py --file-name output.jsonl
```

Prints total pages, unique URLs, empty-content count, total HTML size, and
pages per domain.

## Project structure

```
├── kurdish_crawl/
│   ├── spiders/
│   │   ├── sitemap.py        # whole-domain sitemap spider
│   │   ├── recursive.py      # whole-domain recursive fallback
│   │   └── base.py
│   ├── middlewares.py        # media filter + optional UA rotation
│   ├── sitemap_discovery.py
│   └── settings.py
├── extractor/
│   └── url_extractor.py      # link extraction + media/non-HTML filtering
├── run_crawler.py            # spider selection + feed setup
├── main.py                   # local CLI entrypoint
├── modal_app/
│   └── app.py                # Modal cloud entrypoints (crawl / merge)
├── stats.py                  # dataset statistics
├── parallel_data_domains.json              # crawl targets
└── Pipfile
```
