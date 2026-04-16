# content_engine/tests/test_hook_library.py
import pytest
from content_engine.hook_library import (
    HookTemplate,
    SAVE_DRIVER_TEMPLATES,
    PERFORMANCE_TEMPLATES,
    CONTRAST_TEMPLATES,
    BODY_DROP_TEMPLATES,
    IDENTITY_TEMPLATES,
    pick_templates_for_format,
    pick_transitional_hook,
    get_all_templates,
)
from content_engine.types import ClipFormat


def test_save_driver_count():
    """At least 20 save-driver templates."""
    assert len(SAVE_DRIVER_TEMPLATES) >= 20


def test_performance_template_count():
    assert len(PERFORMANCE_TEMPLATES) >= 4


def test_existing_templates_preserved():
    """All 21 original templates still exist."""
    all_t = get_all_templates()
    original_ids = [
        "contrast.pov_collision", "contrast.everyone_said_dead", "contrast.i_was_told",
        "contrast.walked_into", "contrast.you_think", "contrast.made_at_confession",
        "contrast.nobody_asked",
        "bodydrop.countdown_body", "bodydrop.watch_at_timestamp", "bodydrop.played_at_place",
        "bodydrop.drop_at_timestamp", "bodydrop.felt_in_body", "bodydrop.count_with_me",
        "bodydrop.bassline_warning",
        "identity.if_youve_ever", "identity.made_this_for_specific",
        "identity.this_is_what_sounds_like", "identity.three_years_ago",
        "identity.dear_self", "identity.one_line_for_you", "identity.same_week_shift",
    ]
    all_ids = {t.id for t in all_t}
    for oid in original_ids:
        assert oid in all_ids, f"Missing original template: {oid}"


def test_pick_templates_transitional():
    templates = pick_templates_for_format(ClipFormat.TRANSITIONAL)
    assert len(templates) == 1
    # Transitional draws from save-drivers + body-drop
    assert templates[0].id.startswith(("save.", "bodydrop."))


def test_pick_templates_emotional():
    templates = pick_templates_for_format(ClipFormat.EMOTIONAL)
    assert len(templates) == 1
    # Emotional draws from contrast + save-drivers
    assert templates[0].id.startswith(("contrast.", "save."))


def test_pick_templates_performance():
    templates = pick_templates_for_format(ClipFormat.PERFORMANCE)
    assert len(templates) == 1
    # Performance draws from identity + body-drop + perf
    assert templates[0].id.startswith(("identity.", "bodydrop.", "perf."))


def test_pick_templates_reaches_all_original_21():
    """Over many picks, every original contrast/bodydrop/identity id should surface."""
    import random
    random.seed(42)
    seen_ids = set()
    # Pull across all formats to cover all pools
    for _ in range(800):
        for fmt in (ClipFormat.TRANSITIONAL, ClipFormat.EMOTIONAL, ClipFormat.PERFORMANCE):
            seen_ids.add(pick_templates_for_format(fmt)[0].id)
    original_ids = {t.id for t in (CONTRAST_TEMPLATES + BODY_DROP_TEMPLATES + IDENTITY_TEMPLATES)}
    missing = original_ids - seen_ids
    assert not missing, f"Unreachable original templates: {missing}"


def test_pick_transitional_hook_respects_cooldown():
    from datetime import date
    today = date.today().isoformat()
    bank = [
        {"file": "a.mp4", "category": "nature", "duration_s": 3.0,
         "last_used": today, "performance_score": 1.0, "times_used": 1},
        {"file": "b.mp4", "category": "satisfying", "duration_s": 4.0,
         "last_used": None, "performance_score": 1.0, "times_used": 0},
    ]
    hook = pick_transitional_hook(bank, yesterday_category="elemental")
    assert hook["file"] == "b.mp4"


def test_all_templates_have_required_fields():
    for t in get_all_templates():
        assert t.id, f"Template missing id"
        assert t.angle, f"Template {t.id} missing angle"
        assert t.mechanism, f"Template {t.id} missing mechanism"
        assert t.template, f"Template {t.id} missing template text"
        assert t.example_fill, f"Template {t.id} missing example_fill"
