"""Tests for integration settings and connection test endpoints."""

import pytest
from unittest.mock import AsyncMock, patch
import httpx

from app.main import app
from app.database.database import init_db


@pytest.mark.asyncio
async def test_integrations_status_and_antigravity_test():
    init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        # 1. Get current integration status
        resp = await client.get("/api/settings/integrations")
        assert resp.status_code == 200
        data = resp.json()
        assert "github" in data
        assert "antigravity" in data

        # 2. Test Antigravity endpoint
        ag_resp = await client.post("/api/settings/test-antigravity", json={})
        assert ag_resp.status_code == 200
        ag_data = ag_resp.json()
        assert ag_data["success"] is True
        assert "agentapi" in ag_data["binary_path"]

        # 3. Test GitHub endpoint without token (should fail gracefully)
        gh_resp = await client.post("/api/settings/test-github", json={"token": ""})
        assert gh_resp.status_code == 200
        assert gh_resp.json()["success"] is False

        # 4. Test Antigravity auth status endpoint
        auth_resp = await client.get("/api/auth/antigravity/status")
        assert auth_resp.status_code == 200
        auth_data = auth_resp.json()
        assert "authenticated" in auth_data
        assert "session_active" in auth_data
        assert "binary_exists" in auth_data

        # 5. Test GitHub device auth flow mock
        with patch("app.github.auth.GitHubAuthService.start_device_flow", new_callable=AsyncMock) as mock_start:
            mock_start.return_value = {
                "device_code": "dev123",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "interval": 5,
                "client_id": "test_cid",
            }
            dev_start_resp = await client.post("/api/auth/github/device/start")
            assert dev_start_resp.status_code == 200
            assert dev_start_resp.json()["user_code"] == "ABCD-EFGH"

        with patch("app.github.auth.GitHubAuthService.check_device_token", new_callable=AsyncMock) as mock_poll:
            mock_poll.return_value = {"error": "authorization_pending"}
            poll_resp = await client.post(
                "/api/auth/github/device/poll",
                json={"client_id": "test_cid", "device_code": "dev123"},
            )
            assert poll_resp.status_code == 200
            assert poll_resp.json()["pending"] is True

