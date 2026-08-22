"""Meta's official Instagram Login OAuth flow.

**Which API this is, and why.** Instagram's *Basic Display API* — the one most tutorials
still show, with `user_profile`/`user_media` scopes — was shut down by Meta on
2024-12-04. It is gone; code written against it cannot work. The two supported successors
are:

* **Instagram API with Facebook Login** — for a Business account that is linked to a
  Facebook Page, authorised through `facebook.com/dialog/oauth`. Requires the user to
  have a Page and to grant Page permissions.
* **Instagram API with Instagram Login** — the user authorises with their Instagram
  credentials directly, no Facebook Page involved. Supports Business and Creator
  accounts.

This module implements the second. For a self-audit — "show me what I am exposing" — the
Instagram Login flow is the right one: it asks the person for the minimum, does not drag
a Facebook Page into it, and the token it returns reads *that user's own account only*.

**What that means for the product.** A token obtained here cannot read arbitrary
Instagram users. It reads `/me`. Anything this feature says about other accounts comes
from the platform's ordinary public-source providers, and is labelled as such.

**Token lifecycle.** The code exchange returns a short-lived token (~1 hour), which is
immediately exchanged for a long-lived one (~60 days). Long-lived tokens can be refreshed
once they are at least 24 hours old, extending them another 60 days.

**Revocation.** Instagram Login exposes no server-side token revocation endpoint (unlike
Facebook Login's `DELETE /{user-id}/permissions`). Disconnecting therefore deletes the
credential locally and tells the user where to withdraw the app's access on Meta's side.
This module does not pretend to revoke something it cannot; see
:func:`revocation_instructions`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from app.core.config import Settings, get_settings
from app.security.crypto import Secret
from app.security.ssrf import SafeAsyncClient

logger = logging.getLogger(__name__)

#: Where a person withdraws an app's access, since the API offers no revoke call.
REVOCATION_URL = "https://www.instagram.com/accounts/manage_access/"

#: Fields the Instagram Login API documents on `/me`. Kept explicit so a reader can see
#: exactly what is requested — this is the whole surface of what we ask Meta for.
ME_FIELDS = (
    "user_id,username,name,account_type,profile_picture_url,"
    "followers_count,follows_count,media_count,biography,website"
)
MEDIA_FIELDS = (
    "id,caption,media_type,media_url,permalink,thumbnail_url,timestamp,"
    "like_count,comments_count,username"
)
COMMENT_FIELDS = "id,text,timestamp,username,like_count"


class InstagramOAuthError(Exception):
    """A flow step failed. The message is safe to show a user; it never quotes a token."""


class InstagramNotConfigured(InstagramOAuthError):
    """The operator has not supplied Meta app credentials."""


class InstagramAuthRejected(InstagramOAuthError):
    """Meta rejected the credential — the user must authorise again."""


@dataclass
class TokenGrant:
    """The result of a completed exchange. ``token`` never leaves this process."""

    token: Secret
    account_id: str
    scopes: list[str]
    expires_at: datetime | None

    def redacted(self) -> dict[str, Any]:
        """A logging-safe view. Deliberately has no field that could carry the token."""
        return {
            "account_id": self.account_id,
            "scopes": self.scopes,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


def revocation_instructions() -> str:
    return (
        "Instagram Login does not provide an API to revoke a token from the server side. "
        f"To withdraw this application's access on Meta's side, visit {REVOCATION_URL} "
        "(Instagram: Settings -> Apps and websites) and remove it. Disconnecting here "
        "deletes the stored credential immediately, so this application stops using it "
        "either way."
    )


class InstagramOAuthClient:
    """Thin, auditable wrapper over the documented Instagram Login endpoints.

    Every outbound call goes through :class:`SafeAsyncClient`, the same SSRF-guarded
    egress every provider uses — the OAuth flow gets no privileged network path.
    """

    provider = "instagram"

    def __init__(self, settings: Settings | None = None, *, http: SafeAsyncClient | None = None):
        self.settings = settings or get_settings()
        self._http = http

    def _client(self) -> SafeAsyncClient:
        return self._http or SafeAsyncClient(self.settings)

    def _require_config(self) -> tuple[str, str]:
        if not self.settings.instagram_oauth_configured:
            raise InstagramNotConfigured(
                "Instagram is not configured on this deployment. An administrator must "
                "set INSTAGRAM_APP_ID and INSTAGRAM_APP_SECRET from a Meta app that has "
                "the 'Instagram API with Instagram Login' product enabled."
            )
        return str(self.settings.instagram_app_id), str(self.settings.instagram_app_secret)

    # -- step 1: send the user to Meta ----------------------------------------

    def authorize_url(self, *, state: str) -> str:
        """Build the consent URL. ``state`` binds the flow to one authenticated user."""
        app_id, _ = self._require_config()
        query = urlencode(
            {
                "client_id": app_id,
                "redirect_uri": self.settings.instagram_redirect_uri,
                "scope": ",".join(self.settings.instagram_scopes),
                "response_type": "code",
                "state": state,
            }
        )
        return f"{self.settings.instagram_oauth_authorize_url}?{query}"

    # -- step 2: exchange the code --------------------------------------------

    async def exchange_code(self, code: str) -> TokenGrant:
        """Authorization code -> short-lived token -> long-lived token."""
        app_id, app_secret = self._require_config()
        client = self._client()
        try:
            short = await client.post(
                self.settings.instagram_oauth_token_url,
                data={
                    "client_id": app_id,
                    "client_secret": app_secret,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.settings.instagram_redirect_uri,
                    "code": code,
                },
            )
            if short.status_code != 200:
                raise InstagramAuthRejected(_describe_failure(short, "code exchange"))
            payload = _json(short, "code exchange")

            token = str(payload.get("access_token") or "")
            account_id = str(payload.get("user_id") or "")
            if not token or not account_id:
                raise InstagramAuthRejected(
                    "Meta's response to the code exchange did not include an access token "
                    "and account id."
                )
            permissions = payload.get("permissions")
            scopes = _scopes(permissions) or list(self.settings.instagram_scopes)

            long_lived = await self._exchange_for_long_lived(client, token, app_secret)
            return TokenGrant(
                token=Secret(long_lived[0]),
                account_id=account_id,
                scopes=scopes,
                expires_at=long_lived[1],
            )
        finally:
            if self._http is None:
                await client.aclose()

    async def _exchange_for_long_lived(
        self, client: SafeAsyncClient, short_token: str, app_secret: str
    ) -> tuple[str, datetime | None]:
        """Upgrade a ~1h token to a ~60d one.

        A failure here is not fatal to the connection: the short-lived token still works
        for an initial sync, and the user is simply asked to reconnect sooner. Returning
        the short token beats failing the whole flow.
        """
        query = urlencode(
            {
                "grant_type": "ig_exchange_token",
                "client_secret": app_secret,
                "access_token": short_token,
            }
        )
        response = await client.get(f"{self.settings.instagram_graph_host}/access_token?{query}")
        if response.status_code != 200:
            logger.warning(
                "instagram long-lived token exchange failed (status %s); keeping the "
                "short-lived token",
                response.status_code,
            )
            return short_token, _expiry(3600)
        try:
            payload = response.json()
        except ValueError:
            return short_token, _expiry(3600)
        token = str(payload.get("access_token") or "") or short_token
        return token, _expiry(payload.get("expires_in"))

    async def verify(self, token: Secret) -> dict[str, Any]:
        """Confirm a stored token still works, and return the account it belongs to.

        Run before each sync so a rejected credential produces a precise token state
        (and a "reconnect" prompt) rather than an empty report that looks like the user
        simply has nothing exposed.
        """
        client = self._client()
        try:
            query = urlencode({"fields": ME_FIELDS, "access_token": token.reveal()})
            response = await client.get(f"{self.settings.instagram_graph_base}/me?{query}")
            if response.status_code in (400, 401, 403):
                raise InstagramAuthRejected(_describe_failure(response, "account check"))
            if response.status_code != 200:
                raise InstagramOAuthError(
                    f"Instagram returned HTTP {response.status_code} for the account check."
                )
            return _json(response, "account check")
        finally:
            if self._http is None:
                await client.aclose()

    async def refresh(self, token: Secret) -> tuple[Secret, datetime | None]:
        """Extend a long-lived token. Only valid once it is at least 24 hours old."""
        client = self._client()
        try:
            query = urlencode(
                {"grant_type": "ig_refresh_token", "access_token": token.reveal()}
            )
            response = await client.get(
                f"{self.settings.instagram_graph_host}/refresh_access_token?{query}"
            )
            if response.status_code != 200:
                raise InstagramAuthRejected(_describe_failure(response, "token refresh"))
            payload = _json(response, "token refresh")
            refreshed = str(payload.get("access_token") or "")
            if not refreshed:
                raise InstagramAuthRejected("Meta's refresh response contained no token.")
            return Secret(refreshed), _expiry(payload.get("expires_in"))
        finally:
            if self._http is None:
                await client.aclose()


def _scopes(permissions: Any) -> list[str]:
    if isinstance(permissions, list):
        return [str(p) for p in permissions if p]
    if isinstance(permissions, str) and permissions:
        return [p.strip() for p in permissions.split(",") if p.strip()]
    return []


def _expiry(expires_in: Any) -> datetime | None:
    try:
        seconds = int(expires_in)
    except (TypeError, ValueError):
        return None
    return datetime.now(tz=UTC) + timedelta(seconds=seconds) if seconds > 0 else None


def _json(response: Any, step: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise InstagramOAuthError(f"Meta returned a non-JSON response during {step}.") from exc
    if not isinstance(payload, dict):
        raise InstagramOAuthError(f"Meta returned an unexpected response shape during {step}.")
    return payload


def _describe_failure(response: Any, step: str) -> str:
    """Turn an error response into a message safe to surface.

    Only Meta's own ``error.message`` is quoted, and only when it is a short string —
    the request that failed carried a token and its echo must never reach a log or a UI.
    """
    detail = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                detail = str(error.get("message") or error.get("error_user_msg") or "")
            elif isinstance(error, str):
                detail = error
            detail = detail or str(body.get("error_message") or "")
    except ValueError:
        detail = ""
    detail = detail[:200]
    suffix = f" Meta said: {detail}" if detail else ""
    return f"Instagram rejected the {step} (HTTP {response.status_code})." + suffix
