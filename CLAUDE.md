# CLAUDE.md

Guidance for Claude Code (claude.ai/code) when working in this repository.

## Overview

Scrapy-based crawler that walks **whole domains** (every language section) from a
curated list of news sites and stores each page's **raw HTML** as-is, for a
**parallel / multilingual corpus**. It is a sibling of `../kurdish_scrapy` but
differs fundamentally:

- **No extraction** — it does not run Trafilatura or parse metadata.
- **No filtering** — it does not filter by language, length, or content.

Each crawled HTML page becomes one record with four fields: `url`, `html`
(the raw HTML), `content_type` (the response's `Content-Type` header), and
`fetched_at` (the ISO 8601 UTC timestamp of when the page was
fetched). All downstream work (extraction, metadata, document↔sentence alignment)
runs against this raw HTML, so the source is never lost and can be re-processed
without re-crawling. `url` is the provenance key.

## Commands

Pipenv with Python 3.10:

```bash
pipenv --python 3.10 && pipenv shell && pipenv install
```

Run (reads `parallel_data_domains.json`):

```bash
python main.py --output output.jsonl                              # .jsonl | .json | .csv
python main.py --output output.jsonl --log-file logs/crawler.log --log-level INFO
python stats.py --file-name output.jsonl                          # dataset statistics
```

No test suite, linter, or build step. `.env` (loaded by `kurdish_crawl/settings.py`)
holds only the optional `SCRAPEOPS_API_KEY`.

## Architecture

The flow is **not** the standard `scrapy crawl` CLI. `main.py` → `run_crawler.py`
drives a `CrawlerProcess` programmatically, scheduling one spider per domain.

- **`main.py`**: loads `parallel_data_domains.json`, optionally follows redirects to resolve the
  final domains (news domains move often; `--no-resolve` skips this), configures
  logging, and calls `run_crawler`.
- **`run_crawler.py`**: for each domain, uses `SitemapSpider` when a sitemap is
  discoverable (`sitemap_discovery.py`: robots.txt first, then `SITEMAP_PATTERNS`),
  else `RecursiveSpider`. No language/section logic.
- **Spiders** (`kurdish_crawl/spiders/`): both check the response is HTML
  (`UrlExtractor.content_type`) and `yield {"url": response.url, "html":
  response.text, "content_type": <Content-Type header>, "fetched_at": <ISO 8601
  UTC>}`. `SitemapSpider` matches every
  sitemap entry (`sitemap_rules =
  [("", "parse")]`). `RecursiveSpider` follows **every** in-domain link
  (`UrlExtractor.extract`), which also drops media/binary URLs.
- **Dedup, two layers:**
  - **`ArticleIdDupeFilter`** (`dupefilters.py`, wired via `DUPEFILTER_CLASS`): the
    default URL-fingerprint dedup plus a per-domain article-ID check. For domains in
    `extractor/article_id.py`'s `DOMAIN_ID_EXTRACTORS` (e.g. jinnews →
    `/content/view/<id>`), the same article reached under several paths shares one
    ID and the duplicate is dropped **before fetching** — the only dedup that saves
    crawl time. Non-article URLs return `None` and fall back to URL dedup, so
    listing/pagination pages are never collapsed. It keeps two ID sets: an
    in-memory *enqueued* set (dedup within a run) and a *stored* set recorded on the
    `item_scraped` signal — only the latter persists, next to the output shards as
    `<host>_seen_ids.json` (the feed dir). Persisting only *stored* IDs means a
    capped/interrupted run never marks an un-fetched article as done; a request for
    an article stored in an earlier run is skipped, so `full` crawls are incremental.
    No file-based feed dir → per-process only.
  - **`CanonicalDedupMixin`** (`spiders/dedup.py`): both spiders mix it in and skip
    storing a page whose `<link rel="canonical">` was already seen this run. Generic
    fallback for domains with no ID extractor. Store-time only (the page is already
    fetched); pages without a canonical tag are never treated as dupes. The recursive
    spider still follows links from a duplicate page so coverage is not lost.
- **`extractor/url_extractor.py`**: the only remaining extractor helper — link
  extraction, same-domain checks, media/non-HTML filtering, canonical-URL
  reading (`UrlExtractor.canonical_url`), also used by
  `MediaFilterMiddleware`).

## Key config points

- Politeness: `CONCURRENT_REQUESTS_PER_DOMAIN = 1`, `DOWNLOAD_DELAY = 1`,
  `ROBOTSTXT_OBEY = True`.
- `MediaFilterMiddleware` rejects media/binary URLs before download;
  `ScrapeOpsFakeUserAgentMiddleware` self-disables without `SCRAPEOPS_API_KEY`.
- Feeds use `overwrite: False`, so reusing an output file appends.
- Modal full runs set Scrapy's `JOBDIR` (`output/full/<host>_job/`) via
  `run_crawler(job_dir=...)`, persisting the request queue + seen fingerprints to
  the Volume so an interrupted crawl resumes where it stopped. One spider per
  process only (enforced in `run_crawler`); capped smoke tests get no JOBDIR.
  `crawl_domain` has `timeout=12h` and `retries=5`; a timeout is retried and each
  retry resumes the same JOBDIR. `run_crawler(commit_callback=volume.commit,
  commit_interval=120)` runs a daemon thread that flushes the Volume every ~2 min
  during the crawl, so a kill between the end-commits loses almost no progress.
- `html` is large raw HTML, so **`.jsonl` is the preferred output** (one object
  per line); `.json` builds one giant array and `.csv` is awkward for HTML.
- The provenance contract is the four fields `url` + `html` + `content_type` + `fetched_at`.
  Do not add extraction/metadata here — that belongs downstream.
