"""Providers module for Jobby."""

from jobby.providers.base import JobSourceProvider
from jobby.providers.hellowork import HelloWorkProvider
from jobby.providers.jobbygather import JobbyGatherProvider
from jobby.providers.linkedin import LinkedInProvider
from jobby.providers.registry import ProviderRegistry, registry
from jobby.providers.wttj import WTTJProvider

# Register default built-in providers into global registry
registry.register(LinkedInProvider())
registry.register(HelloWorkProvider())
registry.register(WTTJProvider())
registry.register(JobbyGatherProvider())

__all__ = [
    "JobSourceProvider",
    "LinkedInProvider",
    "HelloWorkProvider",
    "WTTJProvider",
    "JobbyGatherProvider",
    "ProviderRegistry",
    "registry",
]
