class CrawlPolicyError(ValueError):
    """A URL or crawl request violates local access policy."""


class InvalidCrawlURL(CrawlPolicyError):
    pass


class DisallowedCrawlDomain(CrawlPolicyError):
    pass
