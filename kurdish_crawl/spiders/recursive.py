from datetime import datetime, timezone

import scrapy

from extractor.url_extractor import UrlExtractor
from kurdish_crawl.spiders.base import BaseSpider
from kurdish_crawl.spiders.dedup import CanonicalDedupMixin


class RecursiveSpider(CanonicalDedupMixin, BaseSpider):
    """Crawl a whole domain by following every in-domain link.

    No extraction: each HTML page is stored as-is — ``{"url", "html",
    "content_type", "fetched_at"}`` where ``html`` is the raw HTML,
    ``content_type`` is the response's ``Content-Type`` header, and
    ``fetched_at`` is the ISO 8601 UTC timestamp of when the page was fetched.

    Articles served under several URLs are stored once, keyed on their
    ``<link rel="canonical">`` (see :class:`CanonicalDedupMixin`). Links are
    still followed from duplicate pages so no section is missed; the request
    dupe filter stops them being fetched twice.
    """

    name = "recursive_spider"

    def parse(self, response):
        self.logger.debug("Processing %s", response.url)
        if not UrlExtractor.content_type(response):
            self.logger.debug("Skipped non-HTML response: %s", response.url)
            return

        if not self.is_duplicate(response):
            yield {
                "url": response.url,
                "html": response.text,
                "content_type": UrlExtractor.content_type_header(response),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            }

        url_extractor = UrlExtractor()
        for url in url_extractor.extract(response):
            yield scrapy.Request(url, callback=self.parse, dont_filter=False)
