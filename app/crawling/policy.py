import ipaddress
from urllib.parse import urlsplit, urlunsplit

from app.crawling.exceptions import DisallowedCrawlDomain, InvalidCrawlURL


class DomainPolicy:
    """Fail-closed HTTP(S) URL policy with exact/subdomain allowlist matching."""

    def __init__(self, allowed_domains: frozenset[str] | set[str] | tuple[str, ...]) -> None:
        self.allowed_domains = frozenset(self._normalize_domain(domain) for domain in allowed_domains)

    @staticmethod
    def _normalize_domain(domain: str) -> str:
        value = domain.strip().rstrip(".").lower()
        if not value:
            raise InvalidCrawlURL("Domain entries must not be empty")
        try:
            return value.encode("idna").decode("ascii")
        except UnicodeError as error:
            raise InvalidCrawlURL("Domain is not a valid hostname") from error

    @classmethod
    def parse_url(cls, url: str) -> tuple[str, str]:
        if not isinstance(url, str) or not url.strip():
            raise InvalidCrawlURL("URL must be a non-empty string")
        try:
            parts = urlsplit(url.strip())
            host = parts.hostname
            _ = parts.port  # Accessing port validates malformed port values.
        except ValueError as error:
            raise InvalidCrawlURL("URL is malformed") from error
        if parts.scheme.lower() not in {"http", "https"} or not host:
            raise InvalidCrawlURL("Only absolute HTTP or HTTPS URLs are allowed")
        if parts.username is not None or parts.password is not None:
            raise InvalidCrawlURL("URLs containing embedded credentials are not allowed")
        domain = cls._normalize_domain(host)
        if domain == "localhost" or domain.endswith((".localhost", ".local", ".internal", ".lan")):
            raise DisallowedCrawlDomain("Local or private hostnames are not permitted")
        try:
            ip = ipaddress.ip_address(domain)
        except ValueError:
            pass
        else:
            if not ip.is_global:
                raise DisallowedCrawlDomain("Non-public IP addresses are not permitted")
        normalized_url = urlunsplit((parts.scheme.lower(), parts.netloc, parts.path or "/", parts.query, ""))
        return normalized_url, domain

    def validate(self, url: str) -> tuple[str, str]:
        normalized_url, domain = self.parse_url(url)
        allowed = any(domain == item or domain.endswith(f".{item}") for item in self.allowed_domains)
        if not allowed:
            raise DisallowedCrawlDomain(f"Domain is not allowlisted: {domain}")
        return normalized_url, domain


def intersect_domains(*domain_sets: frozenset[str] | set[str] | tuple[str, ...]) -> frozenset[str]:
    """Return the narrowest domains allowed by every supplied allowlist."""
    if not domain_sets:
        return frozenset()
    normalized = [DomainPolicy(values).allowed_domains for values in domain_sets]
    effective = normalized[0]
    for other in normalized[1:]:
        overlap: set[str] = set()
        for left in effective:
            for right in other:
                if left == right or left.endswith(f".{right}"):
                    overlap.add(left)
                elif right.endswith(f".{left}"):
                    overlap.add(right)
        effective = frozenset(overlap)
    return frozenset(effective)
