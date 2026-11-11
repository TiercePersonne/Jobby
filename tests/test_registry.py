import pytest
from typing import List
from jobby.models import JobOpportunity, OpportunityType, SearchParams
from jobby.providers.base import JobSourceProvider
from jobby.providers.registry import ProviderRegistry


class MockProvider(JobSourceProvider):
    name = "mock"
    display_name = "Mock Provider"
    supports_posts = True

    async def search(self, params: SearchParams) -> List[JobOpportunity]:
        return [
            JobOpportunity(
                source=self.name,
                title="Mock Lead Developer",
                company="Mock Corp",
                url="https://mock.com/job/1",
                opportunity_type=OpportunityType.OFFER,
            ),
            JobOpportunity(
                source=self.name,
                title="Mock Recruiter Post",
                url="https://mock.com/post/2",
                opportunity_type=OpportunityType.POST,
                author_name="Mock Recruiter",
            ),
        ]


@pytest.mark.anyio
async def test_provider_registry():
    registry = ProviderRegistry()
    registry.register(MockProvider())

    providers = registry.list_providers()
    assert len(providers) == 1
    assert providers[0]["name"] == "mock"
    assert providers[0]["supports_posts"] is True

    params = SearchParams(keywords=["lead", "developer"])
    results = await registry.search_all(params)

    assert len(results) == 2
    assert results[0].source == "mock"
    assert results[1].opportunity_type == OpportunityType.POST
