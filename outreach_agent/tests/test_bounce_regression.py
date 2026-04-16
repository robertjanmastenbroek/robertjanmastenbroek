"""Regression: bounce.verify_email behaviour on the deterministic branches.

Network-touching paths (Disify + DoH + catch-all probe) are stubbed so the
tests stay hermetic. What we're locking in:
  - Malformed input → invalid
  - Confirmed-dead seed domain → invalid
  - Confirmed-dead seed address → invalid
  - Major provider fast-path → valid without any network call
  - Disify 'invalid' → invalid
  - Disify 'valid' + catch-all → unknown (not valid)
  - Disify 'valid' + non-catch-all → valid
  - Disify 'unknown' + DoH NXDOMAIN → invalid
  - All unknown → allowed through as unknown
"""
import pytest


def test_malformed_email_is_invalid(temp_db):
    import bounce
    result, reason = bounce.verify_email("not-an-email")
    assert result == "invalid"
    assert "malformed" in reason.lower()


def test_seed_dead_domain_blocked(temp_db):
    import bounce
    result, _ = bounce.verify_email("curator@protonmail.com")
    assert result == "invalid"


def test_seed_dead_address_blocked(temp_db):
    import bounce
    result, _ = bounce.verify_email("demos@cercle.io")
    assert result == "invalid"


def test_major_provider_fast_path_skips_network(temp_db, monkeypatch):
    import bounce

    def _should_not_call(*a, **k):
        raise AssertionError("bounce must not hit network for major providers")
    monkeypatch.setattr(bounce, "_disify_check", _should_not_call)
    monkeypatch.setattr(bounce, "_doh_query", _should_not_call)

    result, reason = bounce.verify_email("someone@gmail.com")
    assert result == "valid"
    assert "gmail.com" in reason


def test_disify_invalid_short_circuits(temp_db, monkeypatch):
    import bounce
    monkeypatch.setattr(bounce, "_disify_check", lambda e: ("invalid", "Disify: disposable"))
    monkeypatch.setattr(bounce, "_is_catchall_domain", lambda d: False)
    result, reason = bounce.verify_email("a@unknown-label.com")
    assert result == "invalid"
    assert "disposable" in reason.lower()


def test_disify_valid_plus_catchall_is_unknown(temp_db, monkeypatch):
    import bounce
    monkeypatch.setattr(bounce, "_disify_check", lambda e: ("valid", "Disify ok"))
    monkeypatch.setattr(bounce, "_is_catchall_domain", lambda d: True)
    result, reason = bounce.verify_email("a@catchall-label.com")
    assert result == "unknown"
    assert "catch-all" in reason.lower()


def test_disify_valid_plus_real_mailbox_is_valid(temp_db, monkeypatch):
    import bounce
    monkeypatch.setattr(bounce, "_disify_check", lambda e: ("valid", "Disify ok"))
    monkeypatch.setattr(bounce, "_is_catchall_domain", lambda d: False)
    result, reason = bounce.verify_email("real@real-label.com")
    assert result == "valid"


def test_disify_unknown_plus_nxdomain_is_invalid(temp_db, monkeypatch):
    import bounce
    monkeypatch.setattr(bounce, "_disify_check", lambda e: ("unknown", "Disify down"))
    # NXDOMAIN status code is 3
    monkeypatch.setattr(bounce, "_doh_query", lambda d, record_type="MX": (3, []))
    result, reason = bounce.verify_email("ghost@no-such-tld.xyz")
    assert result == "invalid"
    assert "nxdomain" in reason.lower()


def test_all_inconclusive_allows_through(temp_db, monkeypatch):
    import bounce
    monkeypatch.setattr(bounce, "_disify_check", lambda e: ("unknown", "down"))
    monkeypatch.setattr(bounce, "_doh_query", lambda d, record_type="MX": (-1, []))
    result, _ = bounce.verify_email("uncertain@weird.tld")
    assert result == "unknown"
