import json
import logging
import os
import tempfile

from scrapy import signals
from scrapy.dupefilters import RFPDupeFilter

from extractor.article_id import article_id
from extractor.url_extractor import UrlExtractor

logger = logging.getLogger(__name__)


def _item_url(item) -> str | None:
    if isinstance(item, dict):
        return item.get("url")
    return getattr(item, "url", None)


class ArticleIdDupeFilter(RFPDupeFilter):
    """Scrapy's URL-fingerprint dedup, plus per-domain article-ID dedup.

    For domains in ``DOMAIN_ID_EXTRACTORS`` the same article reached under several
    paths shares one ID, so the duplicate request is dropped *before* it is sent —
    saving the fetch, not just the storage. Other domains and non-article URLs
    fall back to the default URL fingerprinting unchanged.

    Two ID sets, deliberately separate:

    * **stored** (persisted) — IDs of articles actually *stored*, recorded on the
      ``item_scraped`` signal. Saved beside the output shards as
      ``<host>_seen_ids.json`` and loaded on first use. A request for an article
      already stored in an earlier run is skipped, so repeat crawls are
      incremental. Only *stored* IDs are persisted, so an article merely enqueued
      but never fetched (e.g. a capped or interrupted run) is **not** marked done
      and will be fetched next time.

    * **enqueued** (in-memory, per run) — IDs already scheduled this run, so the
      same article linked from many listing pages is fetched once. Not persisted.

    URL fingerprints are not persisted either, so listing/pagination pages are
    re-fetched each run to discover new articles.
    """

    @classmethod
    def from_crawler(cls, crawler):
        instance = super().from_crawler(crawler)
        instance._persist_dir = cls._feed_dir(crawler.settings)
        crawler.signals.connect(instance.item_scraped, signal=signals.item_scraped)
        return instance

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._persist_dir = None
        self._stored_by_domain: dict[str, set] = {}   # persisted: actually stored
        self._loaded_domains: set = set()
        self._enqueued_by_domain: dict[str, set] = {}  # in-memory: scheduled this run
        self._dirty_domains: set = set()

    def request_seen(self, request) -> bool:
        # Default URL-fingerprint check first (and it records the fingerprint).
        if super().request_seen(request):
            return True
        domain = UrlExtractor.get_domain(request.url)
        aid = article_id(domain, request.url)
        if aid is None:
            return False
        if aid in self._stored_for(domain):
            # Stored in an earlier run (or already this run) — skip the fetch.
            logger.debug("Skipping already-stored article %s/%s: %s", domain, aid, request.url)
            return True
        enqueued = self._enqueued_by_domain.setdefault(domain, set())
        if aid in enqueued:
            logger.debug("Dropping duplicate enqueue %s/%s: %s", domain, aid, request.url)
            return True
        enqueued.add(aid)
        return False

    def item_scraped(self, item, response, spider) -> None:
        """Record an article's ID only once it has actually been stored."""
        url = _item_url(item)
        if not url:
            return
        domain = UrlExtractor.get_domain(url)
        aid = article_id(domain, url)
        if aid is None:
            return
        stored = self._stored_for(domain)
        if aid not in stored:
            stored.add(aid)
            self._dirty_domains.add(domain)

    def close(self, reason) -> None:
        super().close(reason)
        self._persist()

    # -- persistence helpers --------------------------------------------------

    @staticmethod
    def _feed_dir(settings):
        """Directory of the first file-based feed, where seen-ID files are kept."""
        for uri in (settings.getdict("FEEDS") or {}):
            path = uri[len("file://"):] if uri.startswith("file://") else uri
            parent = os.path.dirname(path)
            if parent and "://" not in path:  # skip remote feeds (s3://, gs://, …)
                return parent
        return None

    def _stored_for(self, domain: str) -> set:
        """Persisted set of stored IDs for ``domain``, loaded on first access."""
        if domain not in self._loaded_domains:
            self._stored_by_domain[domain] = self._load(domain)
            self._loaded_domains.add(domain)
        return self._stored_by_domain[domain]

    def _path(self, domain: str) -> str:
        safe = domain.replace("/", "_").replace(":", "_") or "domain"
        return os.path.join(self._persist_dir, f"{safe}_seen_ids.json")

    def _load(self, domain: str) -> set:
        if not self._persist_dir:
            return set()
        path = self._path(domain)
        try:
            with open(path, encoding="utf-8") as fh:
                ids = {str(x) for x in json.load(fh)}
            logger.info("Loaded %d stored article id(s) for %s from %s", len(ids), domain, path)
            return ids
        except FileNotFoundError:
            return set()
        except (ValueError, OSError) as exc:
            logger.warning("Could not read seen-id file %s (%s); starting empty", path, exc)
            return set()

    def _persist(self) -> None:
        if not self._persist_dir:
            return
        for domain in self._dirty_domains:
            ids = self._stored_by_domain[domain]
            path = self._path(domain)
            try:
                os.makedirs(self._persist_dir, exist_ok=True)
                # Write to a temp file then atomically replace, so a killed
                # container can't leave a half-written (corrupt) JSON file.
                fd, tmp = tempfile.mkstemp(dir=self._persist_dir, suffix=".tmp")
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(sorted(ids), fh)
                os.replace(tmp, path)
                logger.info("Saved %d stored article id(s) for %s to %s", len(ids), domain, path)
            except OSError as exc:
                logger.warning("Could not save seen-id file %s (%s)", path, exc)
