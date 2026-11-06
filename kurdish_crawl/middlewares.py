# Downloader middlewares.
#
# See: https://docs.scrapy.org/en/latest/topics/downloader-middleware.html

from random import randint
from urllib.parse import urlencode

import requests
from scrapy.exceptions import IgnoreRequest

from extractor.url_extractor import UrlExtractor


class MediaFilterMiddleware:
    """Refuse requests for media / non-HTML URLs before they are downloaded."""

    def process_request(self, request, spider):
        domain = UrlExtractor.get_domain(request.url)
        if not UrlExtractor.should_request(request.url, domain):
            raise IgnoreRequest


class RobotsExemptMiddleware:
    """Ignore ``robots.txt`` for an explicit allowlist of domains only.

    ``ROBOTSTXT_OBEY`` stays ``True`` globally, so every domain is polite by
    default. For hosts in ``ROBOTSTXT_EXEMPT_DOMAINS`` this sets the per-request
    ``dont_obey_robotstxt`` meta flag, which Scrapy's ``RobotsTxtMiddleware``
    checks and, when set, skips the robots lookup for that request. Must run
    *before* ``RobotsTxtMiddleware`` (default order 100), so register it lower.
    """

    def __init__(self, exempt_domains):
        # www-stripped hosts, matching UrlExtractor.get_domain output.
        self.exempt_domains = {d.lower().lstrip(".") for d in exempt_domains if d}

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings.getlist("ROBOTSTXT_EXEMPT_DOMAINS"))

    def _is_exempt(self, domain: str) -> bool:
        return any(
            domain == d or domain.endswith("." + d) for d in self.exempt_domains
        )

    def process_request(self, request, spider):
        if not self.exempt_domains:
            return
        domain = UrlExtractor.get_domain(request.url)
        if self._is_exempt(domain):
            request.meta["dont_obey_robotstxt"] = True


class ScrapeOpsFakeUserAgentMiddleware:
    """Rotate real-looking User-Agent headers via ScrapeOps.

    The ScrapeOps user-agents endpoint returns a list of real browser UAs even
    with no API key, so rotation is gated only on ``SCRAPEOPS_FAKE_USER_AGENT_ENABLED``.
    A ``SCRAPEOPS_API_KEY``, when set, is still sent (larger/better lists). If the
    endpoint can't be reached, rotation self-disables and the crawler falls back
    to the default ``USER_AGENT``. Remove it from ``DOWNLOADER_MIDDLEWARES`` if it
    ever blocks runs.
    """

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def __init__(self, settings):
        self.scrapeops_api_key = settings.get("SCRAPEOPS_API_KEY")
        self.scrapeops_endpoint = settings.get(
            "SCRAPEOPS_FAKE_USER_AGENT_ENDPOINT",
            "https://headers.scrapeops.io/v1/user-agents?",
        )
        self.scrapeops_fake_user_agents_active = settings.get(
            "SCRAPEOPS_FAKE_USER_AGENT_ENABLED", False
        )
        self.scrapeops_num_results = settings.get("SCRAPEOPS_NUM_RESULTS")
        self.user_agents_list = []
        self._scrapeops_fake_user_agents_enabled()
        if self.scrapeops_fake_user_agents_active:
            self._get_user_agents_list()

    def _get_user_agents_list(self):
        payload = {}
        if self.scrapeops_api_key:  # optional — the endpoint works without a key
            payload["api_key"] = self.scrapeops_api_key
        if self.scrapeops_num_results is not None:
            payload["num_results"] = self.scrapeops_num_results
        try:
            response = requests.get(
                self.scrapeops_endpoint, params=urlencode(payload), timeout=10
            )
            self.user_agents_list = response.json().get("result", [])
        except Exception:
            self.user_agents_list = []
        if not self.user_agents_list:
            self.scrapeops_fake_user_agents_active = False

    def _get_random_user_agent(self):
        return self.user_agents_list[randint(0, len(self.user_agents_list) - 1)]

    def _scrapeops_fake_user_agents_enabled(self):
        # No API key required — the endpoint serves UAs anonymously.
        self.scrapeops_fake_user_agents_active = bool(
            self.scrapeops_fake_user_agents_active
        )

    def process_request(self, request, spider):
        if self.scrapeops_fake_user_agents_active and self.user_agents_list:
            request.headers["User-Agent"] = self._get_random_user_agent()
