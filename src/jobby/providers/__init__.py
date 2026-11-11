"""Providers module for Jobby."""

from jobby.providers.base import JobSourceProvider
from jobby.providers.linkedin import LinkedInProvider
from jobby.providers.registry import ProviderRegistry, registry

# Register default built-in providers
registry.register(LinkedInProvider())

__all__ = ["JobSourceProvider", "LinkedInProvider", "ProviderRegistry", "registry"]
