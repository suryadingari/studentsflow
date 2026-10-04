"""Search provider boundary with an optional Google Programmable Search adapter."""

from typing import Protocol

import httpx
from pydantic import BaseModel, HttpUrl


class SearchResult(BaseModel):
    url: HttpUrl
    title: str
    snippet: str
    domain: str
    relevance: float
    provider: str


class SearchProvider(Protocol):
    provider_name: str

    @property
    def available(self) -> bool: ...

    async def search(self, query: str, *, permitted_domains: frozenset[str],
                     limit: int) -> list[SearchResult]: ...


class GoogleCustomSearchProvider:
    """Configured Google CSE adapter; no scraping or browser access is used."""

    provider_name = "google_custom_search"
    endpoint = "https://www.googleapis.com/customsearch/v1"

    def __init__(self, api_key: str | None, search_engine_id: str | None,
                 *, timeout_seconds: float = 8.0) -> None:
        self._api_key = api_key
        self._search_engine_id = search_engine_id
        self.timeout_seconds = timeout_seconds

    @property
    def available(self) -> bool:
        return bool(self._api_key and self._search_engine_id)

    async def search(self, query: str, *, permitted_domains: frozenset[str],
                     limit: int = 10) -> list[SearchResult]:
        if not self.available:
            raise RuntimeError("Search provider is unavailable: configure its API credentials.")
        if not permitted_domains:
            return []
        # Restrict the provider query to the same explicit domain scope as crawling.
        domain_filter = " OR ".join(f"site:{domain}" for domain in sorted(permitted_domains))
        params = {"key": self._api_key, "cx": self._search_engine_id,
                  "q": f"({domain_filter}) {query}", "num": min(max(limit, 1), 10)}
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.get(self.endpoint, params=params)
            response.raise_for_status()
            payload = response.json()
        results: list[SearchResult] = []
        for rank, item in enumerate(payload.get("items", [])[:limit]):
            url = item.get("link")
            title = item.get("title")
            if not url or not title:
                continue
            try:
                from app.crawling.policy import DomainPolicy
                _, domain = DomainPolicy.parse_url(url)
            except ValueError:
                continue
            results.append(SearchResult(url=url, title=title,
                                       snippet=str(item.get("snippet") or "")[:1000],
                                       domain=domain, relevance=1.0 / (rank + 1),
                                       provider=self.provider_name))
        return results
