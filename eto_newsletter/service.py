"""Service layer: orchestrates the API client to carry out app actions.

This sits between the GUI and the raw API client.  It owns the logic that the
provider's API does not give directly:

* sending to a *selected subset* of contacts (MailerLite sends to groups, so we
  sync exactly the chosen subscribers into a dedicated send-group),
* test sends (MailerLite has no test endpoint, so we send the real campaign to
  a one-member test group), and
* recipient-safety verification: a campaign is never scheduled unless the API
  confirms it targets exactly the group we built, so a dropped or ignored
  group filter can never result in a send to all subscribers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .content import markdown_to_email_html
from .mailerlite_client import MailerLiteClient, MailerLiteError, Subscriber

# Default name of the user-facing subscriber group. The actual name is
# configurable in Settings and passed to NewsletterService.
DEFAULT_MAIN_GROUP = "ETO Korea Newsletter Subscribers"
# Internal working groups, rebuilt from scratch for every test/live send:
SEND_GROUP = "ETO Newsletter (current send)"
TEST_GROUP = "ETO Newsletter (test)"

ProgressFn = Callable[[str], None]


@dataclass
class CampaignDraft:
    subject: str
    from_name: str
    from_email: str
    markdown: str
    pdf_url: str | None
    social_links: dict[str, str] | None = None


@dataclass
class GroupComparison:
    """Result of comparing an uploaded CSV against MailerLite.

    ``basis`` says what the CSV was compared against:
      * ``"group"``   - the configured subscriber group. Its membership is
        synced after every successful live send, so it means "who received
        the previous newsletter".
      * ``"account"`` - the saved group does not exist yet (no send made with
        this app), so the comparison fell back to every subscriber in the
        MailerLite account.
      * ``"none"``    - neither the group nor any account subscribers exist;
        the whole CSV is new.
    """

    basis: str  # "group" | "account" | "none"
    baseline_total: int
    new_emails: list[str]  # in the CSV, not in the baseline
    # In the baseline but missing from the CSV. Each entry is a dict with
    # email / status / id / name / company (name and company may be "").
    departed: list[dict[str, str]]
    unsubscribed: list[str]  # in both, but no longer active in MailerLite


class NewsletterService:
    def __init__(
        self, client: MailerLiteClient, main_group: str = DEFAULT_MAIN_GROUP
    ) -> None:
        self.client = client
        self.main_group = main_group.strip() or DEFAULT_MAIN_GROUP

    # ---- contact syncing ---------------------------------------------------

    def sync_contacts(
        self,
        subscribers: list[Subscriber],
        progress: ProgressFn = lambda _msg: None,
    ) -> str:
        """Upsert every subscriber into the main group.  Returns the group id."""
        group_id = self.client.ensure_group(self.main_group)
        self.client.upsert_subscribers_bulk(subscribers, group_id, progress)
        return group_id

    def compare_with_main_group(
        self,
        subscribers: list[Subscriber],
        progress: ProgressFn = lambda _msg: None,
    ) -> GroupComparison:
        """Compare the uploaded CSV against MailerLite.

        Compares against the saved group when it exists (= last send's
        recipients); otherwise falls back to every subscriber in the account,
        so contacts imported through the MailerLite dashboard still count as
        "already registered" rather than showing up as all-new.

        Returns who is new (in the CSV but not in the baseline), who departed
        (in the baseline but missing from the CSV), and who unsubscribed via
        the MailerLite link (present in both, but status is no longer active).
        """
        progress(f"Looking up the group “{self.main_group}”…")
        group_id = self.client.find_group(self.main_group)
        if group_id is not None:
            basis = "group"
            members = self.client.list_group_subscribers(group_id, progress)
        else:
            progress("No saved group yet — fetching every subscriber in the account…")
            members = self.client.list_all_subscribers(progress)
            basis = "account" if members else "none"

        if basis == "none":
            return GroupComparison(
                basis="none",
                baseline_total=0,
                new_emails=[s.email for s in subscribers],
                departed=[],
                unsubscribed=[],
            )
        group_by_email = {
            (m.get("email") or "").strip().lower(): m
            for m in members
            if (m.get("email") or "").strip()
        }
        csv_emails = {s.email.strip().lower(): s.email for s in subscribers}

        new_emails = [
            orig for low, orig in csv_emails.items() if low not in group_by_email
        ]
        def _departed_entry(m: dict[str, Any]) -> dict[str, str]:
            fields = m.get("fields") or {}
            return {
                "email": m.get("email", ""),
                "status": m.get("status") or "unknown",
                "id": str(m.get("id") or ""),
                "name": str(fields.get("name") or ""),
                "company": str(fields.get("company") or ""),
            }

        departed = [
            _departed_entry(m)
            for low, m in group_by_email.items()
            if low not in csv_emails
        ]
        unsubscribed = [
            m.get("email", "")
            for low, m in group_by_email.items()
            if low in csv_emails and (m.get("status") or "active") != "active"
        ]
        progress("Comparison finished.")
        return GroupComparison(
            basis=basis,
            baseline_total=len(group_by_email),
            new_emails=sorted(new_emails, key=str.lower),
            departed=sorted(departed, key=lambda d: d["email"].lower()),
            unsubscribed=sorted(unsubscribed, key=str.lower),
        )

    def _fresh_group(
        self,
        group_name: str,
        subscribers: list[Subscriber],
        progress: ProgressFn,
    ) -> str:
        """Build a group that contains *exactly* these subscribers.

        MailerLite group membership is additive and persists between runs, so a
        reused working group would accumulate recipients from previous tests or
        sends.  To guarantee exact membership, any existing group with this
        name is deleted (deleting a group does not delete the subscribers) and
        a fresh one is created containing only the given members.
        """
        existing = self.client.find_group(group_name)
        if existing is not None:
            progress("Clearing previous working group…")
            self.client.delete_group(existing)
        group_id = self.client.create_group(group_name)
        self.client.upsert_subscribers_bulk(subscribers, group_id, progress)
        return group_id

    def _sync_main_group_membership(
        self,
        subscribers: list[Subscriber],
        progress: ProgressFn,
    ) -> None:
        """Make the main group contain exactly *subscribers* without deleting
        or recreating the group (its id, and anything wired to it in the
        MailerLite dashboard, stays intact)."""
        group_id = self.client.find_group(self.main_group)
        if group_id is None:
            group_id = self.client.create_group(self.main_group)
            current: list[dict[str, Any]] = []
        else:
            current = self.client.list_group_subscribers(group_id, progress)

        self.client.upsert_subscribers_bulk(subscribers, group_id, progress)

        keep = {s.email.strip().lower() for s in subscribers}
        stale = [
            m for m in current
            if (m.get("email") or "").strip().lower() not in keep and m.get("id")
        ]
        for i, member in enumerate(stale, start=1):
            self.client.unassign_from_group(str(member["id"]), group_id)
            if i % 10 == 0 or i == len(stale):
                progress(f"Removing stale group members {i}/{len(stale)}…")

    # ---- recipient-safety gate ----------------------------------------------

    @staticmethod
    def _extract_filter_group_ids(campaign: dict[str, Any]) -> set[str]:
        """Collect every group id referenced by the campaign's recipient filter.

        The API echoes recipients as e.g.
        ``"filter": [[{"operator": "in_any", "args": ["groups", ["42"]]}]]``.
        """
        ids: set[str] = set()
        for condition_group in campaign.get("filter") or []:
            for condition in condition_group or []:
                if not isinstance(condition, dict):
                    continue
                args = condition.get("args") or []
                if len(args) >= 2 and args[0] == "groups" and isinstance(args[1], list):
                    ids.update(str(x) for x in args[1])
        return ids

    def _verify_targeting(self, campaign: dict[str, Any], group_id: str) -> None:
        """Refuse to proceed unless the campaign targets exactly *group_id*.

        This is the hard safety gate against sending to the whole list: if the
        API dropped or ignored the group filter, the campaign would default to
        all active subscribers, so we abort before scheduling.
        """
        targeted = self._extract_filter_group_ids(campaign)
        if not targeted:
            # The creation response may omit the filter; re-fetch to be sure.
            campaign_id = campaign.get("id")
            if campaign_id:
                targeted = self._extract_filter_group_ids(
                    self.client.get_campaign(str(campaign_id))
                )
        if targeted != {str(group_id)}:
            shown = ", ".join(sorted(targeted)) or "none (all subscribers)"
            raise MailerLiteError(
                "SAFETY STOP - the campaign was NOT sent.  MailerLite did not "
                f"apply the intended recipient group (expected group {group_id}, "
                f"got: {shown}).  Sending was aborted to avoid emailing the "
                "entire subscriber list.  Please check the draft campaign in "
                "the MailerLite dashboard and contact support if this repeats."
            )

    # ---- pre-send checks ------------------------------------------------------

    def _check_pdf_link(self, draft: CampaignDraft, progress: ProgressFn) -> None:
        """Abort if the PDF link is set but does not answer with HTTP < 400.

        A typo in the link cannot be fixed after the send, so both the test and
        the live send verify it first.
        """
        if not draft.pdf_url:
            return
        progress("Checking the PDF link…")
        ok, code = self.client.check_url_ok(draft.pdf_url)
        if not ok:
            shown = f"HTTP {code}" if code else "no response"
            raise MailerLiteError(
                f"The PDF link does not work ({shown}): {draft.pdf_url}\n"
                "Fix the link (or clear the field) and try again. "
                "Nothing was sent."
            )

    @staticmethod
    def _parse_ml_datetime(value: Any) -> datetime | None:
        """Parse MailerLite timestamps ('2026-07-03 09:00:00' or ISO), else None."""
        if not value or not isinstance(value, str):
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # MailerLite reports UTC
        return dt

    def find_recent_same_subject(
        self,
        subject: str,
        hours: int = 24,
        progress: ProgressFn = lambda _msg: None,
    ) -> dict[str, Any] | None:
        """Return a recently *sent* campaign with this exact name, if any.

        Used as a duplicate-send guard: if a network hiccup hid the success of
        a previous attempt, this surfaces it before the user sends again. A
        campaign whose timestamp cannot be parsed is treated as recent (the
        safe direction — better a spurious warning than a duplicate send).
        """
        progress("Checking for a recent send with the same subject…")
        for campaign in self.client.list_sent_campaigns():
            if campaign.get("name") != subject:
                continue
            ts = (
                campaign.get("finished_at")
                or campaign.get("scheduled_for")
                or campaign.get("created_at")
            )
            sent_at = self._parse_ml_datetime(ts)
            if sent_at is None or sent_at >= datetime.now(timezone.utc) - timedelta(hours=hours):
                return campaign
        return None

    def _cleanup_test_campaigns(self, progress: ProgressFn) -> None:
        """Best-effort removal of old, already-sent [TEST] campaigns so they do
        not pile up in the MailerLite dashboard. Never blocks a test send."""
        try:
            old = [
                c for c in self.client.list_sent_campaigns()
                if str(c.get("name", "")).startswith("[TEST] ")
            ]
            for i, campaign in enumerate(old, start=1):
                self.client.delete_campaign(str(campaign["id"]))
                progress(f"Removed old test campaign {i}/{len(old)}…")
        except MailerLiteError:
            pass

    # ---- account hygiene -------------------------------------------------------

    def delete_departed(
        self,
        departed: list[dict[str, str]],
        progress: ProgressFn = lambda _msg: None,
    ) -> tuple[int, list[str]]:
        """Delete departed contacts from the MailerLite account.

        Only *active* contacts are deleted. Unsubscribed/bounced ones are kept
        on purpose: deleting them would erase the opt-out record, and they
        would be emailed again if they ever reappear in a CSV.

        Returns (deleted_count, skipped_descriptions).
        """
        deleted = 0
        skipped: list[str] = []
        for entry in departed:
            if entry.get("status") != "active" or not entry.get("id"):
                skipped.append(
                    f"{entry.get('email')} ({entry.get('status') or 'no id'})"
                )
                continue
            self.client.delete_subscriber(entry["id"])
            deleted += 1
            progress(f"Deleted {deleted} departed contact(s)…")
        return deleted, skipped

    def add_to_main_group(
        self,
        subscribers: list[Subscriber],
        progress: ProgressFn = lambda _msg: None,
    ) -> int:
        """Add subscribers to the saved group, creating the group if needed.

        Used from the comparison dialog to register the checked new sign-ups.
        Returns how many were added.
        """
        if not subscribers:
            raise MailerLiteError("No subscribers selected.")
        progress(f"Adding {len(subscribers)} contact(s) to “{self.main_group}”…")
        group_id = self.client.ensure_group(self.main_group)
        self.client.upsert_subscribers_bulk(subscribers, group_id, progress)
        return len(subscribers)

    # ---- building the campaign ----------------------------------------------

    def _render(self, draft: CampaignDraft) -> str:
        return markdown_to_email_html(
            draft.markdown,
            title=draft.subject,
            pdf_url=draft.pdf_url,
            social_links=draft.social_links,
        )

    def _create_verified_campaign(
        self,
        draft: CampaignDraft,
        subject: str,
        group_id: str,
        progress: ProgressFn,
    ) -> str:
        progress("Creating campaign…")
        campaign = self.client.create_campaign(
            subject=subject,
            from_name=draft.from_name,
            from_email=draft.from_email,
            html=self._render(draft),
            group_id=group_id,
        )
        progress("Verifying recipients…")
        self._verify_targeting(campaign, group_id)
        return str(campaign["id"])

    # ---- test send ------------------------------------------------------------

    def send_test(
        self,
        draft: CampaignDraft,
        test_emails: list[str],
        progress: ProgressFn = lambda _msg: None,
    ) -> None:
        """Send a one-off preview to *test_emails* only.

        The test group is rebuilt from scratch each time so it contains exactly
        these addresses, and the campaign's recipient filter is verified
        against that group before scheduling.
        """
        if not test_emails:
            raise MailerLiteError("No test address given.")
        self._check_pdf_link(draft, progress)
        self._cleanup_test_campaigns(progress)
        progress(f"Preparing {len(test_emails)} test recipient(s)…")
        test_subs = [Subscriber(email=e) for e in test_emails]
        group_id = self._fresh_group(TEST_GROUP, test_subs, progress)

        campaign_id = self._create_verified_campaign(
            draft, f"[TEST] {draft.subject}", group_id, progress
        )
        progress("Sending test…")
        self.client.schedule_now(campaign_id)
        progress(
            f"Test sent to {len(test_emails)} address(es) only. "
            "Check the inboxes before the real send."
        )

    # ---- live send ------------------------------------------------------------

    def send_to_selected(
        self,
        draft: CampaignDraft,
        selected: list[Subscriber],
        progress: ProgressFn = lambda _msg: None,
    ) -> str:
        """Send the campaign to exactly the *selected* subscribers.

        The send group is rebuilt from scratch each time, so recipients
        deselected now (or selected in previous months) are genuinely excluded,
        and the campaign's recipient filter is verified before scheduling.
        """
        if not selected:
            raise MailerLiteError("No recipients selected.")

        self._check_pdf_link(draft, progress)
        progress(f"Preparing {len(selected)} recipients…")
        group_id = self._fresh_group(SEND_GROUP, selected, progress)

        campaign_id = self._create_verified_campaign(
            draft, draft.subject, group_id, progress
        )
        progress("Sending…")
        self.client.schedule_now(campaign_id)

        # Sync the subscriber group's membership to mirror exactly this send,
        # so next month's "compare with MailerLite" reports new sign-ups and
        # departures against who actually received this issue. The group
        # itself is never deleted - it may be wired to dashboard signup forms,
        # and deleting would change its id. The campaign is already scheduled
        # at this point: a failure here must NOT read as a failed send, or the
        # user may resend the newsletter to everyone.
        try:
            progress(f"Updating the group “{self.main_group}”…")
            self._sync_main_group_membership(selected, progress)
        except MailerLiteError as exc:
            progress(
                "Newsletter sent, but the subscriber group could not be "
                f"updated ({exc}). Next month's comparison may be stale."
            )

        progress(f"Sent to {len(selected)} subscribers.")
        return campaign_id
