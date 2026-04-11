"""
RJM Outreach Agent — Gmail API Client

Handles OAuth2 authentication, sending, drafting, and inbox scanning.
Uses Google's official gmail API v1 via google-api-python-client.

Auth flow:
  First run: opens browser for OAuth consent → saves token.json
  Subsequent runs: loads token.json silently (auto-refresh)
"""

import base64
import email as email_lib
import logging
import re
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import CREDS_PATH, TOKEN_PATH, GMAIL_SCOPES, FROM_EMAIL, FROM_NAME

log = logging.getLogger("outreach.gmail")

_service = None   # Module-level singleton


def get_service():
    """Return authenticated Gmail API service (lazy-init, cached)."""
    global _service
    if _service:
        return _service

    creds = None
    if TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), GMAIL_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("Refreshing Gmail token...")
            creds.refresh(Request())
        else:
            if not CREDS_PATH.exists():
                raise FileNotFoundError(
                    f"Gmail credentials not found at {CREDS_PATH}\n"
                    "Run: python agent.py setup"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDS_PATH), GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(str(TOKEN_PATH), "w") as f:
            f.write(creds.to_json())
        log.info("Gmail token saved to %s", TOKEN_PATH)

    _service = build("gmail", "v1", credentials=creds)
    log.info("Gmail API authenticated as %s", FROM_EMAIL)
    return _service


def _build_mime(to_email: str, to_name: str, subject: str, body: str,
                reply_to_thread_id: str = None, in_reply_to_message_id: str = None) -> dict:
    """Build a Gmail API message dict from components."""
    msg = MIMEMultipart("alternative")
    # formataddr properly quotes display names that contain special chars (colons,
    # commas, brackets etc.) per RFC 2822 — prevents "Invalid To header" errors.
    msg["To"]   = formataddr((to_name, to_email)) if to_name else to_email
    msg["From"] = formataddr((FROM_NAME, FROM_EMAIL))
    msg["Subject"] = subject

    if in_reply_to_message_id:
        msg["In-Reply-To"] = in_reply_to_message_id
        msg["References"]  = in_reply_to_message_id

    # Plain text part — primary (most email clients prefer it)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    payload = {"raw": raw}
    if reply_to_thread_id:
        payload["threadId"] = reply_to_thread_id
    return payload


def send_email(to_email: str, to_name: str, subject: str, body: str,
               reply_to_thread_id: str = None,
               in_reply_to_message_id: str = None) -> dict:
    """
    Send an email via Gmail API.
    Returns dict with 'id' (message_id) and 'threadId'.
    Raises HttpError on failure.
    """
    svc = get_service()
    payload = _build_mime(to_email, to_name, subject, body,
                          reply_to_thread_id, in_reply_to_message_id)
    result = svc.users().messages().send(userId="me", body=payload).execute()
    log.info("Sent email to %s — message_id=%s thread=%s", to_email, result["id"], result.get("threadId"))
    return result


def create_draft(to_email: str, to_name: str, subject: str, body: str,
                 reply_to_thread_id: str = None,
                 in_reply_to_message_id: str = None) -> dict:
    """
    Create a Gmail draft instead of sending (used when DRAFT_MODE=true).
    Returns draft object.
    """
    svc = get_service()
    payload = _build_mime(to_email, to_name, subject, body,
                          reply_to_thread_id, in_reply_to_message_id)
    draft = svc.users().drafts().create(
        userId="me", body={"message": payload}
    ).execute()
    log.info("Draft created for %s — draft_id=%s", to_email, draft["id"])
    return draft


def get_thread_messages(thread_id: str) -> list[dict]:
    """Fetch all messages in a thread."""
    svc = get_service()
    thread = svc.users().threads().get(userId="me", id=thread_id, format="metadata").execute()
    return thread.get("messages", [])


def scan_inbox_for_replies(sent_thread_ids: list[str]) -> dict[str, dict]:
    """
    Given a list of thread IDs we sent, check which ones have received replies.
    Returns {thread_id: reply_info_dict} for threads with new incoming messages.
    """
    if not sent_thread_ids:
        return {}

    svc = get_service()
    replies_found = {}

    for thread_id in sent_thread_ids:
        try:
            thread = svc.users().threads().get(
                userId="me", id=thread_id, format="full"
            ).execute()
            messages = thread.get("messages", [])
            if len(messages) < 2:
                continue

            # Check if any message in the thread is FROM someone other than us
            for msg in messages[1:]:   # skip the first (our sent message)
                headers = {h["name"].lower(): h["value"]
                           for h in msg.get("payload", {}).get("headers", [])}
                from_header = headers.get("from", "")
                if FROM_EMAIL.lower() not in from_header.lower():
                    # Found a reply from someone else
                    snippet = msg.get("snippet", "")
                    replies_found[thread_id] = {
                        "message_id": msg["id"],
                        "thread_id":  thread_id,
                        "from":       from_header,
                        "subject":    headers.get("subject", ""),
                        "snippet":    snippet,
                        "date":       headers.get("date", ""),
                    }
                    break
        except HttpError as exc:
            log.warning("Could not fetch thread %s: %s", thread_id, exc)

    return replies_found


def search_messages(query: str, max_results: int = 50) -> list[dict]:
    """Search Gmail with a query string. Returns list of message stubs."""
    svc = get_service()
    results = svc.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()
    return results.get("messages", [])


def get_message_snippet(message_id: str) -> str:
    """Fetch just the snippet of a message (fast)."""
    svc = get_service()
    msg = svc.users().messages().get(
        userId="me", id=message_id, format="metadata"
    ).execute()
    return msg.get("snippet", "")


def check_bounced_delivery_failures(since_days: int = 30) -> list[dict]:
    """
    Search for mailer-daemon / delivery failure messages to catch actual bounces.
    Returns list of dicts with {to_address, reason, date}.
    """
    svc = get_service()
    cutoff = (datetime.now() - timedelta(days=since_days)).strftime("%Y/%m/%d")
    query = (
        f"from:(mailer-daemon OR postmaster) "
        f"(\"delivery failure\" OR \"undeliverable\" OR \"bounce\" OR \"failed\") "
        f"after:{cutoff}"
    )
    messages = search_messages(query, max_results=100)
    failures = []

    for stub in messages:
        try:
            msg = svc.users().messages().get(
                userId="me", id=stub["id"], format="full"
            ).execute()
            snippet = msg.get("snippet", "")
            headers = {h["name"].lower(): h["value"]
                       for h in msg.get("payload", {}).get("headers", [])}

            # Try to extract the original recipient from the bounce body
            # Most mailer-daemons include "Final-Recipient:" in the body
            body = _extract_body_text(msg)
            recipient = _extract_bounce_recipient(body, snippet)

            failures.append({
                "message_id": stub["id"],
                "date":       headers.get("date", ""),
                "subject":    headers.get("subject", ""),
                "snippet":    snippet,
                "to_address": recipient,
                "raw_body":   body[:500],
            })
        except HttpError as exc:
            log.warning("Could not parse bounce message %s: %s", stub["id"], exc)

    return failures


def _extract_body_text(msg: dict) -> str:
    """Extract plain text body from a Gmail API message dict.
    Falls back to HTML (stripped) when plain text is absent or empty."""
    payload = msg.get("payload", {})

    def decode_part(part):
        data = part.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        return ""

    def walk_for_mime(part, target_mime):
        mime = part.get("mimeType", "")
        if mime == target_mime:
            return decode_part(part)
        if "parts" in part:
            for sub in part["parts"]:
                result = walk_for_mime(sub, target_mime)
                if result:
                    return result
        return ""

    def strip_html(html: str) -> str:
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;", "&", text)
        text = re.sub(r"&lt;", "<", text)
        text = re.sub(r"&gt;", ">", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    plain = walk_for_mime(payload, "text/plain").strip()
    if plain and len(plain) > 10:
        return plain

    # Fall back to HTML → strip tags
    html = walk_for_mime(payload, "text/html")
    if html:
        return strip_html(html)

    return plain  # return whatever we had (may be empty)


def _extract_bounce_recipient(body: str, snippet: str) -> str:
    """Try to extract the failed-delivery email address from bounce text."""
    # Look for "Final-Recipient: rfc822; email@domain.com" pattern
    match = re.search(r"[Ff]inal-[Rr]ecipient:\s*rfc822;\s*([^\s\r\n]+)", body)
    if match:
        return match.group(1).strip()

    # Look for any email-looking pattern in snippet/body
    emails = re.findall(r"[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}", body + " " + snippet)
    # Filter out our own address
    emails = [e for e in emails if FROM_EMAIL.lower() not in e.lower()
              and "mailer-daemon" not in e.lower()
              and "postmaster" not in e.lower()]
    return emails[0] if emails else ""


def fetch_sent_messages(max_results: int = 500) -> list[dict]:
    """
    Fetch sent messages from the Gmail Sent folder.
    Returns a list of dicts with: {to_email, subject, body, message_id, thread_id, date}
    Used to backfill the DB with original email bodies.
    """
    svc = get_service()
    log.info("Fetching up to %d sent messages from Gmail...", max_results)

    # List all sent messages (paged)
    messages = []
    page_token = None
    while len(messages) < max_results:
        kwargs = {
            "userId": "me",
            "labelIds": ["SENT"],
            "maxResults": min(100, max_results - len(messages)),
        }
        if page_token:
            kwargs["pageToken"] = page_token
        resp = svc.users().messages().list(**kwargs).execute()
        batch = resp.get("messages", [])
        messages.extend(batch)
        page_token = resp.get("nextPageToken")
        if not page_token or not batch:
            break

    log.info("Fetched %d message stubs — retrieving full content...", len(messages))

    results = []
    for stub in messages:
        try:
            msg = svc.users().messages().get(
                userId="me", id=stub["id"], format="full"
            ).execute()
            headers = {
                h["name"].lower(): h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }
            to_raw = headers.get("to", "")
            # Extract plain email from "Name <email>" format
            import re as _re
            m = _re.search(r"[\w.+\-]+@[\w.\-]+", to_raw)
            to_email = m.group(0).lower() if m else ""
            if not to_email:
                continue

            subject = headers.get("subject", "")
            body    = _extract_body_text(msg)
            date    = headers.get("date", "")

            results.append({
                "to_email":   to_email,
                "subject":    subject,
                "body":       body,
                "message_id": msg["id"],
                "thread_id":  msg.get("threadId", ""),
                "date":       date,
            })
        except Exception as exc:
            log.debug("Could not parse sent message %s: %s", stub["id"], exc)

    log.info("Parsed %d sent messages with recipient addresses", len(results))
    return results


def verify_auth():
    """Quick check that Gmail auth is working. Returns profile dict."""
    svc = get_service()
    profile = svc.users().getProfile(userId="me").execute()
    log.info("Gmail auth verified: %s, %d messages total",
             profile.get("emailAddress"), profile.get("messagesTotal", 0))
    return profile
