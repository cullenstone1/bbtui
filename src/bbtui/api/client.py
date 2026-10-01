"""Async HTTP transport for the Bitbucket Cloud REST API v2."""

from typing import Any

import httpx

from bbtui.config import DEFAULT_BASE_URL


class BitbucketError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class AuthenticationError(BitbucketError):
    pass


class PermissionDeniedError(BitbucketError):
    pass


class NotFoundError(BitbucketError):
    pass


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:200] or response.reason_phrase
    error = body.get('error') if isinstance(body, dict) else None
    if isinstance(error, dict):
        return error.get('message') or error.get('detail') or response.reason_phrase
    return response.reason_phrase


def raise_for_status(response: httpx.Response) -> None:
    if response.is_success:
        return
    message = _error_message(response)
    status = response.status_code
    if status == 401:
        raise AuthenticationError(
            f'Authentication failed ({message}). bbtui needs your Atlassian email and a scoped '
            'API token with Bitbucket scopes.',
            status,
        )
    if status == 403:
        raise PermissionDeniedError(f'Permission denied: {message}', status)
    if status == 404:
        raise NotFoundError(f'Not found: {response.request.url.path}', status)
    raise BitbucketError(f'Bitbucket returned {status}: {message}', status)


class BitbucketClient:
    """Thin wrapper over `httpx.AsyncClient` that knows Bitbucket's auth, errors and paging."""

    def __init__(
        self,
        username: str,
        api_token: str,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip('/'),
            auth=httpx.BasicAuth(username, api_token),
            headers={'Accept': 'application/json'},
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> dict:
        response = await self._http.get(path, params=params)
        raise_for_status(response)
        return response.json()

    async def get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        response = await self._http.get(path, params=params, headers={'Accept': 'text/plain'})
        raise_for_status(response)
        return response.text

    async def get_all(
        self, path: str, params: dict[str, Any] | None = None, limit: int | None = None
    ) -> list[dict]:
        """Every `values` entry of a paginated collection, following `next` links.

        Stops early once `limit` entries have been collected.
        """
        values: list[dict] = []
        url: str | None = path
        while url:
            # `next` links already carry the query string, so params only go on the first request.
            page = await self.get_json(url, params if url == path else None)
            values.extend(page.get('values') or [])
            if limit is not None and len(values) >= limit:
                return values[:limit]
            url = page.get('next')
        return values
