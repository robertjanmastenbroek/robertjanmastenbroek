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

sys.path.insert(0, os.path.dirname(__file__))

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


def _check_gmail() -> dict:
    """Check Gmail OAuth token status."""
    result = {"status": "ok", "detail": ""}

    if not Path(CREDS_PATH).exists():
        return {"status": "missing", "detail": "credentials.json not found — run OAuth setup"}

    token_path = Path(TOKEN_PATH)
    if not token_path.exists():
        return {"status": "missing", "detail": "token.json not found — run: python3 agent.py auth"}

    try:
        token_data = json.loads(token_path.read_text())
        expiry_str = token_data.get("expiry") or token_data.get("token_expiry") or ""
        if expiry_str:
            try:
                expiry = datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                if expiry < now:
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
        try:
            _fleet_state.heartbeat(
                "auth_monitor",
                status="ok" if overall == "ok" else "error",
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
