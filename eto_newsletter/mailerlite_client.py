"""MailerLite API client.

This module is the only place that talks to MailerLite. It is deliberately
isolated so that switching to another provider (Brevo, Mailchimp) later means
rewriting only this file, not the GUI or the rest of the app.

API reference: https://developers.mailerlite.com/docs
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests

API_BASE = "https://connect.mailerlite.com/api"
REQUEST_TIMEOUT = 30  # seconds


class MailerLiteError(Exception):
    """Raised when the MailerLite API returns an error or is unreachable."""


@dataclass
class Subscriber:
    """A single contact, as held in memory by the app."""

    email: str
    name: str = "N/A"
    company: str = "N/A"
    joined: str = "N/A"
    suspicious: str = "N/A"
    # Filled in after syncing with MailerLite:
    ml_id: str | None = None
    status: str | None = None

    @property
    def is_suspicious(self) -> bool:
        return bool(self.suspicious) and self.suspicious.strip().upper() != "N/A"


@dataclass
class MailerLiteClient:
    """Thin wrapper over the MailerLite REST API.

    Only the calls the app needs are implemented: verifying the key, ensuring a
    group exists, upserting subscribers, creating a campaign, sending a test,
    and scheduling the live send.

    ``verify_ssl`` controls TLS certificate verification. It defaults to True.
    It is set to False only when the user explicitly enables "Skip SSL
    verification" in Settings, which is sometimes needed behind a corporate
    proxy that performs SSL inspection. Disabling it weakens transport security,
    so it should be a temporary measure pending a proper root-certificate setup.
    """

    api_key: str
    verify_ssl: bool = True
    session: requests.Session = field(default_factory=requests.Session)

    # ---- low-level helpers -------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _request(self, method: str, path: str, _attempt: int = 0, **kwargs: Any) -> Any:
        url = f"{API_BASE}{path}"
        if not self.verify_ssl:
            # The user has intentionally disabled verification; silence the
            # repeated urllib3 warning so it doesn't flood the status log.
            import urllib3

            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        try:
            resp = self.session.request(
                method,
                url,
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
                verify=self.verify_ssl,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise MailerLiteError(f"Network error contacting MailerLite: {exc}") from exc

        # Rate limited: respect Retry-After, but give up after a few tries so
        # the app can never spin forever against a stuck endpoint.
        if resp.status_code == 429:
            if _attempt >= 5:
                raise MailerLiteError(
                    "MailerLite keeps rate-limiting the request. "
                    "Wait a minute and try again."
                )
            retry_after = int(resp.headers.get("Retry-After", "2"))
            time.sleep(min(retry_after, 10))
            return self._request(method, path, _attempt + 1, **kwargs)

        if resp.status_code == 401:
            raise MailerLiteError("Authentication failed - check the API key in Settings.")

        if not resp.ok:
            detail = ""
            try:
                detail = resp.json().get("message", "")
            except ValueError:
                detail = resp.text[:200]
            raise MailerLiteError(
                f"MailerLite API error {resp.status_code}: {detail or resp.reason}"
            )

        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    # ---- public operations -------------------------------------------------

    def verify_key(self) -> bool:
        """Return True if the API key is valid. Used by the Settings dialog."""
        self._request("GET", "/subscribers?limit=1")
        return True

    def find_group(self, name: str) -> str | None:
        """Return the id of the group whose name equals *name*, or None.

        The comparison ignores case and surrounding whitespace, so a group
        renamed to e.g. "korea contacts " in the dashboard is still found.
        """
        encoded = quote(name, safe="")
        data = self._request("GET", f"/groups?filter[name]={encoded}&limit=100")
        wanted = name.strip().casefold()
        for group in data.get("data", []):
            if str(group.get("name", "")).strip().casefold() == wanted:
                return group["id"]
        return None

    def create_group(self, name: str) -> str:
        created = self._request("POST", "/groups", json={"name": name})
        return created["data"]["id"]

    def ensure_group(self, name: str) -> str:
        """Return the id of the group called *name*, creating it if needed."""
        found = self.find_group(name)
        return found if found is not None else self.create_group(name)

    def delete_group(self, group_id: str) -> None:
        """Delete a group.  Subscribers themselves are not deleted, only the
        grouping - this is how the app guarantees a working group contains
        exactly the intended members before a send."""
        self._request("DELETE", f"/groups/{group_id}")

    def list_group_subscribers(
        self, group_id: str, progress=lambda _msg: None
    ) -> list[dict[str, Any]]:
        """Return every subscriber in the group as raw API dicts."""
        return self._list_paginated(
            f"/groups/{group_id}/subscribers", progress, "group member"
        )

    def list_all_subscribers(
        self, progress=lambda _msg: None
    ) -> list[dict[str, Any]]:
        """Return every subscriber in the whole account (all statuses)."""
        return self._list_paginated("/subscribers", progress, "subscriber")

    def _list_paginated(
        self, base_path: str, progress, noun: str
    ) -> list[dict[str, Any]]:
        """Fetch every item of a paginated collection.

        Handles both cursor-based and page-based pagination, whichever the API
        returns, and always terminates: a batch smaller than the page size
        means the last page has been reached.
        """
        members: list[dict[str, Any]] = []
        limit = 100
        cursor: str | None = None
        page: int | None = None
        while True:
            params = f"limit={limit}"
            if cursor:
                params += f"&cursor={quote(cursor, safe='')}"
            elif page:
                params += f"&page={page}"
            data = self._request("GET", f"{base_path}?{params}") or {}
            batch = data.get("data", [])
            members.extend(batch)
            progress(f"Fetched {len(members)} {noun}(s)…")

            if len(batch) < limit:
                break
            meta = data.get("meta") or {}
            next_cursor = meta.get("next_cursor")
            if next_cursor:
                cursor, page = next_cursor, None
                continue
            current = meta.get("current_page")
            last = meta.get("last_page")
            if current is not None and last is not None:
                if current >= last:
                    break
                cursor, page = None, current + 1
                continue
            # No pagination metadata: fall back to blind page increments
            # until a short/empty batch arrives.
            cursor, page = None, (page or 1) + 1
        return members

    @staticmethod
    def _subscriber_payload(sub: Subscriber, group_id: str) -> dict[str, Any]:
        fields: dict[str, str] = {}
        if sub.name and sub.name.upper() != "N/A":
            fields["name"] = sub.name
        if sub.company and sub.company.upper() != "N/A":
            fields["company"] = sub.company

        payload: dict[str, Any] = {"email": sub.email, "groups": [group_id]}
        if fields:
            payload["fields"] = fields
        return payload

    def upsert_subscriber(self, sub: Subscriber, group_id: str) -> str:
        """Create or update a subscriber and attach them to *group_id*.

        MailerLite's POST /subscribers performs an upsert by email, so the same
        address is never duplicated. Custom fields carry the optional metadata.
        Note: the operation is non-destructive - it appends the group and never
        removes the subscriber from other groups.
        """
        payload = self._subscriber_payload(sub, group_id)
        data = self._request("POST", "/subscribers", json=payload)
        sub.ml_id = data["data"]["id"]
        sub.status = data["data"].get("status")
        return sub.ml_id

    def upsert_subscribers_bulk(
        self,
        subs: list[Subscriber],
        group_id: str,
        progress=lambda _msg: None,
    ) -> None:
        """Upsert many subscribers using the /batch endpoint (50 per call).

        One batch call counts as a single request against the rate limit, so a
        500-contact list needs ~10 calls instead of 500. If a batch call fails
        (endpoint unavailable, or some sub-requests were rejected) the chunk
        falls back to one-by-one upserts, which surfaces the exact bad row.
        """
        total = len(subs)
        done = 0
        for start in range(0, total, 50):
            chunk = subs[start:start + 50]
            batch_payload = {
                "requests": [
                    {
                        "method": "POST",
                        "path": "api/subscribers",
                        "body": self._subscriber_payload(s, group_id),
                    }
                    for s in chunk
                ]
            }
            fallback = False
            try:
                result = self._request("POST", "/batch", json=batch_payload)
                if isinstance(result, dict) and result.get("failed"):
                    fallback = True
            except MailerLiteError:
                fallback = True
            if fallback:
                for s in chunk:
                    self.upsert_subscriber(s, group_id)
            done += len(chunk)
            progress(f"Preparing recipients {done}/{total}…")

    def delete_subscriber(self, subscriber_id: str) -> None:
        """Permanently remove a subscriber from the MailerLite account."""
        self._request("DELETE", f"/subscribers/{subscriber_id}")

    def unassign_from_group(self, subscriber_id: str, group_id: str) -> None:
        """Remove a subscriber from one group without deleting the subscriber
        or the group."""
        self._request("DELETE", f"/subscribers/{subscriber_id}/groups/{group_id}")

    def create_campaign(
        self,
        *,
        subject: str,
        from_name: str,
        from_email: str,
        html: str,
        group_id: str,
    ) -> dict[str, Any]:
        """Create a regular email campaign as a draft.

        Returns the full campaign object from the API response, which includes
        the ``filter`` describing the recipients.  Callers must verify that
        filter before scheduling - see NewsletterService._verify_targeting.
        """
        payload = {
            "name": subject,
            "type": "regular",
            "emails": [
                {
                    "subject": subject,
                    "from_name": from_name,
                    "from": from_email,
                    "content": html,
                }
            ],
            "groups": [group_id],
        }
        data = self._request("POST", "/campaigns", json=payload)
        return data["data"]

    def list_sent_campaigns(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the most recent campaigns that have finished sending."""
        data = self._request("GET", f"/campaigns?filter[status]=sent&limit={limit}")
        return (data or {}).get("data", [])

    def delete_campaign(self, campaign_id: str) -> None:
        self._request("DELETE", f"/campaigns/{campaign_id}")

    def check_url_ok(self, url: str) -> tuple[bool, int]:
        """Return (reachable, status_code) for a public URL such as the PDF link.

        Tries HEAD first; some servers reject HEAD, so any failure retries with
        a streaming GET that is closed immediately without downloading the body.
        """
        try:
            resp = self.session.head(
                url, timeout=15, verify=self.verify_ssl, allow_redirects=True
            )
            if resp.status_code >= 400:
                resp = self.session.get(
                    url, timeout=15, verify=self.verify_ssl,
                    allow_redirects=True, stream=True,
                )
                resp.close()
            return resp.status_code < 400, resp.status_code
        except requests.RequestException:
            return False, 0

    def get_campaign(self, campaign_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/campaigns/{campaign_id}")
        return data.get("data", {})

    def schedule_now(self, campaign_id: str) -> None:
        """Send the campaign immediately."""
        self._request(
            "POST",
            f"/campaigns/{campaign_id}/schedule",
            json={"delivery": "instant"},
        )

    def campaign_stats(self, campaign_id: str) -> dict[str, Any]:
        return self.get_campaign(campaign_id).get("stats", {})
