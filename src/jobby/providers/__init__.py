"""Providers module for Jobby."""

from jobby.providers.base import JobSourceProvider
from jobby.providers.glassdoor import GlassdoorProvider
from jobby.providers.hellowork import HelloWorkProvider
from jobby.providers.indeed import IndeedProvider
from jobby.providers.jobgether import JobgetherProvider
from jobby.providers.jobbygather import JobbyGatherProvider
from jobby.providers.linkedin import LinkedInProvider
from jobby.providers.registry import ProviderRegistry, registry
from jobby.providers.teamtailor import TeamtailorProvider
from jobby.providers.wttj import WTTJProvider

# Register default built-in providers into global registry
registry.register(LinkedInProvider())
registry.register(WTTJProvider())
registry.register(HelloWorkProvider())
registry.register(IndeedProvider())
registry.register(GlassdoorProvider())
registry.register(TeamtailorProvider())
registry.register(JobgetherProvider())
registry.register(JobbyGatherProvider())

__all__ = [
    "JobSourceProvider",
    "LinkedInProvider",
    "WTTJProvider",
    "HelloWorkProvider",
    "IndeedProvider",
    "GlassdoorProvider",
    "TeamtailorProvider",
    "JobgetherProvider",
    "JobbyGatherProvider",
    "ProviderRegistry",
    "registry",
]
