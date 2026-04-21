# YouTube Long-Form Publisher

**Holy Rave still-image + full-track YouTube uploads — the @osso-so format, applied to RJM's nomadic electronic catalog.**

Replicates the proven format: single AI-generated still image + one original 5–7 min track, uploaded every 2–3 days. See the research notes in the session that created this module for full context on why this works, plus the @osso-so forensic analysis.

---

## Quick start

```bash
# Health check on all APIs
python3 rjm.py content youtube status

# Preview the Flux prompt for a track (no API calls)
python3 rjm.py content youtube explain Jericho

# Budget / quota estimate
python3 rjm.py content youtube budget

# Publish (dry run — generates images + renders, skips upload)
python3 rjm.py content youtube publish Jericho --dry-run

# Scheduled publish (Thursday 17:00 UTC)
python3 rjm.py content youtube publish Jericho --schedule 2026-04-24T17:00:00Z
```

## Pipeline

```
track_title
    │
    ▼
prompt_builder.build_prompt()
    │  (reads audio_engine.SCRIPTURE_ANCHORS + TRACK_BPMS)
    │  (outputs TrackPrompt — Biblically-Nomadic Flux prompt)
    ▼
image_gen.generate_hero()       ─ fal.ai Flux 2 Pro (+ LoRA)
image_gen.generate_thumbnails() ─ 3 variants @ 1280x720 for A/B
    │
    ▼
render.composite()
    │  (Cloudinary primary; Shotstack PAYG fallback)
    │  (image + audio → 1920x1080 H.264/AAC MP4)
    ▼
registry.build_smart_link()
    │  (Feature.fm → Odesli → raw Spotify + UTM, in priority order)
    ▼
uploader.upload()
    │  (resumable videos.insert + thumbnails.set + playlistItems.insert)
    ▼
registry.append()
    │  (JSONL log at data/youtube_longform/youtube_longform.jsonl)
    ▼
PublishResult
```

## Module layout

| File | Purpose |
|------|---------|
| `__init__.py`        | Public exports |
| `types.py`           | `PublishRequest`, `PublishResult`, `TrackPrompt`, `UploadSpec`, etc. |
| `config.py`          | All tunables (env-driven) |
| `prompt_builder.py`  | Track metadata → Biblically-Nomadic Flux prompt |
| `image_gen.py`       | fal.ai Flux 2 Pro client (hero + thumbnail variants) |
| `render.py`          | Cloudinary / Shotstack image+audio → MP4 |
| `uploader.py`        | YouTube Data API v3 resumable upload |
| `registry.py`        | Dedup + smart-link (Feature.fm / Odesli) |
| `publisher.py`       | End-to-end orchestrator |
| `tests/`             | Prompt-builder contract tests + dry-run integration tests |

## Visual language — Biblically-Nomadic

The distinguishing 20% vs the broader Café de Anatolia / psytrance scene. Visually identical most of the time — desert, Bedouin, sacred geometry, warm earth palette — but with objects and locations specifically from the **Abrahamic cradle**:

- **Settings:** Wadi Rum, Petra, Sinai, Negev, Mount Tabor, Jordan valley
- **Figures:** Bedouin/Berber/nomadic, robed, face obscured, never smiling-for-camera
- **Objects:** shofar (literal Jericho), menorah, oil lamps, water jars, olive branches
- **Text:** Hebrew characters etched in stone, **never** Latin crucifixes
- **Geometry:** echoes Tabernacle floorplan; **not** OM mandalas / yantras / Kabbalistic-neo-pagan
- **Colors:** core #0a0a0a + #d4af37 + earth accents (terracotta / indigo night / ochre)

The negative prompt aggressively blocks: purple gradients, teal, neon, plastic-skin AI glossy, Balenciaga-editorial masked figures on flat red, DMT fractals, ayahuasca imagery, European Gothic cathedrals, smiling stock faces.

## BPM → mood tier → hero subject

| BPM | Tier | Subject |
|---|---|---|
| ≤126 | meditative    | solitary robed figure, cross-legged on basalt, handpan-stillness |
| 127–132 | processional | small caravan moving across dunes, staffs in hand |
| 133–138 | gathering    | circle of cloaked figures around a rising fire |
| 139+ | ecstatic     | vast crowd of silhouetted nomads, arms raised, trumpets aloft |

Each track gets a consistent environment (Wadi Rum, Petra, Negev, etc.) via stable hash of its title — so the channel grid shows variety, but regenerating the same track yields the same scene.

## Scripture-anchor visuals

The Subtle Salt layer. Each scripture anchor maps to a specific **object-over-concept** visual phrase. Example:

```
Joshua 6  →  "a crumbling sandstone wall at the moment of collapse,
              seven bronze ram's-horn trumpets raised toward the fissure,
              ancient Canaanite city receding into dusk behind it"

Psalm 46  →  "a handpan resting on black basalt at the center of a
              moonlit desert canyon, oud leaning against the stone,
              incense smoke rising in a single vertical line of perfect stillness"

John 4    →  "a clay water jar resting on the rim of an ancient stone
              well at noon, a single thread of water catching the sun"
```

The anchor is **never announced**. People who know the Word recognize the imagery; others simply feel the weight.

## Env vars

See `.env.example.youtube_longform` at the project root. Minimum required before `publish` works (not `dry-run`):

```
FAL_KEY=<your-fal.ai-key>
YOUTUBE_CLIENT_ID=<from existing RJM OAuth app>
YOUTUBE_CLIENT_SECRET=<same>
YOUTUBE_REFRESH_TOKEN=<generated via scripts/setup_youtube_oauth.py for HOLY RAVE channel>
CLOUDINARY_CLOUD_NAME=<or SHOTSTACK_API_KEY as fallback>
CLOUDINARY_API_KEY=
CLOUDINARY_API_SECRET=
```

Optional (highly recommended):

```
FAL_BRAND_LORA_URL=<after training; ~$20 one-time>
FAL_BRAND_LORA_SCALE=0.80
FEATUREFM_API_KEY=<for measured Spotify funnel analytics>
YOUTUBE_HOLY_RAVE_CHANNEL_ID=<the new channel ID once created>
YOUTUBE_PLAYLIST_TRIBAL_PSY=
YOUTUBE_PLAYLIST_ORGANIC_HOUSE=
YOUTUBE_PLAYLIST_MIDDLE_EASTERN=
```

## Costs

At 3 uploads/week (156/year):

| Line item | Cost |
|---|---|
| fal.ai Flux 2 Pro (1 hero + 3 thumbs) | ~$22/year |
| fal.ai LoRA training (one-time) | $16–20 |
| fal.ai LoRA retrains (3/year) | ~$60 |
| Cloudinary (free tier) | $0 |
| Shotstack (fallback, PAYG) | ~$2/upload only when used |
| Feature.fm (free tier, UTM tracking) | $0 |
| YouTube Data API quota (default 10k/day) | $0 — 5 uploads/day headroom |
| **Total year 1** | **~$100** |

Compared to a human designer at $50/image = $26,000/year for equivalent volume.

## LoRA training

25+ reference images live under `content/images/lora_training/holy_rave_v1/` (gitignored via `content/images/` rule).

Training flow (not automated yet — one-time manual step):

1. Review the curated set for visual cohesion.
2. Upload set to fal.ai Flux 2 trainer.
3. Train for 1,500–2,500 steps (~$16–20).
4. Copy the resulting `.safetensors` URL to `FAL_BRAND_LORA_URL` in `.env`.
5. Every generation now snaps to the Holy Rave visual universe.

Retrain every 3–4 months as you identify winning thumbnails from YouTube analytics — drop those into the next training set.

## Testing

```bash
# Full suite — no external API calls
python3 -m pytest content_engine/youtube_longform/tests/ -v
```

The `test_prompt_builder.py` suite guards against visual drift: every assertion encodes a brand decision that must survive code changes. The `test_publisher_dry_run.py` suite verifies the orchestrator wiring without hitting fal.ai / YouTube.

## Design discipline

- **No ffmpeg / PyAV / OpenCV / MoviePy anywhere.** Structural block per memory. All rendering is cloud-native.
- **No local encoding** — every MP4 comes from a cloud API.
- **Dedup registry before upload.** A second call for the same track short-circuits with the existing YouTube URL instead of double-publishing.
- **Schedule via `publishAt`, not unlisted→public flip.** Notifications fire correctly only for `private` + `publishAt`.
- **One commit = one concern.** Code changes are separate from visual/website changes (per CLAUDE.md visual-change discipline).

## Next steps (not in scope for this module)

1. Create the `@holyrave` YouTube channel + phone-verify + authorize existing OAuth app.
2. Train `hr_brand_v1` LoRA on the curated training set.
3. First dry-run against `Jericho`. Inspect every asset.
4. First live publish. Thursday 17:00 UTC.
5. Monitor 72h. If retention ≥ 55% and CTR ≥ 6%, scale to 3×/week cadence.
