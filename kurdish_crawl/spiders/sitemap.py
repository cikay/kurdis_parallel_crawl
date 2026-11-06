from datetime import datetime, timezone

from scrapy.spiders import SitemapSpider as BaseSitemapSpider

from extractor.url_extractor import UrlExtractor
from kurdish_crawl import sitemap_discovery
from kurdish_crawl.spiders.dedup import CanonicalDedupMixin


class SitemapSpider(CanonicalDedupMixin, BaseSitemapSpider):
    """Crawl every URL a domain advertises in its sitemap(s).

    No extraction: each HTML page is stored as-is — ``{"url", "html",
    "content_type", "fetched_at"}`` where ``html`` is the raw HTML,
    ``content_type`` is the response's ``Content-Type`` header, and
    ``fetched_at`` is the ISO 8601 UTC timestamp of when the page was fetched.

    Articles served under several URLs are stored once, keyed on their
    ``<link rel="canonical">`` (see :class:`CanonicalDedupMixin`).
    """

    name = "sitemap_spider"

    def __init__(self, sitemap_urls, *args, **kwargs):
        # Match every sitemap entry. Must be set before super().__init__ so
        # Scrapy compiles the callbacks from it.
        self.sitemap_rules = [("", "parse")]
        super().__init__(*args, **kwargs)
        self.sitemap_urls = sitemap_urls

    def parse(self, response):
        self.logger.debug("Processing %s", response.url)
        if not UrlExtractor.content_type(response):
            self.logger.debug("Skipped non-HTML response: %s", response.url)
            return

        if self.is_duplicate(response):
            return

        yield {
            "url": response.url,
            "html": response.text,
            "content_type": UrlExtractor.content_type_header(response),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        }

    @classmethod
    def get_sitemap_urls(cls, url):
        return sitemap_discovery.get_sitemap_urls(url)
