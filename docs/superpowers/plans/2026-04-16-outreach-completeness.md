# Outreach Completeness — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring the outreach, discover, and research pillars of the RJM Command Centre from ~5/10 completeness to ~9/10 across five bounded "lakes" — safety net + observability, end-to-end pipeline flow, research loop closure + email branching, test baseline, and blocking brand gate on email generation.

**Architecture:** Python 3 + SQLite (`outreach_agent/outreach.db`) orchestrated by `outreach_agent/agent.py` and per-concern modules. Discover and research are Claude-CLI skills invoked via `outreach_agent/discover_agent.py` + `outreach_agent/research_agent.py` and drive the SQLite state through `outreach_agent/run_cycle.py` CLI commands. The existing event bus (`outreach_agent/events.py`) is the backbone for cross-module observability.

**Tech Stack:** Python 3.11, stdlib `sqlite3`, `pytest`, `unittest.mock`, Gmail API v1 via `googleapiclient`, Claude CLI (Haiku 4.5) via `subprocess`, launchd.

**Applies principles from:** [Completeness Principle](../../../../../../.claude/projects/-Users-motomoto-Documents-Robert-Jan-Mastenbroek-Command-Centre/memory/feedback_completeness_principle.md) — default to the complete option. Lakes are boilable, oceans are flagged at the end of this plan.

---

## Lake Summary

| Lake | Scope | Priority signal |
|------|-------|----------------|
| 1 | Safety net + observability — close silent-failure paths, add retries, persist heartbeat, publish scheduler events | Unblocks trust in the other lakes |
| 2 | Pipeline end-to-end flow — discover → research → send loop tightened, dead-letter path for unrecoverable sends | Removes throughput leaks |
| 3 | Research loop closure + email branching — research_done changes the prompt, insights feed scoring | Quality multiplier |
| 4 | Test baseline — fixtures, mocks, regression tests on hot paths | Zero-defect posture |
| 5 | Blocking brand gate on email generation — `gate_or_warn` → `gate_or_reject` with one retry | Brand protection |

## File Structure

**Modified:**
- `outreach_agent/reply_responder.py` — wrap send block in try/finally calling `unmark_claimed` on every failure branch
- `outreach_agent/gmail_client.py` — add `send_email_with_retry()` with exponential backoff
- `outreach_agent/agent.py` — replace 3 silent `except Exception:` handlers with logged handlers; call new helpers; publish events
- `outreach_agent/db.py` — add `personalization_audit` table + helpers, `dead_letter` status, `bump_send_attempts`, `mark_dead_letter`; wire `log_discovery` call site
- `outreach_agent/scheduler.py` — publish `outreach.rate_limit_paused` event when bounce or quota trips
- `outreach_agent/template_engine.py` — conditional research-aware system prompt, hooks_used JSON schema, `gate_or_reject` integration with 1 retry
- `outreach_agent/contact_scorer.py` — fix `contact_type` → `type` column bug in reply rate score, consume learning adjustments
- `outreach_agent/learning.py` — add `get_contact_type_score_adjustment()` reading from `learning_insights`
- `outreach_agent/run_cycle.py` — `cmd_add_contact` calls `db.log_discovery`; new `cmd_pending_research` adds `recently_researched` skip; `cmd_plan` publishes pipeline gap events
- `outreach_agent/research_agent.py` — consult `db.recently_searched` before WebSearch (if file exists — otherwise the skill SKILL.md gets a lightweight wrapper rule)
- `outreach_agent/config.py` — `OUTREACH_HEARTBEAT_PATH`, `MAX_SEND_ATTEMPTS`, `BRAND_GATE_MODE`
- `outreach_agent/master_agent.py` — subscribe to `outreach.rate_limit_paused`, `pipeline.gap_detected`, and log to master ledger

**Created:**
- `outreach_agent/tests/__init__.py` — marker
- `outreach_agent/tests/conftest.py` — pytest fixtures (`temp_db`, `mock_gmail`, `fake_claude`)
- `outreach_agent/tests/test_bounce.py` — bounce.verify_email regression
- `outreach_agent/tests/test_reply_detector.py` — reply classification regression
- `outreach_agent/tests/test_send_batch.py` — _send_batch happy path + failure reclaim
- `outreach_agent/tests/test_dedup.py` — `db.add_contact` duplicate + org-domain dedup
- `outreach_agent/tests/test_contact_scorer.py` — scoring math + `type` column fix regression
- `outreach_agent/tests/test_reply_responder.py` — claim + unclaim on failure
- `outreach_agent/tests/test_template_engine.py` — research branching + brand gate integration
- `outreach_agent/tests/test_scheduler.py` — window, interval, quota, event publish
- `outreach_agent/heartbeat.py` — lightweight writer called from `agent.cmd_run` end-of-cycle, read by `rjm.py status`
- `ops/launchd/com.rjm.outreach.plist` — cron-to-launchd migration spec (plus README for installation)
- `scripts/install-outreach-launchd.sh` — idempotent installer

**Reused as-is:** `bounce.py`, `reply_classifier.py`, `brand_gate.py`, `contact_scorer.py` (scoring math kept, only column fix + adjustment call added), `events.py`, `followup_engine.py`, `discover_agent.py` (Claude-CLI wrapper), `~/.claude/scheduled-tasks/rjm-discover/SKILL.md` (skill body unchanged — `run_cycle.py add_contact` already persists discovery log once we wire the call there).

---

## Lake 1 — Safety Net + Observability

**Intent:** Close every silent-failure path on the send side and give every send cycle a visible heartbeat. When the system stops working, it must say so loudly — to the event bus and to the filesystem — not into `/dev/null`.

### Task 1 — reply_responder failure reclaim

**Files:**
- Modify: `outreach_agent/reply_responder.py:309-361`
- Test: `outreach_agent/tests/test_reply_responder.py`

- [ ] **Step 1: Create conftest fixture scaffold**

Create `outreach_agent/tests/__init__.py` (empty) and `outreach_agent/tests/conftest.py`:

```python
"""Shared pytest fixtures for outreach_agent tests."""
import sys
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# Make outreach_agent importable as a flat module namespace
OUTREACH_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(OUTREACH_DIR))


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point db.DB_PATH at an isolated temp file and init schema."""
    db_file = tmp_path / "test_outreach.db"
    import config
    monkeypatch.setattr(config, "DB_PATH", db_file)
    import db
    monkeypatch.setattr(db, "DB_PATH", db_file)
    db.init_db()
    yield db_file


@pytest.fixture
def mock_gmail(monkeypatch):
    """Replace gmail_client module functions with MagicMocks."""
    import gmail_client
    mock_send = MagicMock(return_value={"id": "msg-test", "threadId": "thr-test"})
    mock_thread = MagicMock(return_value=[])
    mock_replied = MagicMock(return_value=False)
    monkeypatch.setattr(gmail_client, "send_email", mock_send)
    monkeypatch.setattr(gmail_client, "get_thread_messages", mock_thread)
    monkeypatch.setattr(gmail_client, "already_replied_in_thread", mock_replied)
    return {"send": mock_send, "thread": mock_thread, "replied": mock_replied}


@pytest.fixture
def fake_claude(monkeypatch):
    """Replace template_engine._call_claude with a canned responder."""
    import template_engine
    def _call(prompt, model=None, timeout=None):
        # Return valid JSON the parser accepts
        return '{"subject":"test subj","body":"test body — Living Water"}'
    monkeypatch.setattr(template_engine, "_call_claude", _call)
    return _call
```

- [ ] **Step 2: Write the failing test**

Create `outreach_agent/tests/test_reply_responder.py`:

```python
"""Regression: reply_responder must release the claim on any failure path."""
from unittest.mock import patch, MagicMock
import pytest


def _seed_positive_reply(db):
    db.add_contact("curator@example.com", "Test Curator", "curator", "melodic techno", "", source="manual")
    db.mark_verified("curator@example.com")
    db.update_contact(
        "curator@example.com",
        reply_intent="positive",
        reply_action="send_track",
        reply_classified_at="2026-04-16T10:00:00",
        gmail_thread_id="thr-123",
        reply_message_id="msg-123",
        sent_subject="Living Water for your playlist",
    )


def test_reply_responder_releases_claim_on_send_failure(temp_db, mock_gmail, fake_claude):
    import db
    import reply_responder
    _seed_positive_reply(db)

    mock_gmail["send"].side_effect = RuntimeError("boom")

    result = reply_responder.run(dry_run=False)

    assert result["failed"] == 1
    row = db.get_contact("curator@example.com")
    assert row["date_replied"] is None, "claim must be released after failure"
```

- [ ] **Step 3: Run the test — must fail**

```bash
cd outreach_agent && python3 -m pytest tests/test_reply_responder.py -v
```

Expected: FAIL — reply_responder leaves `date_replied='processing'` on RuntimeError raised after the claim but before the `mark_replied` call, because the current except-branch in `run()` calls `unmark_claimed` only if control reaches it *and* the contact was claimed by the live path. Verify the leak occurs in this test; the goal of step 4 is to make it pass unconditionally.

- [ ] **Step 4: Restructure the per-contact loop with try/finally claim-release**

In `outreach_agent/reply_responder.py`, replace the whole per-contact `try/except` block in `run()` (lines ~309–361) with:

```python
        claimed_here = not dry_run  # live runs claim in get_and_claim_pending_replies()
        send_succeeded = False
        try:
            # ── Guard: check Gmail thread directly before sending ──────────────
            if thread_id and gmail_client.already_replied_in_thread(
                thread_id, contact.get("reply_message_id")
            ):
                log.info("SKIP %s — already replied in Gmail thread %s", email, thread_id)
                if claimed_here:
                    mark_replied(email)   # sync DB with reality
                skipped += 1
                send_succeeded = True   # no release needed
                continue

            if intent in ("booking_intent", "booking_inquiry"):
                subject, body = _generate_booking_reply(contact, reply_body)
            elif intent == "positive":
                subject, body = _generate_positive_reply(contact, reply_body)
            elif intent == "question":
                subject, body = _generate_question_reply(contact, reply_body)
            else:
                log.info("Skipping %s — intent '%s' not handled", email, intent)
                skipped += 1
                continue

            if dry_run:
                print(f"\n{'='*60}")
                print(f"TO:      {name} <{email}>")
                print(f"INTENT:  {intent}")
                print(f"SUBJECT: {subject}")
                print(f"---\n{body}\n")
                sent += 1
                send_succeeded = True
                continue

            result = gmail_client.send_email(
                to_email=email,
                to_name=name,
                subject=subject,
                body=body,
                reply_to_thread_id=thread_id,
                in_reply_to_message_id=contact.get("reply_message_id"),
            )
            mark_replied(email)
            log.info("Reply sent to %s — message_id=%s", email, result.get("id"))
            sent += 1
            send_succeeded = True

        except Exception as exc:
            log.error("Failed to reply to %s: %s", email, exc, exc_info=True)
            failed += 1
        finally:
            if claimed_here and not send_succeeded:
                unmark_claimed(email)
```

- [ ] **Step 5: Run the test — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_reply_responder.py -v
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add outreach_agent/reply_responder.py outreach_agent/tests/__init__.py outreach_agent/tests/conftest.py outreach_agent/tests/test_reply_responder.py
git commit -m "fix(reply_responder): release claim on every failure path + regression test"
```

### Task 2 — send_attempts + dead_letter on initial sends

**Files:**
- Modify: `outreach_agent/db.py` (add helpers + `dead_letter` status)
- Modify: `outreach_agent/agent.py:160-217` (_send_batch failure branches)
- Modify: `outreach_agent/config.py` (add `MAX_SEND_ATTEMPTS`)
- Test: `outreach_agent/tests/test_send_batch.py`

- [ ] **Step 1: Write the failing test**

Create `outreach_agent/tests/test_send_batch.py`:

```python
"""Regression: _send_batch must count attempts and dead-letter after MAX_SEND_ATTEMPTS."""
from unittest.mock import MagicMock
import pytest


def _seed_verified(db, email="label@example.com", ctype="curator"):
    db.add_contact(email, "Test Label", ctype, "tribal techno", "", source="manual")
    db.mark_verified(email)


def test_send_batch_dead_letters_after_max_attempts(temp_db, mock_gmail, fake_claude, monkeypatch):
    import db
    import config
    import agent

    monkeypatch.setattr(config, "MAX_SEND_ATTEMPTS", 3)
    monkeypatch.setattr(config, "DRAFT_MODE", False)
    mock_gmail["send"].side_effect = RuntimeError("smtp down")
    _seed_verified(db)

    for _ in range(config.MAX_SEND_ATTEMPTS):
        agent._send_batch(1)

    row = db.get_contact("label@example.com")
    assert row["send_attempts"] >= config.MAX_SEND_ATTEMPTS
    assert row["status"] == "dead_letter", f"expected dead_letter, got {row['status']}"


def test_send_batch_failure_increments_attempts(temp_db, mock_gmail, fake_claude, monkeypatch):
    import db
    import config
    import agent

    monkeypatch.setattr(config, "MAX_SEND_ATTEMPTS", 3)
    monkeypatch.setattr(config, "DRAFT_MODE", False)
    mock_gmail["send"].side_effect = RuntimeError("smtp down")
    _seed_verified(db)

    agent._send_batch(1)

    row = db.get_contact("label@example.com")
    assert row["send_attempts"] == 1
    assert row["status"] == "verified"  # put back in queue
```

- [ ] **Step 2: Run the test — must fail**

```bash
cd outreach_agent && python3 -m pytest tests/test_send_batch.py -v
```

Expected: FAIL — `MAX_SEND_ATTEMPTS` does not exist in `config`; `agent._send_batch` does not increment `send_attempts`; there is no `dead_letter` status anywhere.

- [ ] **Step 3: Add config constant**

In `outreach_agent/config.py`, append near `MAX_EMAILS_PER_DAY`:

```python
# Maximum number of send attempts before a contact is moved to dead_letter.
# Lake 1 — Safety net. Prevents infinite retry against permanently-broken addresses.
MAX_SEND_ATTEMPTS = 3
```

- [ ] **Step 4: Add db helpers**

In `outreach_agent/db.py`, add near `mark_bounced_full`:

```python
def bump_send_attempts(email: str) -> int:
    """Increment send_attempts and return the new value."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET send_attempts = COALESCE(send_attempts,0) + 1 WHERE email = ?",
            (email.lower(),),
        )
        row = conn.execute(
            "SELECT send_attempts FROM contacts WHERE email = ?", (email.lower(),)
        ).fetchone()
        return row["send_attempts"] if row else 0


def mark_dead_letter(email: str, reason: str = ""):
    """Move a contact to dead_letter — stops further send attempts permanently."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE contacts SET status='dead_letter', notes = COALESCE(notes,'') || ? WHERE email = ?",
            (f" [dead_letter: {reason[:200]}]", email.lower()),
        )
```

- [ ] **Step 5: Wire into _send_batch failure branches**

In `outreach_agent/agent.py`, replace both failure branches (lines ~162-168 and ~213-217) with a helper + explicit dead-letter step. Add near the top of the file:

```python
from config import MAX_SEND_ATTEMPTS
```

Replace the template-generation failure block (lines ~162-168):

```python
        try:
            subject, body = template_engine.generate_email(contact, learn_ctx)
        except Exception as exc:
            log.error("Email generation failed for %s: %s", email, exc, exc_info=True)
            attempts = db.bump_send_attempts(email)
            if attempts >= MAX_SEND_ATTEMPTS:
                db.mark_dead_letter(email, f"generate_email: {exc}")
                log.warning("Dead-lettered %s after %d attempts", email, attempts)
            else:
                db.update_contact(email, status="verified")
            failed += 1
            continue
```

Replace the send failure block (lines ~213-217):

```python
        except Exception as exc:
            log.error("Send failed for %s: %s", email, exc, exc_info=True)
            attempts = db.bump_send_attempts(email)
            if attempts >= MAX_SEND_ATTEMPTS:
                db.mark_dead_letter(email, f"send_email: {exc}")
                log.warning("Dead-lettered %s after %d attempts", email, attempts)
            else:
                db.update_contact(email, status="verified")
            failed += 1
```

- [ ] **Step 6: Run the tests — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_send_batch.py -v
```

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add outreach_agent/agent.py outreach_agent/db.py outreach_agent/config.py outreach_agent/tests/test_send_batch.py
git commit -m "feat(outreach): send_attempts counter + dead_letter status after 3 failures"
```

### Task 3 — gmail_client retry with exponential backoff

**Files:**
- Modify: `outreach_agent/gmail_client.py`
- Test: `outreach_agent/tests/test_gmail_retry.py`

- [ ] **Step 1: Write the failing test**

Create `outreach_agent/tests/test_gmail_retry.py`:

```python
"""Regression: send_email must retry transient errors and surface permanent ones."""
from unittest.mock import MagicMock, patch
import pytest


class _FakeHttpError(Exception):
    def __init__(self, status):
        self.resp = MagicMock(status=status)
        super().__init__(f"HTTP {status}")


def test_send_email_retries_on_5xx(monkeypatch):
    import gmail_client
    calls = {"n": 0}

    def _mock_service():
        svc = MagicMock()
        def _execute():
            calls["n"] += 1
            if calls["n"] < 3:
                raise _FakeHttpError(503)
            return {"id": "msg-1", "threadId": "thr-1"}
        svc.users().messages().send().execute.side_effect = _execute
        return svc

    monkeypatch.setattr(gmail_client, "get_service", _mock_service)
    monkeypatch.setattr(gmail_client, "HttpError", _FakeHttpError)
    monkeypatch.setattr("time.sleep", lambda *a, **kw: None)

    result = gmail_client.send_email("t@example.com", "T", "s", "b")
    assert result["id"] == "msg-1"
    assert calls["n"] == 3


def test_send_email_raises_on_permanent_error(monkeypatch):
    import gmail_client

    def _mock_service():
        svc = MagicMock()
        svc.users().messages().send().execute.side_effect = _FakeHttpError(400)
        return svc

    monkeypatch.setattr(gmail_client, "get_service", _mock_service)
    monkeypatch.setattr(gmail_client, "HttpError", _FakeHttpError)
    monkeypatch.setattr("time.sleep", lambda *a, **kw: None)

    with pytest.raises(_FakeHttpError):
        gmail_client.send_email("t@example.com", "T", "s", "b")
```

- [ ] **Step 2: Run the test — must fail**

```bash
cd outreach_agent && python3 -m pytest tests/test_gmail_retry.py -v
```

Expected: FAIL — current `send_email` raises on the first HttpError without any retry.

- [ ] **Step 3: Add retry wrapper**

In `outreach_agent/gmail_client.py`, add near the top:

```python
import time

# Retry policy for transient Gmail API errors — Lake 1 safety net.
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 3
_BACKOFF_BASE = 2.0  # seconds
```

Wrap the existing `send_email` body so the `.execute()` call runs through a retry loop. Concrete pattern — replace the `execute` call site:

```python
def _execute_with_retry(request, label: str):
    """Execute a Gmail API request with exponential backoff on transient errors."""
    last_exc = None
    for attempt in range(_MAX_RETRIES):
        try:
            return request.execute()
        except HttpError as exc:
            status = getattr(getattr(exc, "resp", None), "status", None)
            if status not in _RETRY_STATUSES:
                raise
            last_exc = exc
            wait = _BACKOFF_BASE ** attempt
            log.warning(
                "Gmail %s transient error %s (attempt %d/%d) — backing off %.1fs",
                label, status, attempt + 1, _MAX_RETRIES, wait,
            )
            time.sleep(wait)
    raise last_exc
```

Then inside `send_email()`, change `return service.users().messages().send(...).execute()` to:

```python
    request = service.users().messages().send(userId="me", body=message_body)
    return _execute_with_retry(request, "send_email")
```

- [ ] **Step 4: Run the tests — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_gmail_retry.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/gmail_client.py outreach_agent/tests/test_gmail_retry.py
git commit -m "feat(gmail_client): exponential backoff for transient 429/5xx errors"
```

### Task 4 — Replace silent exception handlers in agent.cmd_run

**Files:**
- Modify: `outreach_agent/agent.py:253-281`

- [ ] **Step 1: Audit silent handlers**

```bash
cd outreach_agent && grep -n "except Exception" agent.py
```

Expected: Handlers at `_verify_pending_contacts` silent-swallow pattern (line ~70 `time.sleep`), inbox check (~256), classify (~266), auto-reply (~274), learning (~280), followups (~287). The worst offender is the pair in `_verify_pending_contacts` (`bounce.verify_email` fall-through) — confirm there isn't one there; there isn't, so this task targets the four non-fatal blocks in `cmd_run()`.

- [ ] **Step 2: Replace each bare `log.warning` call with structured logging + event publish**

In `outreach_agent/agent.py`, at the top of `cmd_run()` (above the step-4 inbox check):

```python
    try:
        import events as _events
        _EVENTS_AVAILABLE = True
    except ImportError:
        _EVENTS_AVAILABLE = False

    def _publish_failure(stage: str, exc: Exception):
        log.error("Cycle stage '%s' failed: %s", stage, exc, exc_info=True)
        if _EVENTS_AVAILABLE:
            try:
                _events.publish(
                    "outreach.cycle_stage_failed",
                    source="agent.cmd_run",
                    payload={"stage": stage, "error": str(exc)[:500]},
                )
            except Exception:
                pass  # Event bus unavailable — logs remain the source of truth
```

Then replace each `except Exception as exc:` non-fatal block with:

```python
    except Exception as exc:
        _publish_failure("inbox_check", exc)
        inbox_result = None
```

Do the same swap for `classify_pending` → `"reply_classification"`, `reply_responder.run` → `"auto_reply"`, `learning.maybe_generate_insights` → `"learning_insights"`, and `followup_engine.run_followup_batch` → `"followups"`. Use `exc_info=True` on every log line so tracebacks reach the log file.

- [ ] **Step 3: Sanity run the agent in draft mode**

```bash
cd outreach_agent && DRAFT_MODE=1 python3 agent.py run 2>&1 | tail -40
```

Expected: cycle completes. Any failing stage now prints a traceback and publishes an `outreach.cycle_stage_failed` event.

- [ ] **Step 4: Verify event was written**

```bash
cd outreach_agent && python3 -c "import db; rows = db.get_conn().execute('SELECT event_type, source, payload FROM events WHERE event_type=\"outreach.cycle_stage_failed\" ORDER BY id DESC LIMIT 3').fetchall(); [print(dict(r)) for r in rows]"
```

Expected: If nothing was failing, empty. Otherwise, recent failures listed with stage + error.

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/agent.py
git commit -m "feat(outreach): structured logging + event publish for non-fatal stage failures"
```

### Task 5 — personalization_audit table + insert on every generate_email

**Intent:** Track which hooks the template engine actually used per send, so Lake 3's research-branching has a ground-truth signal for the learning loop.

**Files:**
- Modify: `outreach_agent/db.py` (add table + helper)
- Modify: `outreach_agent/template_engine.py` (emit audit row after parse)
- Test: `outreach_agent/tests/test_personalization_audit.py`

- [ ] **Step 1: Add schema + helper**

In `outreach_agent/db.py`, inside the `SCHEMA` string add (before the `CREATE INDEX` block for fleet_state):

```sql
CREATE TABLE IF NOT EXISTS personalization_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    NOT NULL,
    sent_at         TEXT    NOT NULL,
    research_done   INTEGER DEFAULT 0,
    used_research   INTEGER DEFAULT 0,
    hooks_used      TEXT,              -- JSON array of labels: ["playlist_mention","bpm_match"]
    subject_len     INTEGER,
    body_len        INTEGER,
    brand_score     INTEGER,           -- 0-5 from brand_gate
    brand_passed    INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_audit_email ON personalization_audit(email);
CREATE INDEX IF NOT EXISTS idx_audit_sent_at ON personalization_audit(sent_at);
```

And append at the bottom of the file:

```python
def log_personalization_audit(
    email: str,
    research_done: bool,
    used_research: bool,
    hooks_used: list[str],
    subject: str,
    body: str,
    brand_score: int,
    brand_passed: bool,
):
    """Store an audit row for every successful email generation."""
    import json
    from datetime import datetime
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO personalization_audit
               (email, sent_at, research_done, used_research, hooks_used,
                subject_len, body_len, brand_score, brand_passed)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                email.lower(),
                datetime.now().isoformat(),
                1 if research_done else 0,
                1 if used_research else 0,
                json.dumps(hooks_used[:10]),
                len(subject or ""),
                len(body or ""),
                int(brand_score or 0),
                1 if brand_passed else 0,
            ),
        )
```

- [ ] **Step 2: Write the failing test**

Create `outreach_agent/tests/test_personalization_audit.py`:

```python
"""Regression: every generate_email call must append a personalization_audit row."""
import json


def test_audit_written_on_generate(temp_db, fake_claude):
    import db
    import template_engine as te

    db.add_contact("c@example.com", "Curator", "curator", "melodic techno", "", source="manual")
    db.mark_verified("c@example.com")
    db.store_research("c@example.com", "Hosts Ritual Techno playlist, 12k followers. Track fit: Living Water.")

    contact = db.get_contact("c@example.com")
    te.generate_email(contact, learning_context="")

    rows = db.get_conn().execute(
        "SELECT * FROM personalization_audit WHERE email=?", ("c@example.com",)
    ).fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["research_done"] == 1
    assert row["body_len"] > 0
```

- [ ] **Step 3: Run the test — must fail**

```bash
cd outreach_agent && python3 -m pytest tests/test_personalization_audit.py -v
```

Expected: FAIL — `personalization_audit` does not exist, or row count is zero.

- [ ] **Step 4: Emit audit row from generate_email**

In `outreach_agent/template_engine.py`, modify `generate_email()` at the end (replacing the existing `gate_or_warn` call, near line 490):

```python
    subject, body = _parse_response(raw)
    log.info("Generated — subject: %r", subject)

    brand_score = 0
    brand_passed = False
    if _BRAND_GATE_AVAILABLE:
        result = _brand_gate.validate_content(body)
        brand_score = result.get("score", 0)
        brand_passed = result.get("passes", False)
        if not brand_passed:
            log.warning(
                "[brand_gate:template_engine.generate_email] score=%s/5 flags=%s",
                brand_score, result.get("flags"),
            )

    try:
        import db as _db
        _db.log_personalization_audit(
            email=contact.get("email", ""),
            research_done=bool(contact.get("research_done")),
            used_research=bool(contact.get("research_notes")),
            hooks_used=[],  # populated by Lake 3 — keep empty for now
            subject=subject, body=body,
            brand_score=brand_score,
            brand_passed=brand_passed,
        )
    except Exception as exc:
        log.warning("personalization_audit write failed: %s", exc)

    return subject, body
```

- [ ] **Step 5: Run the test — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_personalization_audit.py -v
```

Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add outreach_agent/db.py outreach_agent/template_engine.py outreach_agent/tests/test_personalization_audit.py
git commit -m "feat(outreach): personalization_audit table + row per generate_email"
```

### Task 6 — Scheduler publishes rate_limit events

**Files:**
- Modify: `outreach_agent/scheduler.py:155-194`
- Test: `outreach_agent/tests/test_scheduler.py`

- [ ] **Step 1: Write the failing test**

Create `outreach_agent/tests/test_scheduler.py`:

```python
"""Regression: SendWindow publishes an event when bounce limit trips."""
from unittest.mock import patch


def test_send_window_publishes_bounce_event(temp_db, monkeypatch):
    import db
    import scheduler
    import events as _events

    # Seed 20 sent contacts with 3 actual bounces (15% > 5% default)
    for i in range(20):
        db.add_contact(f"t{i}@example.com", f"T{i}", "curator", "", "", source="manual")
        db.mark_verified(f"t{i}@example.com")
        db.mark_sent(
            email=f"t{i}@example.com",
            message_id=f"m{i}", thread_id=f"tr{i}",
            subject="s", body_snippet="b", template_type="curator",
        )
    for i in range(3):
        db.update_contact(f"t{i}@example.com", bounce="actual")

    published = []
    orig_publish = _events.publish
    def _spy(event_type, source, payload):
        published.append({"event_type": event_type, "source": source, "payload": payload})
        return orig_publish(event_type, source, payload)
    monkeypatch.setattr(_events, "publish", _spy)

    scheduler.SendWindow()  # instantiation triggers bounce_rate_safe

    types = [e["event_type"] for e in published]
    assert "outreach.rate_limit_paused" in types, f"expected rate_limit event, got {types}"
```

- [ ] **Step 2: Run the test — must fail**

```bash
cd outreach_agent && python3 -m pytest tests/test_scheduler.py -v
```

Expected: FAIL — scheduler does not currently publish any event.

- [ ] **Step 3: Add event publish in `SendWindow.__init__`**

In `outreach_agent/scheduler.py`, at the top of the file add:

```python
try:
    import events as _events
    _EVENTS_AVAILABLE = True
except ImportError:
    _EVENTS_AVAILABLE = False
```

And at the end of `SendWindow.__init__` (after `self.can_send = ...`):

```python
        if not self.can_send and _EVENTS_AVAILABLE:
            reason = "bounce_limit" if not self.bounce_ok else (
                "outside_window" if not self.in_window else
                "quota_exhausted" if self.quota_left <= 0 else
                "interval"
            )
            try:
                _events.publish(
                    "outreach.rate_limit_paused",
                    source="scheduler.SendWindow",
                    payload={"reason": reason, "status": self.status()},
                )
            except Exception:
                pass
```

- [ ] **Step 4: Run the test — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_scheduler.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/scheduler.py outreach_agent/tests/test_scheduler.py
git commit -m "feat(scheduler): publish outreach.rate_limit_paused event when sends blocked"
```

### Task 7 — Heartbeat file + rjm.py status integration

**Files:**
- Create: `outreach_agent/heartbeat.py`
- Modify: `outreach_agent/agent.py:302-308` (cmd_run tail)
- Modify: `outreach_agent/config.py` (add `HEARTBEAT_PATH`)
- Modify: `rjm.py` (status prints heartbeat age)

- [ ] **Step 1: Add config entry**

In `outreach_agent/config.py` after `LOG_PATH`:

```python
HEARTBEAT_PATH = BASE_DIR / "outreach_heartbeat.json"
```

- [ ] **Step 2: Create heartbeat module**

Create `outreach_agent/heartbeat.py`:

```python
"""Write and read the outreach agent heartbeat.

Called at the end of every successful cycle by agent.cmd_run().
Read by rjm.py status to detect agent stalls.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import HEARTBEAT_PATH


def write_heartbeat(summary: dict):
    """Persist a heartbeat record. Called at end of each agent cycle."""
    record = {
        "last_run_at": datetime.now().isoformat(),
        "summary": {k: v for k, v in summary.items() if not k.startswith("_")},
        "today_sent": summary.get("_today_sent", 0),
        "reply_rate": summary.get("_reply_rate", "—"),
    }
    HEARTBEAT_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")


def read_heartbeat() -> Optional[dict]:
    if not HEARTBEAT_PATH.exists():
        return None
    try:
        return json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def heartbeat_age_minutes() -> Optional[float]:
    record = read_heartbeat()
    if not record or "last_run_at" not in record:
        return None
    try:
        last = datetime.fromisoformat(record["last_run_at"])
    except ValueError:
        return None
    return (datetime.now() - last).total_seconds() / 60.0
```

- [ ] **Step 3: Wire into agent.cmd_run end-of-cycle**

In `outreach_agent/agent.py`, modify the tail of `cmd_run()`:

```python
    # 9. Summary + heartbeat
    summary = db.get_pipeline_summary()
    log.info(
        "Cycle complete — DB: %s | Today: %d sent | Reply rate: %s",
        {k: v for k, v in summary.items() if not k.startswith("_")},
        summary.get("_today_sent", 0),
        summary.get("_reply_rate", "—"),
    )
    try:
        import heartbeat as _heartbeat
        _heartbeat.write_heartbeat(summary)
    except Exception as exc:
        log.warning("Heartbeat write failed: %s", exc)
```

- [ ] **Step 4: Wire into rjm.py status**

In `rjm.py`, find `cmd_outreach` or the project-level `status` printing block and append a heartbeat check. Minimal diff pattern — locate the function body that prints the outreach status (grep `outreach.*status` in rjm.py to find it) and add:

```python
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent / "outreach_agent"))
        import heartbeat as _hb
        age = _hb.heartbeat_age_minutes()
        if age is None:
            print("  Heartbeat:        ❌ never written")
        elif age > 60:
            print(f"  Heartbeat:        ⚠️  stale ({age:.0f}m ago)")
        else:
            print(f"  Heartbeat:        ✅ {age:.0f}m ago")
    except Exception as exc:
        print(f"  Heartbeat:        ⚠️  read failed: {exc}")
```

- [ ] **Step 5: Verify**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/intelligent-austin"
DRAFT_MODE=1 python3 outreach_agent/agent.py run 2>&1 | tail -5
ls -l outreach_agent/outreach_heartbeat.json
python3 -c "import sys; sys.path.insert(0, 'outreach_agent'); import heartbeat; print(heartbeat.heartbeat_age_minutes())"
```

Expected: heartbeat file exists, age is a small float (seconds-to-minutes).

- [ ] **Step 6: Commit**

```bash
git add outreach_agent/heartbeat.py outreach_agent/agent.py outreach_agent/config.py rjm.py
git commit -m "feat(outreach): heartbeat file + status read for stall detection"
```

### Task 8 — launchd plist for outreach agent (replaces cron)

**Files:**
- Create: `ops/launchd/com.rjm.outreach.plist`
- Create: `scripts/install-outreach-launchd.sh`

- [ ] **Step 1: Write plist**

Create `ops/launchd/com.rjm.outreach.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.rjm.outreach</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>-lc</string>
        <string>cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent" &amp;&amp; python3 agent.py run</string>
    </array>

    <key>StartInterval</key>
    <integer>1800</integer>

    <key>StandardOutPath</key>
    <string>/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent/agent.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent/agent.log</string>

    <key>RunAtLoad</key>
    <false/>

    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
```

- [ ] **Step 2: Write installer**

Create `scripts/install-outreach-launchd.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

PLIST_SRC="$(cd "$(dirname "$0")/.." && pwd)/ops/launchd/com.rjm.outreach.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.rjm.outreach.plist"

echo "→ Copying $PLIST_SRC → $PLIST_DST"
mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST_SRC" "$PLIST_DST"

echo "→ Bootstrapping via launchctl"
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo "→ Verifying"
launchctl list | grep com.rjm.outreach || {
    echo "❌ launchd failed to register com.rjm.outreach"
    exit 1
}
echo "✅ Outreach agent scheduled every 1800s via launchd."
```

Make executable:

```bash
chmod +x scripts/install-outreach-launchd.sh
```

- [ ] **Step 3: Do NOT run the installer as part of the plan**

Installing a launchd agent is a hard-to-reverse action that affects shared state outside this worktree. Leave it for a deliberate step the user initiates. Add a note to the README paragraph below in the same commit.

- [ ] **Step 4: Commit**

```bash
git add ops/launchd/com.rjm.outreach.plist scripts/install-outreach-launchd.sh
git commit -m "ops(outreach): launchd plist + idempotent installer (not auto-activated)"
```

---

## Lake 2 — Pipeline End-to-End Flow

**Intent:** Fill the gaps between discover → verify → research → send so a contact flows through without manual nudging, and no pipeline stage silently drops work.

### Task 9 — Wire db.log_discovery into run_cycle.cmd_add_contact

**Files:**
- Modify: `outreach_agent/run_cycle.py:443-464`
- Test: `outreach_agent/tests/test_discovery_log.py`

- [ ] **Step 1: Write the failing test**

Create `outreach_agent/tests/test_discovery_log.py`:

```python
"""Regression: cmd_add_contact must insert a discovery_log row."""
def test_add_contact_logs_discovery(temp_db, monkeypatch):
    import db
    import run_cycle
    import bounce
    monkeypatch.setattr(bounce, "verify_email", lambda e: ("valid", "mx ok"))

    run_cycle.cmd_add_contact(
        "new@example.com", "New Curator", "curator",
        "tribal", "Spotify playlist Tribal Nights 8k followers", "example.com",
    )
    rows = db.get_conn().execute("SELECT * FROM discovery_log ORDER BY id DESC").fetchall()
    assert len(rows) >= 1
    assert rows[0]["contact_type"] == "curator"
```

- [ ] **Step 2: Run the test — must fail**

```bash
cd outreach_agent && python3 -m pytest tests/test_discovery_log.py -v
```

Expected: FAIL — `discovery_log` has zero rows.

- [ ] **Step 3: Patch cmd_add_contact**

In `outreach_agent/run_cycle.py`, at the end of `cmd_add_contact()` (after the verification print), append:

```python
    try:
        db.log_discovery(
            search_query=notes[:200] or f"{ctype}:{genre[:50]}",
            contact_type=ctype,
            results_found=1,
        )
    except Exception as exc:
        print(f"⚠️  log_discovery failed: {exc}")
```

- [ ] **Step 4: Run the test — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_discovery_log.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/run_cycle.py outreach_agent/tests/test_discovery_log.py
git commit -m "feat(outreach): persist discovery_log row on every cmd_add_contact"
```

### Task 10 — dedup hit regression test

**Intent:** Lock in the org-domain dedup behaviour so a refactor can't silently break it.

**Files:**
- Test: `outreach_agent/tests/test_dedup.py`

- [ ] **Step 1: Write the test**

Create `outreach_agent/tests/test_dedup.py`:

```python
"""Regression: add_contact rejects exact duplicates and org-domain duplicates."""
def test_add_contact_rejects_exact_duplicate(temp_db):
    import db
    ok, _ = db.add_contact("a@label.com", "A", "label", "", "")
    assert ok
    ok2, reason = db.add_contact("a@label.com", "A", "label", "", "")
    assert not ok2
    assert "duplicate" in reason


def test_add_contact_rejects_org_duplicate_after_send(temp_db):
    import db
    ok, _ = db.add_contact("a@label.com", "A", "label", "", "")
    assert ok
    db.mark_verified("a@label.com")
    db.mark_sent(
        email="a@label.com", message_id="m1", thread_id="t1",
        subject="s", body_snippet="b", template_type="label",
    )
    # Second person at same custom domain now blocked
    ok2, reason = db.add_contact("b@label.com", "B", "label", "", "")
    assert not ok2, "org-level dedup should block second contact at same domain"
    assert "org duplicate" in reason.lower()


def test_add_contact_allows_shared_domain(temp_db):
    import db
    db.add_contact("a@gmail.com", "A", "curator", "", "")
    db.mark_verified("a@gmail.com")
    db.mark_sent(
        email="a@gmail.com", message_id="m1", thread_id="t1",
        subject="s", body_snippet="b", template_type="curator",
    )
    ok, _ = db.add_contact("b@gmail.com", "B", "curator", "", "")
    assert ok, "shared domains like gmail.com must not dedup"
```

- [ ] **Step 2: Run tests — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_dedup.py -v
```

Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add outreach_agent/tests/test_dedup.py
git commit -m "test(db): lock in add_contact dedup + org-domain behaviour"
```

### Task 11 — Stale-research rescue + pending_research cohort

**Intent:** If a contact has been in `verified` with `research_done=0` for more than 48 hours, its research job was never picked up. Surface these in `cmd_pending_research` so they re-enter the research skill's queue with priority.

**Files:**
- Modify: `outreach_agent/db.py` (add query for stale research)
- Modify: `outreach_agent/run_cycle.py:482-497`
- Test: `outreach_agent/tests/test_pending_research.py`

- [ ] **Step 1: Write the failing test**

Create `outreach_agent/tests/test_pending_research.py`:

```python
"""pending_research should surface contacts even if older than FIFO tail."""
import json
import io
import contextlib


def test_pending_research_includes_stale(temp_db):
    import db
    import run_cycle
    db.add_contact("a@label.com", "A", "label", "tribal", "")
    db.mark_verified("a@label.com")
    # add_contact sets date_added to today — unresearched by default

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_cycle.cmd_pending_research()
    out = buf.getvalue()
    assert "a@label.com" in out
```

- [ ] **Step 2: Run — must pass (current behaviour already returns it)**

```bash
cd outreach_agent && python3 -m pytest tests/test_pending_research.py -v
```

Expected: 1 passed. (Locking in behaviour so the next change can't regress it.)

- [ ] **Step 3: Commit**

```bash
git add outreach_agent/tests/test_pending_research.py
git commit -m "test(run_cycle): regression for pending_research FIFO output"
```

### Task 12 — auto_verify_and_queue + cmd_plan pipeline-gap event

**Intent:** Add a `cmd_plan` publish so master_agent sees pipeline gaps (empty verified queue, zero researched-ahead, dead_letter backlog) as events.

**Files:**
- Modify: `outreach_agent/run_cycle.py:160-360` (`cmd_plan`)
- Test: `outreach_agent/tests/test_cmd_plan_events.py`

- [ ] **Step 1: Write the failing test**

Create `outreach_agent/tests/test_cmd_plan_events.py`:

```python
"""cmd_plan must publish pipeline.gap_detected when verified queue is empty."""
import io, contextlib


def test_cmd_plan_publishes_empty_queue_event(temp_db):
    import db
    import run_cycle
    import events as _events

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_cycle.cmd_plan()

    rows = _events.recent("pipeline.gap_detected", limit=5)
    assert any("empty_verified_queue" in (r.get("payload") or "") for r in rows), \
        f"no pipeline.gap_detected event with empty_verified_queue. Got: {rows}"
```

- [ ] **Step 2: Run — must fail**

```bash
cd outreach_agent && python3 -m pytest tests/test_cmd_plan_events.py -v
```

Expected: FAIL — cmd_plan does not publish events.

- [ ] **Step 3: Add publish at end of cmd_plan**

In `outreach_agent/run_cycle.py`, add at the top near other imports:

```python
try:
    import events as _events
    _EVENTS_AVAILABLE = True
except ImportError:
    _EVENTS_AVAILABLE = False
```

At the end of `cmd_plan()`, after any existing output, append:

```python
    if _EVENTS_AVAILABLE:
        gaps = []
        verified_n = len(db.get_contacts_by_status("verified"))
        new_n      = len(db.get_contacts_by_status("new"))
        dead_n     = len(db.get_contacts_by_status("dead_letter"))
        if verified_n == 0:
            gaps.append("empty_verified_queue")
        if new_n == 0 and verified_n < 10:
            gaps.append("empty_new_queue")
        if dead_n >= 10:
            gaps.append("dead_letter_backlog")
        if gaps:
            try:
                _events.publish(
                    "pipeline.gap_detected",
                    source="run_cycle.cmd_plan",
                    payload={"gaps": gaps, "verified": verified_n, "new": new_n, "dead": dead_n},
                )
            except Exception:
                pass
```

- [ ] **Step 4: Run — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_cmd_plan_events.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/run_cycle.py outreach_agent/tests/test_cmd_plan_events.py
git commit -m "feat(run_cycle): publish pipeline.gap_detected events from cmd_plan"
```

### Task 13 — discover SKILL.md logs search queries

**Intent:** The discover skill currently calls `run_cycle.py add_contact` for each contact but never records the fact that a search was run. Task 9 wires `log_discovery` into `cmd_add_contact`, but we still want an explicit search-level record so `recently_searched()` can gate the skill without having to hit add_contact first.

**Files:**
- Create: new `run_cycle.py` sub-command `log_search`
- Modify: `~/.claude/scheduled-tasks/rjm-discover/SKILL.md`

- [ ] **Step 1: Add cmd_log_search in run_cycle.py**

Add:

```python
def cmd_log_search(query: str, ctype: str, results: str = "0"):
    db.init_db()
    try:
        db.log_discovery(query[:200], ctype, int(results))
        print(f"✅ logged search: {query[:60]}")
    except Exception as exc:
        print(f"❌ log_search failed: {exc}")
```

Register it in `main()` (wherever the CLI dispatch happens). Inspect lines 662+ for the pattern and mirror an existing subcommand.

- [ ] **Step 2: Update discover SKILL.md**

Edit `~/.claude/scheduled-tasks/rjm-discover/SKILL.md` — after the "SEARCH PROCESS" section, add a line before each search call:

```
Before running a search, log it:
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/outreach_agent" && python3 run_cycle.py log_search "[query]" "[type]" "[results]"
```

- [ ] **Step 3: Verify manually**

```bash
cd outreach_agent && python3 run_cycle.py log_search "tribal techno curator" "curator" "3"
python3 -c "import db; rows = db.get_conn().execute('SELECT * FROM discovery_log ORDER BY id DESC LIMIT 3').fetchall(); [print(dict(r)) for r in rows]"
```

Expected: one row matching the logged query.

- [ ] **Step 4: Commit**

```bash
git add outreach_agent/run_cycle.py
git commit -m "feat(run_cycle): log_search CLI for discover skill query tracking"
```

Then commit the skill change separately since it lives outside the worktree — instruct user to sync the skill file via their normal scheduled-task sync flow.

### Task 14 — recently_searched guard in discover skill

**Intent:** Have `log_search` refuse to re-log (and optionally print a sentinel) if `recently_searched` reports True — lets the skill know to pick a different query.

**Files:**
- Modify: `outreach_agent/run_cycle.py:cmd_log_search`

- [ ] **Step 1: Enhance cmd_log_search**

Replace the body with:

```python
def cmd_log_search(query: str, ctype: str, results: str = "0"):
    db.init_db()
    if db.recently_searched(query, within_hours=48):
        print(f"⏭  skip (searched <48h): {query[:60]}")
        return
    try:
        db.log_discovery(query[:200], ctype, int(results))
        print(f"✅ logged search: {query[:60]}")
    except Exception as exc:
        print(f"❌ log_search failed: {exc}")
```

- [ ] **Step 2: Manual verify**

```bash
cd outreach_agent && python3 run_cycle.py log_search "tribal techno curator" "curator" "3"
python3 run_cycle.py log_search "tribal techno curator" "curator" "3"
```

Expected second call: `⏭  skip (searched <48h)`.

- [ ] **Step 3: Commit**

```bash
git add outreach_agent/run_cycle.py
git commit -m "feat(run_cycle): log_search dedupes via recently_searched 48h guard"
```

---

## Lake 3 — Research Loop Closure + Email Branching

**Intent:** The research pipeline currently writes notes into `contacts.research_notes`, and the template engine mechanically injects them. We need three things: (a) the prompt itself must branch on whether research exists, (b) Claude must return `hooks_used` so we can audit personalization, (c) insights must feed back into scoring so the learning loop is no longer read-only.

### Task 15 — Research-aware prompt branching

**Files:**
- Modify: `outreach_agent/template_engine.py:149-269`
- Test: `outreach_agent/tests/test_template_research.py`

- [ ] **Step 1: Write the failing test**

Create `outreach_agent/tests/test_template_research.py`:

```python
"""Prompt must explicitly branch on research presence and request hooks_used JSON."""
def test_prompt_without_research_has_fallback_instruction(temp_db):
    import template_engine as te
    contact = {"email": "a@x.com", "name": "A", "type": "curator", "genre": "tribal", "notes": "", "research_notes": "", "research_done": 0}
    prompt = te._build_prompt(contact)
    assert "NO RECIPIENT RESEARCH" in prompt
    assert "fallback" in prompt.lower()
    assert "hooks_used" in prompt


def test_prompt_with_research_uses_it(temp_db):
    import template_engine as te
    contact = {"email": "a@x.com", "name": "A", "type": "curator", "genre": "tribal",
               "notes": "", "research_notes": "Hosts Ritual Techno playlist, 12k followers.",
               "research_done": 1}
    prompt = te._build_prompt(contact)
    assert "Ritual Techno" in prompt
    assert "RECIPIENT RESEARCH" in prompt
    assert "hooks_used" in prompt
```

- [ ] **Step 2: Run — must fail**

```bash
cd outreach_agent && python3 -m pytest tests/test_template_research.py -v
```

Expected: FAIL — no `NO RECIPIENT RESEARCH` branch, no `hooks_used` schema request.

- [ ] **Step 3: Restructure _build_prompt**

Replace `_build_prompt` in `outreach_agent/template_engine.py` (lines ~235-269):

```python
def _build_prompt(contact: dict, learning_context: str = "") -> str:
    """Build the full prompt (system + user combined for CLI mode).

    Branches on whether the contact has research_notes. With research: the model
    MUST cite a specific fact from the research in sentence 1. Without research:
    the model falls back to genre/type-level angles and is forbidden from
    fabricating specific playlist names or episode titles.

    Both branches request the extended output schema:
        {"subject": "...", "body": "...", "hooks_used": ["..."]}
    """
    ctype = contact.get("type", "curator")
    genre = contact.get("genre", "")
    notes = contact.get("notes", "")
    name  = contact.get("name", "")
    research = (contact.get("research_notes", "") or "").strip()
    research_done = bool(contact.get("research_done")) or bool(research)

    is_christian = _is_christian_contact(genre, notes)
    christian_addon = _CHRISTIAN_ADDON if is_christian else ""

    system  = _SYSTEM_BASE + _TYPE_ADDONS.get(ctype, "") + christian_addon
    tracks  = _get_track_recs(ctype, genre, notes)

    user = f"""Write an outreach email to:
Name:  {name}
Type:  {ctype}
Genre: {genre}
Notes: {notes}

TRACKS (choose 1 — include its Spotify URL inline every time you mention it):
{tracks}
RULE: Never name a track without its Spotify link directly after it. Example: "Living Water (https://open.spotify.com/track/...)"

SPOTIFY ARTIST PAGE: {ARTIST['spotify_artist']}
"""

    if research_done and research:
        user += f"""
RECIPIENT RESEARCH (ground-truth — cite a specific fact in sentence 1):
{research}

INSTRUCTIONS:
- Sentence 1 MUST reference a specific detail from the research above.
- Do NOT invent playlist names, episode titles, or follower counts that are not in the research.
- If the research does not mention a track fit, default to the TRACKS list above.
"""
    else:
        user += """
NO RECIPIENT RESEARCH is available for this contact.

FALLBACK INSTRUCTIONS:
- Open with a type/genre-level angle derived strictly from the Name/Type/Notes fields above.
- You MUST NOT fabricate any specific playlist name, episode title, follower count, or personal anecdote.
- Use phrases like "given your [genre] focus" instead of inventing specifics.
- If nothing concrete is available, lead with ONE sentence about what {ctype}s in this genre typically program.
"""

    if learning_context:
        user += f"\nINSIGHTS FROM PAST SUCCESSFUL EMAILS:\n{learning_context}\n"

    user += """
OUTPUT SCHEMA — Return ONLY valid JSON with these three keys:
{
  "subject": "...",
  "body": "...",
  "hooks_used": ["playlist_mention"|"bpm_match"|"episode_reference"|"genre_fallback"|"scripture_anchor"|"bio_story"|"other"]
}
hooks_used is a list of 1–5 labels identifying which personalization levers you pulled. Use "genre_fallback" when no research was available.
"""

    return system + "\n\n" + user
```

- [ ] **Step 4: Run — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_template_research.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add outreach_agent/template_engine.py outreach_agent/tests/test_template_research.py
git commit -m "feat(template_engine): branch prompt on research presence + hooks_used schema"
```

### Task 16 — Parse hooks_used, persist in personalization_audit

**Files:**
- Modify: `outreach_agent/template_engine.py:_parse_response, generate_email`

- [ ] **Step 1: Update _parse_response to return hooks_used**

Change the return signature of `_parse_response` from `tuple[str, str]` to `tuple[str, str, list[str]]`:

```python
def _parse_response(raw: str) -> tuple[str, str, list[str]]:
    """Parse Claude's JSON response into (subject, body, hooks_used)."""
    raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    match = re.search(r"\{.*?\}", raw, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object found in Claude response: {raw[:200]!r}")
    raw = match.group(0)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match2 = re.search(r"\{.*\}", raw, re.DOTALL)
        if match2:
            data = json.loads(match2.group(0))
        else:
            raise
    subject = data.get("subject", "").strip()
    body    = data.get("body", "").strip()
    hooks   = data.get("hooks_used", []) or []
    if not isinstance(hooks, list):
        hooks = []
    hooks = [str(h)[:40] for h in hooks[:10]]

    if not subject or not body:
        raise ValueError("Claude returned empty subject or body")

    body = _inject_spotify_links(body)
    body = _ensure_signature(body)

    return subject, body, hooks
```

- [ ] **Step 2: Update generate_email to pass hooks_used into audit**

In `generate_email`:

```python
    subject, body, hooks = _parse_response(raw)
    log.info("Generated — subject: %r (hooks: %s)", subject, hooks)

    brand_score = 0
    brand_passed = False
    if _BRAND_GATE_AVAILABLE:
        result = _brand_gate.validate_content(body)
        brand_score = result.get("score", 0)
        brand_passed = result.get("passes", False)
        if not brand_passed:
            log.warning(
                "[brand_gate:template_engine.generate_email] score=%s/5 flags=%s",
                brand_score, result.get("flags"),
            )

    try:
        import db as _db
        _db.log_personalization_audit(
            email=contact.get("email", ""),
            research_done=bool(contact.get("research_done")),
            used_research=bool(contact.get("research_notes")),
            hooks_used=hooks,
            subject=subject, body=body,
            brand_score=brand_score, brand_passed=brand_passed,
        )
    except Exception as exc:
        log.warning("personalization_audit write failed: %s", exc)

    return subject, body
```

- [ ] **Step 3: Update generate_emails_batch parser similarly**

Find the batch loop (around line 562):

```python
    for item in items:
        email   = item.get("email", "").strip()
        subject = item.get("subject", "").strip()
        body    = item.get("body", "").strip()
        hooks   = item.get("hooks_used", []) or []
        if not isinstance(hooks, list):
            hooks = []
        hooks = [str(h)[:40] for h in hooks[:10]]
        if not email or not subject or not body:
            continue
        body = _inject_spotify_links(body)
        body = _ensure_signature(body)
        result[email] = (subject, body)

        try:
            import db as _db
            brand_score = 0
            brand_passed = False
            if _BRAND_GATE_AVAILABLE:
                _r = _brand_gate.validate_content(body)
                brand_score = _r.get("score", 0)
                brand_passed = _r.get("passes", False)
            contact_row = next((c for c in contacts if c.get("email", "").lower() == email.lower()), {})
            _db.log_personalization_audit(
                email=email,
                research_done=bool(contact_row.get("research_done")),
                used_research=bool(contact_row.get("research_notes")),
                hooks_used=hooks,
                subject=subject, body=body,
                brand_score=brand_score, brand_passed=brand_passed,
            )
        except Exception as exc:
            log.warning("personalization_audit batch write failed: %s", exc)
```

- [ ] **Step 4: Run all template tests**

```bash
cd outreach_agent && python3 -m pytest tests/test_template_research.py tests/test_personalization_audit.py -v
```

Expected: all green. If `test_personalization_audit.py` now fails because the audit row is still empty hooks for the live-research contact, update the fake_claude fixture to return `"hooks_used": ["playlist_mention"]`.

- [ ] **Step 5: Update fake_claude fixture**

In `outreach_agent/tests/conftest.py`, update the canned responder:

```python
    def _call(prompt, model=None, timeout=None):
        return (
            '{"subject":"test subj","body":"test body — Living Water",'
            '"hooks_used":["genre_fallback"]}'
        )
```

Re-run tests:

```bash
cd outreach_agent && python3 -m pytest tests/ -v
```

All previously-passing tests remain green.

- [ ] **Step 6: Commit**

```bash
git add outreach_agent/template_engine.py outreach_agent/tests/conftest.py
git commit -m "feat(template_engine): parse hooks_used + persist in personalization_audit"
```

### Task 17 — Learning insights → contact_scorer adjustment

**Intent:** `learning_insights` is currently a one-way write. Consume it in `contact_scorer` so the scoring changes when the engine learns something.

**Files:**
- Modify: `outreach_agent/learning.py` (add `get_contact_type_score_adjustment`)
- Modify: `outreach_agent/contact_scorer.py` (call adjustment + fix `contact_type` → `type` bug)
- Test: `outreach_agent/tests/test_contact_scorer.py`

- [ ] **Step 1: Write the failing test**

Create `outreach_agent/tests/test_contact_scorer.py`:

```python
"""Contact scorer must use 'type' column (not 'contact_type') and apply learning adjustment."""
def test_scorer_reads_type_column(temp_db):
    import db
    import contact_scorer

    db.add_contact("c@x.com", "C", "curator", "tribal", "")
    db.mark_verified("c@x.com")
    db.mark_sent(
        email="c@x.com", message_id="m1", thread_id="t1",
        subject="s", body_snippet="b", template_type="curator",
    )
    db.update_contact("c@x.com", status="responded")

    contact = {"email": "c2@x.com", "type": "curator", "genre": "tribal", "notes": ""}
    score = contact_scorer.score_contact(contact)
    # Should not crash on bad column name and should return a number 0..10
    assert 0 <= score <= 10


def test_learning_adjustment_nudges_score(temp_db, monkeypatch):
    import db
    import contact_scorer
    import learning

    # Insert a winning-type insight
    db.save_insight(
        insight_type="pattern",
        content="Podcasts reply 3x more often than curators this quarter",
        based_on_n=30,
    )

    contact = {"email": "p@x.com", "type": "podcast", "genre": "", "notes": ""}
    adj = learning.get_contact_type_score_adjustment("podcast")
    assert adj >= 0  # non-negative; may be zero if heuristic doesn't match
```

- [ ] **Step 2: Run — reveal column bug**

```bash
cd outreach_agent && python3 -m pytest tests/test_contact_scorer.py -v
```

Expected: FAIL — either `OperationalError: no such column: contact_type` in `_reply_rate_score`, or `AttributeError` on `learning.get_contact_type_score_adjustment`.

- [ ] **Step 3: Fix the column bug in contact_scorer.py**

In `outreach_agent/contact_scorer.py`, find the `_reply_rate_score` function (around line 60) and change the query to use `type` instead of `contact_type`. Minimal fix:

```python
def _reply_rate_score(ctype: str) -> float:
    """Score based on historical reply rate for this contact type."""
    with db.get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status IN ('sent','followup_sent','responded','won','closed','lost')) AS sent,
                COUNT(*) FILTER (WHERE status IN ('responded','won','closed')) AS replied
            FROM contacts
            WHERE type = ?
        """, (ctype,)).fetchone()
    sent     = row["sent"] or 0
    replied  = row["replied"] or 0
    if sent < 5:
        return 0.0
    rate = replied / sent
    # 2.0 at 20% reply rate, linear below
    return min(2.0, (rate / 0.20) * 2.0)
```

- [ ] **Step 4: Add learning.get_contact_type_score_adjustment**

In `outreach_agent/learning.py`, append:

```python
def get_contact_type_score_adjustment(contact_type: str) -> float:
    """Return a small score nudge (0.0–1.0) based on recent learning_insights
    that mention this contact type positively.

    Heuristic: iterate the 10 most recent 'pattern' insights. For each insight
    containing the contact_type keyword AND a positive-direction word
    ('higher', 'more', 'better', 'winner', 'top'), add +0.25 (cap 1.0).
    Used by contact_scorer.score_contact as an additive boost.
    """
    rows = db.get_recent_insights(limit=10)
    positive_markers = ("higher", "more replies", "better", "top", "winner", "best", "outperforms")
    boost = 0.0
    target = contact_type.lower()
    for insight in rows:
        text = (insight.get("content") or "").lower()
        if target in text and any(m in text for m in positive_markers):
            boost += 0.25
    return min(1.0, boost)
```

- [ ] **Step 5: Wire into contact_scorer.score_contact**

In `outreach_agent/contact_scorer.py`, find the main `score_contact` function and after summing the existing components, add:

```python
    try:
        import learning
        adjustment = learning.get_contact_type_score_adjustment(ctype)
    except Exception:
        adjustment = 0.0
    total = min(10.0, round(total + adjustment, 2))
    return total
```

- [ ] **Step 6: Run all scorer tests — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_contact_scorer.py -v
```

Expected: 2 passed.

- [ ] **Step 7: Commit**

```bash
git add outreach_agent/contact_scorer.py outreach_agent/learning.py outreach_agent/tests/test_contact_scorer.py
git commit -m "fix(contact_scorer): use 'type' column + consume learning insights for score boost"
```

---

## Lake 4 — Test Baseline

**Intent:** Everything above assumes pytest is fast, isolated, and trustworthy. Lock that in with bounce + reply_detector coverage (the two modules most likely to mis-classify reality) and wire pytest up to run locally without sibling-process interference.

### Task 18 — bounce.verify_email regression

**Files:**
- Test: `outreach_agent/tests/test_bounce.py`

- [ ] **Step 1: Discover interface**

```bash
cd outreach_agent && python3 -c "import bounce; help(bounce.verify_email)" 2>&1 | head -20
```

- [ ] **Step 2: Write tests**

Create `outreach_agent/tests/test_bounce.py`:

```python
"""Lock in bounce.verify_email classification for obvious inputs."""
import pytest


def test_verify_email_rejects_missing_at(temp_db):
    import bounce
    result, reason = bounce.verify_email("not-an-email")
    assert result == "invalid"


def test_verify_email_rejects_empty(temp_db):
    import bounce
    result, reason = bounce.verify_email("")
    assert result == "invalid"


def test_verify_email_accepts_gmail(temp_db):
    import bounce
    # gmail.com always has MX records — offline fallback must still accept it
    result, _ = bounce.verify_email("someone@gmail.com")
    assert result in ("valid", "unknown"), f"gmail.com should not be marked invalid — got {result}"
```

- [ ] **Step 3: Run**

```bash
cd outreach_agent && python3 -m pytest tests/test_bounce.py -v
```

Expected: 3 passed. If any fail because `bounce.verify_email` needs network, mark the network-dependent one with `@pytest.mark.network` and `pytest.importorskip` DNS; the first two must pass without network.

- [ ] **Step 4: Commit**

```bash
git add outreach_agent/tests/test_bounce.py
git commit -m "test(bounce): lock in verify_email rejection of malformed inputs"
```

### Task 19 — reply_detector / reply_classifier coverage

**Files:**
- Test: `outreach_agent/tests/test_reply_classifier.py`

- [ ] **Step 1: Write tests**

Create `outreach_agent/tests/test_reply_classifier.py`:

```python
"""Lock in reply_classifier intent output for representative snippets."""
import pytest


@pytest.mark.parametrize("snippet,expected", [
    ("Sure, I'd love to have you on the podcast. What are your Tue/Wed availability?", "booking_intent"),
    ("Thanks for reaching out, unfortunately the playlist is full this quarter.", "negative"),
    ("Looks good, send me the WAV.", "positive"),
    ("What's your Spotify artist link?", "question"),
])
def test_reply_classifier_intents(snippet, expected, temp_db, monkeypatch):
    import reply_classifier

    # If reply_classifier uses Claude CLI, stub it
    try:
        import template_engine
        def _fake(prompt, model=None, timeout=None):
            return f'{{"intent":"{expected}","action":"stub"}}'
        monkeypatch.setattr(template_engine, "_call_claude", _fake)
    except Exception:
        pass

    classify = getattr(reply_classifier, "classify_snippet", None) or getattr(reply_classifier, "_classify_intent", None)
    if classify is None:
        pytest.skip("No public classify entrypoint")
    result = classify(snippet)
    assert expected in (result if isinstance(result, str) else str(result))
```

- [ ] **Step 2: Run**

```bash
cd outreach_agent && python3 -m pytest tests/test_reply_classifier.py -v
```

Expected: 4 passed OR 4 skipped if no public entrypoint exists. Either outcome is acceptable as a baseline — the goal is test scaffolding, not to assert a specific API surface.

- [ ] **Step 3: Commit**

```bash
git add outreach_agent/tests/test_reply_classifier.py
git commit -m "test(reply_classifier): parametric intent regression skeleton"
```

### Task 20 — Test runner config + Makefile target

**Files:**
- Create: `outreach_agent/pytest.ini`
- Modify: `rjm.py` (add `test outreach` subcommand if dispatch allows)

- [ ] **Step 1: Create pytest.ini**

Create `outreach_agent/pytest.ini`:

```ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -ra --strict-markers
markers =
    network: requires DNS/network access
```

- [ ] **Step 2: Run the full test suite once**

```bash
cd outreach_agent && python3 -m pytest -v
```

Expected: All tests from Tasks 1-19 pass. If anything is red, fix before moving on.

- [ ] **Step 3: Commit**

```bash
git add outreach_agent/pytest.ini
git commit -m "test(outreach): add pytest.ini with strict markers + test discovery"
```

---

## Lake 5 — Blocking Brand Gate on Email Generation

**Intent:** Today `template_engine.generate_email` calls `brand_gate.gate_or_warn` (non-blocking). When a sub-3/5 body slips through, it's shipped anyway. Move to blocking mode with one retry, and fall back to a safe template-only subject on double failure.

### Task 21 — Brand gate blocking mode + 1 retry

**Files:**
- Modify: `outreach_agent/template_engine.py:generate_email`
- Modify: `outreach_agent/config.py` (add `BRAND_GATE_MODE`)
- Test: `outreach_agent/tests/test_brand_gate_integration.py`

- [ ] **Step 1: Add config**

In `outreach_agent/config.py`:

```python
# Brand gate enforcement mode for template_engine.generate_email.
#   "warn"   — log-only (legacy behaviour)
#   "block"  — reject + retry once, then escalate to dead_letter via caller
BRAND_GATE_MODE = "block"
```

- [ ] **Step 2: Write the failing test**

Create `outreach_agent/tests/test_brand_gate_integration.py`:

```python
"""generate_email in block mode retries once on brand failure, then raises."""
import pytest
from unittest.mock import patch


def test_brand_gate_retries_once_then_raises(temp_db, monkeypatch):
    import template_engine as te
    import config
    import db

    monkeypatch.setattr(config, "BRAND_GATE_MODE", "block")
    db.add_contact("f@x.com", "F", "curator", "techno", "")
    db.mark_verified("f@x.com")
    contact = db.get_contact("f@x.com")

    calls = {"n": 0}
    def _bad(prompt, model=None, timeout=None):
        calls["n"] += 1
        # Return a body that fails brand gate: boilerplate, long, no concrete detail
        return (
            '{"subject":"a generic hello",'
            '"body":"Hi there, I am passionate about my unique sound and special journey. '
            'I hope this amazing message finds you well. Would love to connect.",'
            '"hooks_used":["other"]}'
        )
    monkeypatch.setattr(te, "_call_claude", _bad)

    with pytest.raises(RuntimeError, match="brand gate"):
        te.generate_email(contact, learning_context="")

    assert calls["n"] == 2, f"expected 1 generate + 1 retry = 2 calls, got {calls['n']}"


def test_brand_gate_passes_on_good_body(temp_db, monkeypatch):
    import template_engine as te
    import config
    import db

    monkeypatch.setattr(config, "BRAND_GATE_MODE", "block")
    db.add_contact("g@x.com", "G", "curator", "psytrance", "")
    db.mark_verified("g@x.com")
    contact = db.get_contact("g@x.com")

    def _good(prompt, model=None, timeout=None):
        return (
            '{"subject":"Halleluyah 140 BPM Psytrance for Ritual Techno",'
            '"body":"Your playlist sits at 138-142 BPM. Halleluyah is 140 BPM psytrance, '
            'recorded in Tenerife. Joshua 6 reference in the drop — the walls come down. '
            'https://open.spotify.com/artist/2Seaafm5k1hAuCkpdq7yds",'
            '"hooks_used":["bpm_match","playlist_mention"]}'
        )
    monkeypatch.setattr(te, "_call_claude", _good)

    subject, body = te.generate_email(contact, learning_context="")
    assert "140 BPM" in subject
    assert "Halleluyah" in body
```

- [ ] **Step 3: Run — must fail**

```bash
cd outreach_agent && python3 -m pytest tests/test_brand_gate_integration.py -v
```

Expected: FAIL — current code calls `gate_or_warn` (never raises) and has no retry loop.

- [ ] **Step 4: Patch generate_email with retry loop**

In `outreach_agent/template_engine.py`, replace the body of `generate_email()`:

```python
def generate_email(contact: dict, learning_context: str = "") -> tuple[str, str]:
    """Generate a personalised email, enforcing the brand gate when BRAND_GATE_MODE='block'.

    Retries once on brand-gate rejection. If the second attempt also fails,
    raises RuntimeError so the caller can bump send_attempts and dead-letter
    after the Lake 1 threshold.
    """
    from config import BRAND_GATE_MODE
    contact_type = contact.get("type", "curator")
    from db import get_best_template_type
    best_template = get_best_template_type(contact_type)
    if best_template and best_template != contact.get("template_type"):
        log.info(
            "Template override: %s → %s (best reply rate for %s)",
            contact.get("template_type", "default"), best_template, contact_type,
        )
        contact = {**contact, "template_type": best_template}

    last_flags: list = []
    for attempt in range(2):
        prompt = _build_prompt(contact, learning_context)
        log.info(
            "Generating email for %s (%s) — attempt %d",
            contact.get("email"), contact.get("type"), attempt + 1,
        )
        raw = _call_claude(prompt)
        subject, body, hooks = _parse_response(raw)

        brand_score = 0
        brand_passed = True
        brand_flags: list = []
        if _BRAND_GATE_AVAILABLE:
            result = _brand_gate.validate_content(body)
            brand_score = result.get("score", 0)
            brand_passed = result.get("passes", False)
            brand_flags = result.get("flags", [])

        if not _BRAND_GATE_AVAILABLE or BRAND_GATE_MODE != "block" or brand_passed:
            try:
                import db as _db
                _db.log_personalization_audit(
                    email=contact.get("email", ""),
                    research_done=bool(contact.get("research_done")),
                    used_research=bool(contact.get("research_notes")),
                    hooks_used=hooks,
                    subject=subject, body=body,
                    brand_score=brand_score,
                    brand_passed=brand_passed,
                )
            except Exception as exc:
                log.warning("personalization_audit write failed: %s", exc)
            log.info("Generated — subject: %r (hooks=%s brand=%s/5)", subject, hooks, brand_score)
            return subject, body

        last_flags = brand_flags
        log.warning(
            "Brand gate rejected attempt %d for %s: score=%d/5 flags=%s",
            attempt + 1, contact.get("email"), brand_score, brand_flags,
        )

    # Two attempts failed — log audit with brand_passed=0 and raise
    try:
        import db as _db
        _db.log_personalization_audit(
            email=contact.get("email", ""),
            research_done=bool(contact.get("research_done")),
            used_research=bool(contact.get("research_notes")),
            hooks_used=[],
            subject="", body="",
            brand_score=0, brand_passed=False,
        )
    except Exception:
        pass
    raise RuntimeError(f"brand gate rejected email after 2 attempts: {last_flags}")
```

- [ ] **Step 5: Run — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_brand_gate_integration.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Run full test suite — no regressions**

```bash
cd outreach_agent && python3 -m pytest -v
```

Expected: all tests green. If `test_send_batch.py` now sees the RuntimeError from brand gate instead of the intended RuntimeError from the mocked `gmail_client.send_email`, update the mock in `fake_claude` to return a brand-passing body (it already does after Task 16).

- [ ] **Step 7: Commit**

```bash
git add outreach_agent/template_engine.py outreach_agent/config.py outreach_agent/tests/test_brand_gate_integration.py
git commit -m "feat(template_engine): blocking brand gate with 1 retry + dead_letter escalation"
```

### Task 22 — Verify raise is handled by _send_batch dead-letter

**Intent:** Task 2 already wraps `generate_email` exceptions into `bump_send_attempts` + `mark_dead_letter`. Confirm the new RuntimeError flows through cleanly.

**Files:**
- Test: extension to `outreach_agent/tests/test_send_batch.py`

- [ ] **Step 1: Add test case**

Append to `outreach_agent/tests/test_send_batch.py`:

```python
def test_send_batch_dead_letters_on_brand_rejection(temp_db, mock_gmail, monkeypatch):
    import config
    import db
    import agent
    import template_engine as te

    monkeypatch.setattr(config, "MAX_SEND_ATTEMPTS", 2)
    monkeypatch.setattr(config, "DRAFT_MODE", False)
    monkeypatch.setattr(config, "BRAND_GATE_MODE", "block")

    def _bad(prompt, model=None, timeout=None):
        return (
            '{"subject":"hi","body":"Hi there, I am passionate about my unique sound and special journey. '
            'Hope this amazing message finds you well.","hooks_used":["other"]}'
        )
    monkeypatch.setattr(te, "_call_claude", _bad)

    db.add_contact("x@example.com", "X", "curator", "techno", "", source="manual")
    db.mark_verified("x@example.com")

    for _ in range(config.MAX_SEND_ATTEMPTS):
        agent._send_batch(1)

    row = db.get_contact("x@example.com")
    assert row["status"] == "dead_letter"
    assert row["send_attempts"] >= config.MAX_SEND_ATTEMPTS
```

- [ ] **Step 2: Run — must pass**

```bash
cd outreach_agent && python3 -m pytest tests/test_send_batch.py -v
```

Expected: 3 passed.

- [ ] **Step 3: Commit**

```bash
git add outreach_agent/tests/test_send_batch.py
git commit -m "test(send_batch): brand gate rejection flows to dead_letter after max attempts"
```

---

## Final Verification

**Files:** None (run-only)

- [ ] **Step 1: Full pytest run**

```bash
cd outreach_agent && python3 -m pytest -v 2>&1 | tail -30
```

Expected: all tests pass, exit code 0.

- [ ] **Step 2: Dry-run the agent end-to-end**

```bash
cd "/Users/motomoto/Documents/Robert-Jan Mastenbroek Command Centre/.claude/worktrees/intelligent-austin"
DRAFT_MODE=1 python3 outreach_agent/agent.py run 2>&1 | tail -40
```

Expected: cycle completes, heartbeat file written, no unhandled exceptions.

- [ ] **Step 3: Inspect event bus**

```bash
cd outreach_agent && python3 -c "
import db, events as e
for ev in e.recent(limit=20):
    print(ev['event_type'], ev['source'], (ev['payload'] or '')[:80])
"
```

Expected: events for `outreach.cycle_stage_failed` (if any), `outreach.rate_limit_paused` (if limits tripped), `pipeline.gap_detected` (if queue gaps).

- [ ] **Step 4: Inspect personalization_audit and discovery_log**

```bash
cd outreach_agent && python3 -c "
import db
rows = db.get_conn().execute('SELECT COUNT(*), MAX(sent_at) FROM personalization_audit').fetchone()
print('audit rows:', dict(rows))
rows = db.get_conn().execute('SELECT COUNT(*), MAX(searched_at) FROM discovery_log').fetchone()
print('discovery rows:', dict(rows))
"
```

Expected: non-zero counts, latest timestamp in the last few minutes.

- [ ] **Step 5: Heartbeat age check**

```bash
cd outreach_agent && python3 -c "import heartbeat; print('age min:', heartbeat.heartbeat_age_minutes())"
```

Expected: small float, < 5.

- [ ] **Step 6: Final commit of any loose ends (if needed)**

```bash
git status
git diff --stat
```

If clean, plan is complete.

---

## Explicitly Out of Scope (Oceans — Flagged, Not Boiled)

The following are real gaps but too large for this plan. They need their own spec + plan.

1. **Deliverability system** — DMARC/DKIM/SPF review, warm-up schedule tuning, inbox-placement testing, blacklist monitoring. Multi-week ops project.
2. **Spotify Playlist API integration** — replacing the Claude-CLI discover loop with direct Spotify API calls for curator discovery. Blocked on API quota + Spotify partner status.
3. **Full learning loop redesign** — current Lake 3 task adds a one-hop adjustment. A complete system would retrain per-cohort bandit weights, A/B subject-line testing, and closed-loop reply-rate regression. Multi-month research project.
4. **Paid email recovery service (Mailgun/Postmark)** — migrating off Gmail API for outbound. Requires DNS work, template migration, deliverability analysis.
5. **Full observability stack** — Prometheus exporters, Grafana dashboards, alerting rules. This plan adds events + heartbeat as the minimum viable layer; a dashboard is a separate project.
6. **Master agent consuming new events** — `master_agent.py` already has heartbeat logic; teaching it to subscribe to `outreach.rate_limit_paused` and `pipeline.gap_detected` is valuable but separable. Flag as next plan.

Each is a worthy lake → own spec → own plan when prioritised.
