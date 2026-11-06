"""Per-domain article-ID extractors for pre-fetch deduplication.

Some sites serve the same article under several URLs (reached via different
category / section paths). When the article's stable ID can be read straight from
the URL, the duplicate can be dropped *before* it is fetched — the only kind of
dedup that saves crawl time.

``DOMAIN_ID_EXTRACTORS`` maps a www-stripped host to a function that returns the
stable article ID for a URL, or ``None`` when the URL is not an article (home,
listing, pagination, …). Returning ``None`` is important: it means "do not
collapse", so distinct listing/pagination pages are never mistaken for one
another. Domains absent from the map fall back to plain URL deduplication.
"""

import re
from urllib.parse import urlsplit

# Several sites (jinnews, mezopotamyaajansi) serve articles at
# /content/view/<numeric-id>, reachable under many category prefixes
# (e.g. /GUNCEL/content/view/291456, ANALIZ/content/view/328284). The numeric id
# is the stable key; .search ignores any prefix/suffix, trailing slash or query.
_CONTENT_VIEW_ARTICLE = re.compile(r"/content/view/(\d+)")


def _content_view_id(url: str):
    match = _CONTENT_VIEW_ARTICLE.search(urlsplit(url).path)
    return match.group(1) if match else None


# channel8 serves articles at /<lang>/news/<uuid> (e.g.
# /kurmanci/news/cb5cd306-bfe8-4516-b349-9e704ffa4f37). The trailing UUID is the
# stable id. Matching the UUID specifically (not just the last path segment)
# avoids collapsing listing/pagination pages such as /english/list/programs/2.
_CHANNEL8_ARTICLE = re.compile(
    r"/news/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})"
)


def _channel8_id(url: str):
    match = _CHANNEL8_ARTICLE.search(urlsplit(url).path)
    return match.group(1).lower() if match else None


# kurdistan24 serves articles at /<lang>/story/<numeric-id>/<slug> (e.g.
# /tr/story/950975/kurdistan-...). The numeric id is the stable key; the slug
# varies and is ignored. Non-story paths (home, lists, pagination) return None.
_KURDISTAN24_ARTICLE = re.compile(r"/story/(\d+)")


def _kurdistan24_id(url: str):
    match = _KURDISTAN24_ARTICLE.search(urlsplit(url).path)
    return match.group(1) if match else None


# rudaw serves articles at /<lang>/categories/<category>/<numeric-id> (e.g.
# /kurmanci/categories/interview/1306731). The numeric id after the category is
# the stable key. Anchoring it to /categories/<cat>/ avoids matching
# category/pagination paths (e.g. /categories/interview with no id).
_RUDAW_ARTICLE = re.compile(r"/categories/[^/]+/(\d+)")


def _rudaw_id(url: str):
    match = _RUDAW_ARTICLE.search(urlsplit(url).path)
    return match.group(1) if match else None


DOMAIN_ID_EXTRACTORS = {
    "jinnews.net": _content_view_id,
    "mezopotamyaajansi.com": _content_view_id,
    "channel8.com": _channel8_id,
    "kurdistan24.net": _kurdistan24_id,
    "rudaw.net": _rudaw_id,
}


def article_id(domain: str, url: str):
    """Stable article ID for ``url`` on ``domain``, or ``None`` if not applicable."""
    extractor = DOMAIN_ID_EXTRACTORS.get(domain)
    return extractor(url) if extractor else None
