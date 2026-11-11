"""Base class for all Job Sources / Providers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from jobby.models import JobOpportunity, SearchParams


class JobSourceProvider(ABC):
    """Abstract base class that all site scrapers / API connectors must implement."""

    name: str = "base"
    display_name: str = "Base Provider"
    supports_posts: bool = False  # True for platforms with social hiring posts (LinkedIn)
    enabled: bool = True

    @abstractmethod
    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        """Search and extract job opportunities (offers and/or posts) according to params."""
        pass

    def test_connection(self) -> bool:
        """Quick check to verify if the source endpoint or credentials are reachable."""
        return True
