# Scrapy settings for kurdish_crawl project
#
# This crawler walks *whole domains* (every language section) and stores each
# page's raw HTML as-is — one record per page: {"url", "html", "fetched_at"}. No text
# extraction, metadata parsing or language filtering. See CLAUDE.md.
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
import os

import dotenv


dotenv.load_dotenv()


BOT_NAME = "kurdish_crawl"

SPIDER_MODULES = ["kurdish_crawl.spiders"]
NEWSPIDER_MODULE = "kurdish_crawl.spiders"

ADDONS = {}

# Obey robots.txt rules by default (polite on every domain).
ROBOTSTXT_OBEY = True

# ...except for these hosts, which disallow generic crawlers in robots.txt but
# that we have consciously chosen to crawl anyway (public news, polite delays).
# RobotsExemptMiddleware sets `dont_obey_robotstxt` per request for these only.
# Use www-stripped hosts (as UrlExtractor.get_domain returns).
ROBOTSTXT_EXEMPT_DOMAINS = [
    "mezopotamyaajansi.com",
]

# URL-fingerprint dedup plus per-domain article-ID dedup (see
# kurdish_crawl/dupefilters.py and extractor/article_id.py). Per-domain seen-ID
# sets persist as <host>_seen_ids.json next to the output shards (the feed dir).
DUPEFILTER_CLASS = "kurdish_crawl.dupefilters.ArticleIdDupeFilter"

# Crawl politeness — be a good citizen on small news sites.
CONCURRENT_REQUESTS_PER_DOMAIN = 3
DOWNLOAD_DELAY = 0.3

# Default User-Agent. Scrapy's own UA ("Scrapy/… +scrapy.org") is rejected with
# HTTP 403 by sites behind bot protection (e.g. botantimes.com). A real browser
# UA avoids that. When ScrapeOps rotation is active (SCRAPEOPS_API_KEY set) the
# ScrapeOpsFakeUserAgentMiddleware overrides this per request; otherwise this is
# what every request sends.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Downloader middlewares: drop media/non-HTML requests, optionally rotate UAs.
DOWNLOADER_MIDDLEWARES = {
    # Must precede RobotsTxtMiddleware (100) so the exempt flag is set in time.
    "kurdish_crawl.middlewares.RobotsExemptMiddleware": 50,
    "kurdish_crawl.middlewares.MediaFilterMiddleware": 300,
    "kurdish_crawl.middlewares.ScrapeOpsFakeUserAgentMiddleware": 400,
}

FEED_EXPORT_ENCODING = "utf-8"

SCRAPEOPS_API_KEY = os.getenv("SCRAPEOPS_API_KEY")
SCRAPEOPS_FAKE_USER_AGENT_ENABLED = True
SCRAPEOPS_NUM_RESULTS = 10
SCRAPEOPS_FAKE_USER_AGENT_ENDPOINT = "https://headers.scrapeops.io/v1/user-agents?"

SITEMAP_PATTERNS = [
    "sitemap.xml",
    "post-sitemap.xml",
    "blog-posts-sitemap.xml",
    "sitemaps/post-sitemap.xml",
    "sitemap_index.xml",
    "sitemaps/sitemap_index.xml",
    "sitemaps/sitemap.xml",
    "sitemaps/sitemap-articles.xml",
    "/sitemap-index.xml",
    "/sitemaps.xml",
    "/sitemap/sitemap.xml",
    "/wp-sitemap.xml",  # WordPress
    "/news-sitemap.xml",
]
