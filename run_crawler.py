import logging
import threading
from pathlib import Path
from typing import Callable

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings
from twisted.python.failure import Failure

from extractor.url_extractor import UrlExtractor
from kurdish_crawl.spiders.recursive import RecursiveSpider
from kurdish_crawl.spiders.sitemap import SitemapSpider


SUPPORTED_FEED_FORMATS = {
    ".csv": "csv",
    ".json": "json",
    ".jsonl": "jsonlines",
}

logger = logging.getLogger(__name__)


def _infer_feed_format(output_path: str) -> str:
    ext = Path(output_path).suffix.lower()
    feed_format = SUPPORTED_FEED_FORMATS.get(ext)
    if not feed_format:
        supported = ", ".join(SUPPORTED_FEED_FORMATS.keys())
        raise ValueError(
            f"Unsupported output extension '{ext}'. Use one of: {supported}"
        )
    return feed_format


def run_crawler(
    output_path: str,
    urls: list[str],
    log_file: str = "logs/crawler.log",
    log_level: str = "INFO",
    max_pages: int | None = None,
    job_dir: str | None = None,
    commit_callback: Callable[[], None] | None = None,
    commit_interval: float = 120.0,
) -> None:
    feed_format = _infer_feed_format(output_path)
    settings = get_project_settings()
    if max_pages:
        # Stop each spider after this many scraped items — a quick smoke test
        # instead of a full-domain walk.
        settings.set("CLOSESPIDER_ITEMCOUNT", max_pages, priority="cmdline")
    if job_dir:
        # Persist the scheduler queue + seen-request fingerprints here, so a
        # crawl killed mid-run (timeout, preemption) resumes exactly where it
        # stopped on the next run with the same JOBDIR — nothing is re-fetched.
        # One spider per process only (Scrapy's JOBDIR is process-wide); on
        # Modal that holds because each domain gets its own container.
        settings.set("JOBDIR", job_dir, priority="cmdline")
        if len(urls) > 1:
            raise ValueError(
                "job_dir is per-spider state but multiple URLs were given; "
                "run one domain per process when using JOBDIR."
            )
    settings.set(
        "FEEDS",
        {
            output_path: {
                "format": feed_format,
                "encoding": "utf-8",
                "overwrite": False,
            }
        },
        priority="cmdline",
    )
    settings.set("LOG_ENABLED", True, priority="cmdline")
    settings.set("LOG_FILE", log_file, priority="cmdline")
    settings.set("LOG_LEVEL", log_level.upper(), priority="cmdline")

    crawler_process = CrawlerProcess(settings)
    crawl_failures: list[Failure] = []

    def _log_crawl_failure(failure: Failure) -> Failure:
        crawl_failures.append(failure)
        logger.error(
            "Crawler deferred failed: %s",
            failure.getErrorMessage(),
            exc_info=(failure.type, failure.value, failure.getTracebackObject()),
        )
        return failure

    # Hosts that must use the recursive spider even if a sitemap exists — their
    # sitemaps are partial, so sitemap-only crawling under-fetches the site.
    force_recursive = {
        d.lower().lstrip(".")
        for d in settings.getlist("FORCE_RECURSIVE_DOMAINS")
        if d
    }

    def _is_forced_recursive(target_url: str) -> bool:
        domain = UrlExtractor.get_domain(target_url)
        return any(
            domain == d or domain.endswith("." + d) for d in force_recursive
        )

    logger.info("Scheduling spiders for %d domain(s)", len(urls))
    for url in urls:
        # Crawl the whole domain: recursive if forced (partial sitemap) or if no
        # sitemap is discoverable; sitemap spider otherwise.
        sitemap_urls = None if _is_forced_recursive(url) else SitemapSpider.get_sitemap_urls(url)
        if sitemap_urls:
            logger.info(
                "Using sitemap spider for %s (%d sitemap URL candidates)",
                url,
                len(sitemap_urls),
            )
            deferred = crawler_process.crawl(SitemapSpider, sitemap_urls=sitemap_urls)
        else:
            if _is_forced_recursive(url):
                logger.info(
                    "Forcing recursive spider for %s (FORCE_RECURSIVE_DOMAINS)", url
                )
            else:
                logger.info("Using recursive spider for %s", url)
            deferred = crawler_process.crawl(RecursiveSpider, url=url)
        deferred.addErrback(_log_crawl_failure)

    # Periodically flush progress (feed shard + JOBDIR state) to durable storage
    # while the crawl runs, so a container killed between the built-in end-commits
    # (timeout, preemption) loses at most ~commit_interval seconds of work. Runs in
    # a daemon thread so it never blocks the Twisted reactor.
    stop_committing = threading.Event()
    committer: threading.Thread | None = None
    if commit_callback:
        def _periodic_commit() -> None:
            while not stop_committing.wait(commit_interval):
                try:
                    commit_callback()
                    logger.info("Periodic checkpoint commit done")
                except Exception as exc:  # never let a commit error kill the crawl
                    logger.warning("Periodic checkpoint commit failed: %s", exc)
        committer = threading.Thread(
            target=_periodic_commit, name="periodic-commit", daemon=True
        )
        committer.start()

    try:
        crawler_process.start()
    except Exception as e:
        logger.exception("Crawler process crashed: %s", e)
        raise
    finally:
        stop_committing.set()
        if committer is not None:
            committer.join(timeout=10)

    if crawl_failures:
        logger.error(
            "Crawler finished with %d deferred failure(s)", len(crawl_failures)
        )
        raise RuntimeError(
            f"Crawler finished with {len(crawl_failures)} deferred failure(s)."
        )
