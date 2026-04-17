# outreach_agent/auth_monitor.py
"""
Auth Monitor — checks credential health and broadcasts to fleet.

Bridges: Gmail OAuth Scopes + Instagram Credentials -> Fleet State + Events.

Checks:
  1. Gmail token.json exists and is not expired
  2. INSTAGRAM_ACCESS_TOKEN env var is set
  3. BUFFER_API_KEY env var is set (bonus)

Publishes auth.health event and registers fleet heartbeat.
"""

import os
import sys
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))


def _load_env() -> None:
    """Load repo-root .env into os.environ (idempotent).

    The monitor is invoked directly by schedulers and by rjm.py — neither of
    which source .env into the shell. Without this, INSTAGRAM_ACCESS_TOKEN /
    BUFFER_API_KEY come back "missing" even though they're configured.
    Mirrors the pattern in metrics_fetcher._load_env so the behaviour is
    consistent across the fleet.
    """
    env_file = Path(__file__).parent.parent / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        # Strip surrounding quotes so `INSTAGRAM_USER_ID="123"` → `123`.
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


_load_env()

try:
    from config import TOKEN_PATH, CREDS_PATH
except ImportError:
    TOKEN_PATH = Path(__file__).parent / "token.json"
    CREDS_PATH = Path(__file__).parent / "credentials.json"

try:
    import events as _events
    _EVENTS_AVAILABLE = True
except ImportError:
    _EVENTS_AVAILABLE = False

try:
    import fleet_state as _fleet_state
    _FLEET_AVAILABLE = True
except ImportError:
    _FLEET_AVAILABLE = False


def _try_refresh_gmail_token(token_path: Path) -> Optional[str]:
    """Attempt to refresh an expired Gmail token in place.

    Returns the new ISO expiry string on success, or None on failure (caller
    should treat as still-expired). Silent if google-auth libs aren't available
    — we log and fall through to the original "expired" signal.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except ImportError:
        return None

    try:
        tok = json.loads(token_path.read_text())
        scopes = tok.get("scopes") or None
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)
        if not creds.refresh_token:
            return None
        creds.refresh(Request())
        token_path.write_text(creds.to_json())
        refreshed = json.loads(token_path.read_text())
        return refreshed.get("expiry")
    except Exception:
        return None


def _check_gmail() -> dict:
    """Check Gmail OAuth token status.

    Behaviour: if the token is expired AND a refresh_token is available, we
    refresh it in place (same thing the real outreach agent does lazily at send
    time, see gmail_client.get_service). The monitor keeps the token warm on
    its own schedule instead of waiting for the 30-min scheduler to try sending
    and fail. That eliminates the false-positive "auth.health: expired" signal
    that was fired every 30 minutes whenever nothing had been sent in the last
    hour (since tokens expire after 60 min).
    """
    result = {"status": "ok", "detail": ""}

    if not Path(CREDS_PATH).exists():
        return {"status": "missing", "detail": "credentials.json not found — run OAuth setup"}

    token_path = Path(TOKEN_PATH)
    if not token_path.exists():
        return {"status": "missing", "detail": "token.json not found — run: python3 agent.py auth"}

    try:
        token_data = json.loads(token_path.read_text())
        has_refresh = bool(token_data.get("refresh_token"))
        expiry_str = token_data.get("expiry") or token_data.get("token_expiry") or ""
        if expiry_str:
            try:
                expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if expiry < now:
                    if has_refresh:
                        new_expiry = _try_refresh_gmail_token(token_path)
                        if new_expiry:
                            return {"status": "ok", "detail": f"refreshed — valid until {new_expiry}"}
                    return {"status": "expired", "detail": f"token expired at {expiry_str} — re-run auth"}
                result["detail"] = f"valid until {expiry_str}"
            except ValueError:
                result["detail"] = "expiry format unrecognised — assuming valid"
        else:
            result["detail"] = "no expiry field — assuming valid (refresh token present)"
    except Exception as e:
        return {"status": "error", "detail": str(e)}

    return result


def _check_instagram() -> dict:
    """Check Instagram access token presence."""
    token = os.getenv("INSTAGRAM_ACCESS_TOKEN")
    if not token:
        return {"status": "missing", "detail": "INSTAGRAM_ACCESS_TOKEN env var not set"}
    return {"status": "ok", "detail": f"token present ({len(token)} chars)"}


def _check_buffer() -> dict:
    """Check Buffer API key presence."""
    key = os.getenv("BUFFER_API_KEY")
    if not key:
        return {"status": "missing", "detail": "BUFFER_API_KEY env var not set"}
    return {"status": "ok", "detail": f"key present ({len(key)} chars)"}


def run_check() -> dict:
    """
    Run all credential checks. Returns dict with keys: gmail, instagram, buffer, overall.
    Publishes auth.health event and fleet heartbeat.
    """
    checks = {
        "gmail":     _check_gmail(),
        "instagram": _check_instagram(),
        "buffer":    _check_buffer(),
    }

    all_ok = all(v["status"] == "ok" for v in checks.values())
    any_error = any(v["status"] in ("expired", "error") for v in checks.values())
    overall = "ok" if all_ok else ("error" if any_error else "degraded")

    checks["overall"] = overall

    if _EVENTS_AVAILABLE:
        try:
            _events.publish("auth.health", "auth_monitor", checks)
        except Exception:
            pass

    if _FLEET_AVAILABLE:
        # The monitor ran successfully — it correctly observed the auth state.
        # Broken downstream credentials belong in auth.health (consumed by the
        # dashboard), not in the monitor's own error_count. Previously every
        # expired-token check was counted as a monitor failure, inflating the
        # fleet error count (15/18) when the monitor itself was fine.
        try:
            _fleet_state.heartbeat(
                "auth_monitor",
                status="ok",
                result=overall,
            )
        except Exception:
            pass

    return checks


def format_summary(checks: dict) -> str:
    """Format a check result dict as a human-readable string."""
    lines = ["── Auth Health ──────────────────────────────"]
    for key in ("gmail", "instagram", "buffer"):
        if key not in checks:
            continue
        status = checks[key].get("status", "?")
        detail = checks[key].get("detail", "")
        icon = "+" if status == "ok" else ("-" if status in ("expired", "error") else "!")
        lines.append(f"  {icon} {key:<12} {status:<10} {detail}")
    lines.append(f"  Overall: {checks.get('overall', '?')}")
    return "\n".join(lines)
