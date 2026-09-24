"""Bearer authentication on REST endpoints.

Verifies that both `Authorization: Bearer <token>` and `X-Treg-Token: <token>` work
everywhere a treg API token is accepted, and that the caller's Authorization header
never leaks to upstream providers.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from treg import crypto
from treg.api import app
from treg.config import get_settings
from treg.infra.db import reset_db, session_maker
from treg.models import Membership, Org, Tool, User, Secret

from conftest import drain_background_writes, make_upstream


async def _seed_org_and_token(email: str = "bearer-test@example.com") -> tuple[str, str, int]:
    """Create a user, org, and membership. Returns (token, slug, org_id)."""
    # Derive unique slug from email to avoid conflicts in multi-user tests
    slug = email.replace("@", "-at-").replace(".", "-")
    async with session_maker() as s:
        u = User(email=email)
        s.add(u)
        await s.flush()
        o = Org(name=f"Team for {email}", slug=slug, balance_micro=10_000_000)
        s.add(o)
        await s.flush()
        token = "test-bearer-token-" + email
        s.add(Membership(user_id=u.id, org_id=o.id, role="owner", token_hash=crypto.hash_token(token)))
        await s.commit()
        return token, o.slug, o.id


async def _seed_tool(org_id: int) -> str:
    """Create a simple test tool. Returns tool name."""
    async with session_maker() as s:
        tool = Tool(
            org_id=org_id,
            name="echo",
            base_url="http://upstream",
            host="upstream",
            bindings=[],
            owner="bearer-test@example.com",
        )
        s.add(tool)
        await s.commit()
        return tool.name


@pytest.fixture
async def bearer_client():
    """Client fixture with a fresh DB and upstream, no pre-auth."""
    await drain_background_writes()
    await reset_db()
    app.state.http = AsyncClient(transport=ASGITransport(app=make_upstream()), base_url="http://upstream")
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://registry") as c:
            yield c
    finally:
        await drain_background_writes()
        await app.state.http.aclose()


# ---- X-Treg-Token (baseline) ----------------------------------------------------------

async def test_x_treg_token_authenticates_call(bearer_client):
    """X-Treg-Token authenticates /call/* (the existing behavior)."""
    token, slug, org_id = await _seed_org_and_token()
    await _seed_tool(org_id)
    r = await bearer_client.get("/call/echo/test", headers={"X-Treg-Token": token})
    assert r.status_code == 200, r.text


async def test_x_treg_token_authenticates_tools(bearer_client):
    """X-Treg-Token authenticates /tools (member endpoint)."""
    token, slug, _ = await _seed_org_and_token()
    r = await bearer_client.get("/tools", headers={"X-Treg-Token": token})
    assert r.status_code == 200, r.text


async def test_x_treg_token_authenticates_balance(bearer_client):
    """X-Treg-Token authenticates /orgs/{org_id}/balance."""
    token, slug, org_id = await _seed_org_and_token()
    r = await bearer_client.get(f"/orgs/{org_id}/balance", headers={"X-Treg-Token": token})
    assert r.status_code == 200, r.text


# ---- Authorization: Bearer -------------------------------------------------------------

async def test_bearer_authenticates_call(bearer_client):
    """Authorization: Bearer authenticates /call/*."""
    token, slug, org_id = await _seed_org_and_token()
    await _seed_tool(org_id)
    r = await bearer_client.get("/call/echo/test", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


async def test_bearer_authenticates_tools(bearer_client):
    """Authorization: Bearer authenticates /tools (member endpoint)."""
    token, slug, _ = await _seed_org_and_token()
    r = await bearer_client.get("/tools", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


async def test_bearer_authenticates_balance(bearer_client):
    """Authorization: Bearer authenticates /orgs/{org_id}/balance."""
    token, slug, org_id = await _seed_org_and_token()
    r = await bearer_client.get(f"/orgs/{org_id}/balance", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text


async def test_bearer_case_insensitive(bearer_client):
    """Bearer scheme is case-insensitive per RFC 7235."""
    token, slug, org_id = await _seed_org_and_token()
    await _seed_tool(org_id)
    for scheme in ("Bearer", "bearer", "BEARER"):
        r = await bearer_client.get("/call/echo/test", headers={"Authorization": f"{scheme} {token}"})
        assert r.status_code == 200, f"{scheme} failed: {r.text}"


async def test_bearer_trimmed(bearer_client):
    """Bearer token is trimmed (leading/trailing whitespace)."""
    token, slug, org_id = await _seed_org_and_token()
    await _seed_tool(org_id)
    r = await bearer_client.get("/call/echo/test", headers={"Authorization": f"Bearer   {token}   "})
    assert r.status_code == 200, r.text


# ---- Both headers (X-Treg-Token wins) --------------------------------------------------

async def test_x_treg_token_wins_over_bearer(bearer_client):
    """When both headers are present, X-Treg-Token wins."""
    token_a, slug_a, org_a = await _seed_org_and_token("user-a@example.com")
    token_b, slug_b, org_b = await _seed_org_and_token("user-b@example.com")
    await _seed_tool(org_a)
    
    r = await bearer_client.get(
        "/call/echo/test",
        headers={"X-Treg-Token": token_a, "Authorization": f"Bearer {token_b}"},
    )
    assert r.status_code == 200, r.text
    # The call should use token_a's org, not token_b's


# ---- No auth / malformed auth ----------------------------------------------------------

async def test_no_auth_returns_401(bearer_client):
    """No authentication returns 401."""
    r = await bearer_client.get("/tools")
    assert r.status_code == 401
    assert r.json()["detail"] == "not authenticated"


async def test_invalid_token_returns_401(bearer_client):
    """An invalid token returns 401."""
    r = await bearer_client.get("/tools", headers={"X-Treg-Token": "invalid-token"})
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid token"


async def test_invalid_bearer_returns_401(bearer_client):
    """An invalid Bearer token returns 401."""
    r = await bearer_client.get("/tools", headers={"Authorization": "Bearer invalid-token"})
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid token"


async def test_malformed_authorization_ignored(bearer_client):
    """A malformed Authorization header (not Bearer scheme) is ignored → 401."""
    token, slug, _ = await _seed_org_and_token()
    # Basic auth, not Bearer
    r = await bearer_client.get("/tools", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 401
    assert r.json()["detail"] == "not authenticated"


async def test_empty_bearer_returns_401(bearer_client):
    """Empty Bearer token returns 401."""
    r = await bearer_client.get("/tools", headers={"Authorization": "Bearer "})
    assert r.status_code == 401
    assert r.json()["detail"] == "not authenticated"


# ---- Authorization header not leaked upstream ------------------------------------------

async def test_authorization_header_not_forwarded_upstream(bearer_client):
    """The caller's Authorization header must NOT leak to the upstream provider."""
    token, slug, org_id = await _seed_org_and_token()
    await _seed_tool(org_id)
    
    r = await bearer_client.get(
        "/call/echo/headers",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    upstream_response = r.json()
    # The upstream should NOT receive the Authorization header
    assert "authorization" not in upstream_response.get("headers", {}), (
        "Authorization header leaked to upstream"
    )


async def test_x_treg_token_not_forwarded_upstream(bearer_client):
    """X-Treg-Token header must NOT leak to the upstream provider."""
    token, slug, org_id = await _seed_org_and_token()
    await _seed_tool(org_id)
    
    r = await bearer_client.get(
        "/call/echo/headers",
        headers={"X-Treg-Token": token},
    )
    assert r.status_code == 200, r.text
    upstream_response = r.json()
    # The upstream should NOT receive the X-Treg-Token header
    assert "x-treg-token" not in upstream_response.get("headers", {}), (
        "X-Treg-Token header leaked to upstream"
    )


# ---- require_identity tests (user-level endpoints) ------------------------------------

async def test_bearer_authenticates_orgs_list(bearer_client):
    """Authorization: Bearer authenticates /orgs (identity endpoint)."""
    token, slug, _ = await _seed_org_and_token()
    r = await bearer_client.get("/orgs", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    assert any(o["slug"] == slug for o in r.json())
