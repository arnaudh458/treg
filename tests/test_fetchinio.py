"""Fetchin API-key connection, curated catalog and shared-account capacity policy."""

import math

import httpx
import pytest
from httpx import AsyncClient

from treg.api import app
from treg.config import Settings, get_settings
from treg.domain.catalog import store
from treg.domain.capacity import collectors, policy
from treg.oauth_providers import get, platform_bindings


@pytest.fixture
def fetchin_on(monkeypatch):
    monkeypatch.setenv("TREG_PLATFORM_KEY_FETCHINIO", "PLATFORM-FETCHIN")
    monkeypatch.setenv("TREG_PLATFORM_PROVIDERS", "fetchinio")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_fetchin_catalog_is_bounded_and_platform_eligible(fetchin_on):
    catalog = store.load()
    rows = [ep for ep in catalog.endpoints if ep["provider"] == "fetchinio"]
    assert len(rows) == 7
    assert {ep["path"] for ep in rows} == {
        "/api/v1/profile", "/api/v1/company", "/api/v1/posts",
        "/api/v1/profile/reactions", "/api/v1/post/comments",
        "/api/v1/post/reactions", "/api/v1/post/engagement",
    }
    for ep in rows:
        assert ep["method"] == "GET"
        assert ep["strict_query"] is True
        assert catalog.platform_eligible(ep)
        assert ep["cost"]["type"] == "per_call"
        assert ep["cost"]["confidence"] == "verified"
        expected = 0.003 if ep["id"].endswith("post.engagement") else 0.0015
        assert catalog.cost_view(ep["cost"], "fetchinio")["usd"] == expected
    profile = catalog.by_id["fetchinio.linkedin.user.profile"]
    assert "fullProfile" not in profile["input"]["queryParams"]


def test_fetchin_registry_and_platform_binding(fetchin_on):
    provider = get("fetchinio")
    assert provider.base_url == "https://api.fetchin.io"
    assert provider.probe_path == "/api/v1/subscription"
    assert provider.token_header == "X-API-Key"
    assert Settings(_env_file=None).platform_key_for("fetchinio") == "PLATFORM-FETCHIN"
    assert platform_bindings(provider) == [{
        "platform_setting": "platform_key_fetchinio",
        "injector": "env",
        "location": "header",
        "name": "X-API-Key",
        "format": "{secret}",
    }]


async def test_fetchin_connect_uses_the_free_subscription_probe(clients, monkeypatch):
    def probe(request):
        assert request.url.path == "/api/v1/subscription"
        assert request.method == "GET"
        if request.headers["x-api-key"] == "bad":
            return httpx.Response(401, json={"code": "INVALID_API_KEY"})
        return httpx.Response(200, json={
            "plan": "free", "active": True, "status": "free",
            "creditsRemaining": 0, "creditsLimit": 1000, "creditsUsed": 1000,
            "renewalDate": None, "rpsLimit": 5, "cancelAtPeriodEnd": False,
        })

    async with AsyncClient(transport=httpx.MockTransport(probe)) as upstream:
        monkeypatch.setattr(app.state, "http", upstream)
        bad = await clients.post(
            "/connections/token", json={"provider": "fetchinio", "token": "bad"})
        assert bad.status_code == 422
        good = await clients.post(
            "/connections/token", json={"provider": "fetchinio", "token": "own-key"})
        assert good.status_code == 200, good.text

    tools = {tool["name"]: tool for tool in (await clients.get("/tools")).json()}
    assert set(tools) == {"fetchinio"}
    assert tools["fetchinio"]["base_url"] == "https://api.fetchin.io"
    assert tools["fetchinio"]["bindings"][0]["name"] == "X-API-Key"


async def test_fetchin_capacity_collector_and_conservative_rate_policy():
    def serve(request):
        assert request.url.path == "/api/v1/subscription"
        assert request.headers["x-api-key"] == "test-key"
        return httpx.Response(200, json={
            "plan": "free", "status": "free", "creditsRemaining": 51_000,
            "paygCreditsRemaining": 50_000, "renewalDate": None, "rpsLimit": 5,
        })

    async with AsyncClient(transport=httpx.MockTransport(serve)) as upstream:
        row = await collectors._fetchinio(upstream, "test-key")
    assert row["value"] == 51_000
    assert row["unit"] == "credits"
    assert "account limit 5 requests/s" in row["note"]

    capacity = policy.default_policy("fetchinio", has_key=True)
    assert capacity.capacity_type == "credits"
    assert capacity.funding_mode == "manual"
    assert capacity.source == "api"
    assert capacity.rate_limit == {"limit": 2, "window_s": 1, "source": "policy"}


@pytest.mark.parametrize("remaining", [None, True, -1, "51000", float("nan")])
async def test_fetchin_capacity_rejects_uncertain_balances(remaining):
    def serve(_request):
        if isinstance(remaining, float) and math.isnan(remaining):
            return httpx.Response(200, content=b'{"creditsRemaining": NaN}')
        return httpx.Response(200, json={"creditsRemaining": remaining})

    async with AsyncClient(transport=httpx.MockTransport(serve)) as upstream:
        with pytest.raises(ValueError, match="remaining-credit"):
            await collectors._fetchinio(upstream, "test-key")
