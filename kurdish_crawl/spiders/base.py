from urllib.parse import urlparse

import scrapy


class BaseSpider(scrapy.Spider):
    custom_settings = {
        "DEPTH_LIMIT": 0,  # 0 = no depth limit (crawl the entire site)
        "DUPEFILTER_CLASS": "kurdish_crawl.dupefilters.ArticleIdDupeFilter",
    }

    def __init__(self, url: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed_domains = [self.get_domain(url)]
        self.start_urls = [url]

    def get_domain(self, url):
        return urlparse(url).netloc
