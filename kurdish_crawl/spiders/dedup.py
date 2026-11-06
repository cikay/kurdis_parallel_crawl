from extractor.url_extractor import UrlExtractor


class CanonicalDedupMixin:
    """Skip pages that are the same article served under a different URL.

    Many news sites publish one article at several URLs (category, tag, date or
    print variants) but point every variant's ``<link rel="canonical">`` at the
    one true URL. This mixin reads that canonical URL and keeps a per-spider set
    of the ones already seen, so each article is stored at most once.

    This is *store-time* dedup: the page has already been fetched by the time its
    canonical URL is known, so it saves storage, not requests. Pages without a
    canonical tag are never treated as duplicates. The seen-set lives only for
    the spider's run; it does not persist across crawls.
    """

    def is_duplicate(self, response) -> bool:
        seen = self.__dict__.setdefault("_seen_canonicals", set())
        canonical = UrlExtractor.canonical_url(response)
        if not canonical:
            return False
        if canonical in seen:
            self.logger.debug(
                "Duplicate of %s (canonical %s)", response.url, canonical
            )
            return True
        seen.add(canonical)
        return False
