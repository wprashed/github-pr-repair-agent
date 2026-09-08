"""GitHub API client with async httpx, authentication, and rate limiting."""

import asyncio
import logging
from typing import Any, Dict, List, Optional
import httpx

from app.config.settings import settings

logger = logging.getLogger(__name__)


class GitHubAPIError(Exception):
    """Base exception for GitHub API errors."""

    def __init__(self, status_code: int, message: str, response_data: Optional[Any] = None):
        super().__init__(f"GitHub API Error [{status_code}]: {message}")
        self.status_code = status_code
        self.response_data = response_data


class GitHubClient:
    """Async GitHub REST API Client."""

    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None):
        self.token = token or settings.GITHUB_TOKEN
        self.base_url = (base_url or settings.GITHUB_API_URL).rstrip("/")
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Autonomous-GitHub-PR-Repair-Agent/0.1.0",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=30.0,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def _request(self, method: str, path: str, **kwargs) -> Any:
        """Internal helper with rate-limit tracking and retries."""
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        max_retries = 3

        for attempt in range(max_retries):
            try:
                response = await self._client.request(method, url, **kwargs)

                # Check rate limit headers
                remaining = response.headers.get("x-ratelimit-remaining")
                if remaining and int(remaining) <= 1:
                    logger.warning("GitHub API rate limit nearly exhausted: %s remaining", remaining)

                if response.status_code == 403 and "rate limit" in response.text.lower():
                    reset_time = int(response.headers.get("x-ratelimit-reset", 0))
                    logger.error("GitHub API rate limit exceeded. Reset at %s", reset_time)
                    raise GitHubAPIError(403, "Rate limit exceeded", response.json())

                if response.status_code >= 400:
                    try:
                        err_json = response.json()
                        msg = err_json.get("message", response.text)
                    except Exception:
                        msg = response.text
                    raise GitHubAPIError(response.status_code, msg, response.text)

                if response.status_code == 204:
                    return None
                return response.json()

            except (httpx.ConnectError, httpx.TimeoutException) as exc:
                if attempt == max_retries - 1:
                    raise GitHubAPIError(0, f"Network error after {max_retries} attempts: {exc}")
                await asyncio.sleep(2 ** attempt)

    async def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(self, path: str, json_data: Optional[Dict[str, Any]] = None) -> Any:
        return await self._request("POST", path, json=json_data)

    async def get_raw_text(self, path: str) -> str:
        """Get raw text, e.g. for logs or diffs."""
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        headers = dict(self._client.headers)
        headers["Accept"] = "application/vnd.github.v3.diff"
        resp = await self._client.get(url, headers=headers)
        if resp.status_code >= 400:
            raise GitHubAPIError(resp.status_code, resp.text)
        return resp.text

    async def verify_auth(self) -> Dict[str, Any]:
        """Verify token and return current authenticated user data."""
        return await self.get("/user")
