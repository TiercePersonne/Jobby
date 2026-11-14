"""Registry for extensible Job Providers."""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Type

from jobby.models import JobOpportunity, SearchParams
from jobby.providers.base import JobSourceProvider
from jobby.providers.relevance import (
    record_provider_audit_result,
    reset_current_audit_source,
    reset_search_audit,
    set_current_audit_source,
)

logger = logging.getLogger(__name__)


class ProviderRegistry:
    """Registry maintaining active and pluggable providers."""

    def __init__(self) -> None:
        self._providers: Dict[str, JobSourceProvider] = {}

    def register(self, provider: JobSourceProvider) -> None:
        """Register a provider instance."""
        self._providers[provider.name.lower()] = provider
        logger.debug(f"Registered provider: {provider.name} ({provider.display_name})")

    def register_class(self, provider_cls: Type[JobSourceProvider], *args, **kwargs) -> None:
        """Instantiate and register a provider class."""
        instance = provider_cls(*args, **kwargs)
        self.register(instance)

    def get(self, name: str) -> Optional[JobSourceProvider]:
        return self._providers.get(name.lower())

    def list_providers(self) -> List[Dict[str, str | bool]]:
        return [
            {
                "name": p.name,
                "display_name": p.display_name,
                "supports_posts": p.supports_posts,
                "enabled": p.enabled,
            }
            for p in self._providers.values()
        ]

    async def search_all(
        self,
        params: SearchParams,
        source_names: Optional[List[str]] = None,
        fail_silently: bool = True
    ) -> List[JobOpportunity]:
        """
        Execute searches across selected providers concurrently.
        """
        selected_providers: List[JobSourceProvider] = []
        if source_names:
            normalized = [n.lower() for n in source_names]
            for name in normalized:
                p = self.get(name)
                if p and p.enabled:
                    selected_providers.append(p)
                elif not p:
                    logger.warning(f"Requested provider '{name}' is not registered.")
        else:
            selected_providers = [p for p in self._providers.values() if p.enabled]

        if not selected_providers:
            logger.warning("No providers available for search.")
            return []

        reset_search_audit([p.name for p in selected_providers])
        tasks = [self._safe_search(provider, params, fail_silently) for provider in selected_providers]
        results = await asyncio.gather(*tasks)

        # Flatten list of lists
        all_opportunities: List[JobOpportunity] = []
        for provider_results in results:
            all_opportunities.extend(provider_results)

        return all_opportunities

    async def _safe_search(
        self,
        provider: JobSourceProvider,
        params: SearchParams,
        fail_silently: bool
    ) -> List[JobOpportunity]:
        token = set_current_audit_source(provider.name)
        try:
            res = await provider.search(params)
            record_provider_audit_result(provider.name, len(res), status="ok")
            return res
        except Exception as e:
            logger.error(f"Error querying provider {provider.name}: {e}")
            record_provider_audit_result(provider.name, 0, status="error", note=str(e)[:120])
            if not fail_silently:
                raise
            return []
        finally:
            reset_current_audit_source(token)


# Global singleton instance
registry = ProviderRegistry()
