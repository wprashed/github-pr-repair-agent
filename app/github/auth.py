"""GitHub OAuth & Device Flow authentication service."""

import logging
from pathlib import Path
import shutil
from typing import Any, Dict, Optional
import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)


class GitHubAuthService:
    """Manages GitHub OAuth and Device Authorization (RFC 8628) flows."""

    DEVICE_CODE_URL = "https://github.com/login/device/code"
    ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
    OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"

    @classmethod
    async def start_device_flow(
        cls,
        client_id: Optional[str] = None,
        scope: str = "repo,read:user",
    ) -> Dict[str, Any]:
        """Initiate GitHub Device Authorization flow.

        Returns device_code, user_code, verification_uri, interval, and expires_in.
        """
        cid = client_id or settings.GITHUB_CLIENT_ID
        if not cid:
            # Publicly known client ID for GitHub CLI device auth
            cid = "178c6fc778ccc68e1d6a"

        payload = {"client_id": cid, "scope": scope}
        headers = {"Accept": "application/json"}
        timeout = httpx.Timeout(20.0, connect=10.0)

        # Retry up to 2 attempts for transient connection/DNS hiccups
        last_error = ""
        for attempt in range(1, 3):
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(cls.DEVICE_CODE_URL, data=payload, headers=headers)
                    if resp.status_code != 200:
                        logger.error("GitHub Device Flow init failed [%d]: %s", resp.status_code, resp.text)
                        return {
                            "error": f"Failed to initiate device flow: {resp.text}",
                            "client_id": cid,
                        }
                    data = resp.json()
                    data["client_id"] = cid
                    return data
            except httpx.TimeoutException as exc:
                last_error = f"Connection timed out contacting GitHub (attempt {attempt}/2)"
                logger.warning("GitHub Device start_device_flow timeout: %s", exc)
            except Exception as exc:
                last_error = f"Network error contacting GitHub: {exc}"
                logger.warning("GitHub Device start_device_flow error (attempt %d/2): %s", attempt, exc)

        return {
            "error": last_error or "Unable to reach GitHub Device Code endpoint.",
            "client_id": cid,
        }

    @classmethod
    async def check_device_token(
        cls,
        client_id: str,
        device_code: str,
        client_secret: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Poll GitHub to see if user has authorized the device code.

        Returns either access_token or authorization_pending/slow_down/expired_token.
        """
        payload = {
            "client_id": client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        if client_secret:
            payload["client_secret"] = client_secret

        headers = {"Accept": "application/json"}
        timeout = httpx.Timeout(20.0, connect=10.0)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(cls.ACCESS_TOKEN_URL, data=payload, headers=headers)
                logger.info("GitHub Device check_device_token response [%d]: %s", resp.status_code, resp.text)
                if resp.status_code != 200:
                    return {"error": f"HTTP {resp.status_code}: {resp.text}"}
                return resp.json()
        except httpx.TimeoutException:
            logger.warning("GitHub Device check_device_token timed out connecting to %s", cls.ACCESS_TOKEN_URL)
            return {"error": "authorization_pending", "error_description": "Connection timed out, retrying...", "interval": 5}
        except Exception as exc:
            logger.error("GitHub Device check_device_token exception: %s", exc)
            return {"error": "request_failed", "error_description": str(exc), "interval": 5}

    @classmethod
    async def exchange_oauth_code(
        cls,
        code: str,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Exchange OAuth authorization code for access token in web flow."""
        cid = client_id or settings.GITHUB_CLIENT_ID
        sec = client_secret or settings.GITHUB_CLIENT_SECRET

        if not (cid and sec):
            return {"error": "GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET are required for OAuth code exchange."}

        payload = {
            "client_id": cid,
            "client_secret": sec,
            "code": code,
        }
        if redirect_uri:
            payload["redirect_uri"] = redirect_uri

        headers = {"Accept": "application/json"}

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(cls.ACCESS_TOKEN_URL, data=payload, headers=headers)
            return resp.json()


class AntigravityAuthService:
    """Inspects and verifies local Antigravity session authentication."""

    @classmethod
    def get_auth_status(cls) -> Dict[str, Any]:
        """Check active Antigravity session, OAuth token file, and binary readiness."""
        oauth_token_path = Path.home() / ".gemini" / "jetski-standalone-oauth-token"
        state_path = Path.home() / ".gemini" / "antigravity" / "antigravity_state.pbtxt"
        bin_path = settings.ANTIGRAVITY_BIN_PATH

        has_token = oauth_token_path.exists() and oauth_token_path.stat().st_size > 0
        has_state = state_path.exists()

        resolved_bin = None
        if shutil.which(bin_path) or Path(bin_path).is_file():
            resolved_bin = bin_path
        elif shutil.which("agentapi"):
            resolved_bin = shutil.which("agentapi")

        bin_exists = bool(resolved_bin)
        is_authenticated = has_token or bin_exists

        return {
            "authenticated": is_authenticated,
            "session_active": has_token,
            "has_state": has_state,
            "binary_exists": bin_exists,
            "binary_path": resolved_bin,
            "model": settings.ANTIGRAVITY_MODEL,
            "provider": settings.AGENT_PROVIDER,
            "summary": (
                "Authenticated Antigravity session and agentapi binary detected."
                if (has_token and bin_exists)
                else ("Antigravity agentapi binary ready." if bin_exists else "Antigravity binary not found.")
            ),
        }
