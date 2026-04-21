"""
Tests for image_gen.py — mocked fal.ai calls.

Ensures the exact argument shape sent to fal.ai matches what Flux 2 Pro
actually accepts (verified against live docs 2026-04-21):
  - prompt, image_size (dict), output_format, seed — allowed
  - num_inference_steps, guidance_scale, negative_prompt, loras — NOT allowed on Flux 2 Pro

And that when FAL_BRAND_LORA_URL is set, the code routes to fal-ai/flux-lora
with the full parameter shape it DOES accept.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from content_engine.youtube_longform import image_gen, prompt_builder
from content_engine.youtube_longform.types import TrackPrompt


def _fake_fal(endpoint_captured: list, arguments_captured: list, image_url: str = "https://fake.fal/image.jpg"):
    """Build a fake fal_client module that captures subscribe() calls."""
    fake = types.ModuleType("fal_client")

    def subscribe(endpoint, arguments=None, with_logs=False):
        endpoint_captured.append(endpoint)
        arguments_captured.append(arguments or {})
        return {"images": [{"url": image_url}]}

    fake.subscribe = subscribe  # type: ignore
    return fake


def test_baseline_flux_2_pro_call_shape(monkeypatch, tmp_path):
    """Baseline (no LoRA) sends only fields Flux 2 Pro accepts."""
    endpoints: list[str] = []
    args: list[dict] = []
    fake = _fake_fal(endpoints, args)

    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "fal_client", fake)
    # Force LoRA off
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_BRAND_LORA_URL", "")
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_KEY", "test-key")
    # Redirect image dir to tmp
    monkeypatch.setattr("content_engine.youtube_longform.config.IMAGE_DIR", tmp_path)

    # Stub out _download so no real network
    monkeypatch.setattr(image_gen, "_download", lambda url, dest, timeout=60: dest.write_bytes(b"fake"))

    prompt = prompt_builder.build_prompt("Jericho", seed=42)
    image_gen.generate_hero(prompt, use_references=False)

    assert len(endpoints) == 1
    assert endpoints[0] == "fal-ai/flux-2-pro"
    call_args = args[0]

    # REQUIRED on Flux 2 Pro
    assert "prompt" in call_args
    assert "image_size" in call_args
    assert call_args["image_size"] == {"width": 1920, "height": 1080}
    assert call_args.get("output_format") == "jpeg"
    assert call_args.get("seed") == 42

    # MUST NOT be in the Flux 2 Pro call (would cause 422 if sent)
    assert "num_inference_steps" not in call_args, "Flux 2 Pro rejects num_inference_steps"
    assert "guidance_scale"      not in call_args, "Flux 2 Pro rejects guidance_scale"
    assert "negative_prompt"     not in call_args, "Flux 2 Pro rejects negative_prompt"
    assert "loras"               not in call_args, "Flux 2 Pro rejects loras"


def test_baseline_merges_negative_into_positive_prompt(monkeypatch, tmp_path):
    """Because Flux 2 Pro has no negative_prompt field, negatives are folded into prompt."""
    endpoints: list[str] = []
    args: list[dict] = []
    fake = _fake_fal(endpoints, args)
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "fal_client", fake)
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_BRAND_LORA_URL", "")
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_KEY", "test-key")
    monkeypatch.setattr("content_engine.youtube_longform.config.IMAGE_DIR", tmp_path)
    monkeypatch.setattr(image_gen, "_download", lambda url, dest, timeout=60: dest.write_bytes(b"fake"))

    prompt = prompt_builder.build_prompt("Jericho")
    image_gen.generate_hero(prompt, use_references=False)

    merged = args[0]["prompt"]
    assert "Avoid:" in merged, "Negative prompt should be merged with 'Avoid:' marker"
    # A sampling of negative tokens should now appear in the merged prompt
    assert "plastic skin" in merged
    assert "purple gradients" in merged


def test_lora_path_routes_to_flux_lora_endpoint(monkeypatch, tmp_path):
    """When FAL_BRAND_LORA_URL is set, routes to fal-ai/flux-lora with loras array."""
    endpoints: list[str] = []
    args: list[dict] = []
    fake = _fake_fal(endpoints, args)
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "fal_client", fake)
    monkeypatch.setattr(
        "content_engine.youtube_longform.config.FAL_BRAND_LORA_URL",
        "https://fake.fal/holyrave_v1.safetensors",
    )
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_KEY", "test-key")
    monkeypatch.setattr("content_engine.youtube_longform.config.IMAGE_DIR", tmp_path)
    monkeypatch.setattr(image_gen, "_download", lambda url, dest, timeout=60: dest.write_bytes(b"fake"))

    prompt = prompt_builder.build_prompt("Jericho", seed=99)
    image_gen.generate_hero(prompt, use_references=False)

    assert endpoints[0] == "fal-ai/flux-lora"
    call_args = args[0]
    # Flux 1 LoRA endpoint accepts ALL the fields Flux 2 Pro rejects
    assert "loras" in call_args
    assert call_args["loras"][0]["path"] == "https://fake.fal/holyrave_v1.safetensors"
    assert "num_inference_steps" in call_args
    assert "guidance_scale" in call_args
    assert "negative_prompt" in call_args


def test_generate_cached_image_is_not_regenerated(monkeypatch, tmp_path):
    """If the expected hero image already exists on disk, skip fal.ai call."""
    endpoints: list[str] = []
    args: list[dict] = []
    fake = _fake_fal(endpoints, args)
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "fal_client", fake)
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_BRAND_LORA_URL", "")
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_KEY", "test-key")
    monkeypatch.setattr("content_engine.youtube_longform.config.IMAGE_DIR", tmp_path)
    monkeypatch.setattr(image_gen, "_download", lambda url, dest, timeout=60: dest.write_bytes(b"fake"))

    prompt = prompt_builder.build_prompt("Jericho", seed=42)
    image_gen.generate_hero(prompt, use_references=False)       # First call generates
    first_call_count = len(endpoints)
    image_gen.generate_hero(prompt, use_references=False)       # Second call should cache
    assert len(endpoints) == first_call_count, "Cached image should skip fal.ai call"


def test_estimate_cost_math():
    """Cost math matches Flux 2 Pro published pricing."""
    # 1 hero @ 1920x1080 = $0.045 (1st MP $0.03 + extra MP $0.015)
    # 3 thumbs @ 1280x720 each rounds up to 1MP = $0.03 each
    cost = image_gen.estimate_cost_usd(hero_count=1, thumb_count=3)
    # (0.045 * 1) + (0.03 * 3) = 0.045 + 0.09 = 0.135
    assert abs(cost - 0.135) < 0.001


def test_reference_conditioning_routes_to_edit_endpoint(monkeypatch, tmp_path):
    """
    When reference URLs are supplied (non-empty), image gen routes to
    fal-ai/flux-2-pro/edit with image_urls — not the baseline endpoint.
    """
    endpoints: list[str] = []
    args: list[dict] = []
    fake = _fake_fal(endpoints, args)
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "fal_client", fake)
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_BRAND_LORA_URL", "")
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_KEY", "test-key")

    refs = ["https://fake.cdn/ref1.jpg", "https://fake.cdn/ref2.jpg"]
    image_gen._generate_one(
        prompt="test prompt",
        negative_prompt="test neg",
        width=1920,
        height=1080,
        seed=42,
        reference_urls=refs,
    )

    assert endpoints[0] == "fal-ai/flux-2-pro/edit", (
        "With references supplied, must route to the Edit endpoint"
    )
    assert args[0]["image_urls"] == refs
    assert "output_format" in args[0]


def test_reference_conditioning_caps_at_9_refs(monkeypatch):
    """fal-ai/flux-2-pro/edit hard cap is 9 references — we truncate."""
    endpoints: list[str] = []
    args: list[dict] = []
    fake = _fake_fal(endpoints, args)
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "fal_client", fake)
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_BRAND_LORA_URL", "")
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_KEY", "test-key")

    refs = [f"https://fake/{i}.jpg" for i in range(15)]
    image_gen._generate_one(
        prompt="p", negative_prompt="", width=1024, height=1024,
        seed=None, reference_urls=refs,
    )
    assert len(args[0]["image_urls"]) == 9


def test_no_fal_key_raises_helpful_error(monkeypatch):
    """Missing FAL_KEY should raise ImageGenError with actionable message."""
    monkeypatch.delenv("FAL_KEY", raising=False)
    monkeypatch.setattr("content_engine.youtube_longform.config.FAL_KEY", "")
    with pytest.raises(image_gen.ImageGenError) as exc_info:
        image_gen._fal_client()
    assert "FAL_KEY" in str(exc_info.value)
