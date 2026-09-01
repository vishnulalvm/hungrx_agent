"""Known aggregator / third-party / social-platform domains that must
never be accepted as a restaurant's *official* website, even when a
provider returns them with high confidence (a Yelp listing is often the
top search result for a small restaurant, but it is not the restaurant's
own site).

Deliberately a flat, explicit set rather than a heuristic (e.g. "contains
'menu' in a known aggregator's brand name") — false negatives here (an
aggregator slipping through) are worse than the maintenance cost of
occasionally adding a new one.
"""

KNOWN_AGGREGATOR_DOMAINS: frozenset[str] = frozenset(
    {
        # Review / discovery platforms
        "yelp.com",
        "tripadvisor.com",
        "opentable.com",
        "zomato.com",
        "foursquare.com",
        "yellowpages.com",
        # Delivery marketplaces
        "doordash.com",
        "ubereats.com",
        "grubhub.com",
        "postmates.com",
        "seamless.com",
        "menufy.com",
        "slicelife.com",
        "chownow.com",
        "toasttab.com",
        # Maps / general directories
        "google.com",
        "maps.google.com",
        "bing.com",
        "mapquest.com",
        # Social platforms (a restaurant's Facebook/Instagram page is not
        # its official website even when it's the only web presence found)
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "linkedin.com",
        "tiktok.com",
        # Link-in-bio aggregators sometimes surfacing as a "website"
        "linktr.ee",
        "linktree.com",
    }
)


def is_known_aggregator(domain: str) -> bool:
    """True if `domain` (or any parent domain of it, e.g.
    "order.doordash.com" -> "doordash.com") is a known aggregator."""
    domain = domain.lower()
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        candidate = ".".join(parts[i:])
        if candidate in KNOWN_AGGREGATOR_DOMAINS:
            return True
    return False
