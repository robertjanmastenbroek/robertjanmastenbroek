# Fan-First Relationship Outreach System — Design Spec
**Date:** 2026-04-17  
**Status:** Approved  
**Replaces:** rjm-discover (curator-only cold-pitch pipeline)

---

## Philosophy

The system shifts from a pitch machine to a relationship builder. The question is never
"did we send an email?" — it's "where are we in this relationship?"

Every contact moves through a relationship arc, not an email status sequence. A reply
is the new "win." A playlist add or booking is the natural downstream result of trust
built over time.

---

## Target Audience — Three Zones

### Zone 1 — The Sacred Intersection (most unique, zero competition)
| Persona | Who | Primary Goal |
|---|---|---|
| `faith_creator` | Christian/spiritual content creators (IG/TT/YT) | Relationship → music share |
| `church` | Churches / youth ministries running or open to electronic music events | Booking |
| `retreat` | Faith-based and conscious retreat organizers | Booking |
| `ecstatic_dance` | Ecstatic Dance organizers globally (sober, spiritual, needs DJs) | Booking |

### Zone 2 — The Scene Insiders
| Persona | Who | Primary Goal |
|---|---|---|
| `rave_photographer` | Event photographers (website/IG) | Relationship → collaboration |
| `sound_engineer` | Live sound engineers at conscious/techno events | Relationship → advocate |
| `conscious_promoter` | Conscious rave / sober event promoters | Booking |
| `lifestyle_creator` | Rave fashion, festival lifestyle creators | Relationship → music share |

### Zone 3 — The Lifestyle Overlap
| Persona | Who | Primary Goal |
|---|---|---|
| `digital_nomad` | Nomad creators, Tenerife-based or global | Relationship → music share |
| `surfer` | Ocean/surf lifestyle creators (Tenerife angle) | Relationship → music share |
| `sacred_artist` | Sacred geometry, dark aesthetic visual artists | Relationship → collaboration |
| `genre_creator` | YouTube/TikTok melodic techno / psytrance creators | Music share |

### Existing (kept, now relationship-first)
| Persona | Maps from |
|---|---|
| `curator` | type=curator |
| `podcast` | type=podcast |
| `event_promoter` | type=festival, booking_agent |

---

## Contact Model

New columns added to `contacts` table alongside existing fields (no data loss):

| Column | Type | Values |
|---|---|---|
| `relationship_stage` | TEXT | `discovered` · `researched` · `first_touch` · `responded` · `nurturing` · `collaborating` · `advocate` |
| `persona` | TEXT | See personas above |
| `outreach_goal` | TEXT | `relationship` · `booking` · `music_share` · `collaboration` |
| `warmth_score` | INTEGER | 0–100 (auto-updated on interaction) |
| `faith_signals` | INTEGER | 0=none · 1=spiritual · 2=faith-adjacent · 3=explicit faith |
| `platforms` | TEXT | JSON: `{"instagram": "...", "youtube": "...", "tiktok": "..."}` |
| `their_location` | TEXT | City/country — used for geo intelligence |
| `audience_size` | INTEGER | Follower/subscriber count |
| `personal_notes` | TEXT | What we know about them as a human |
| `last_interaction_at` | TEXT | ISO timestamp |
| `interaction_count` | INTEGER | Total touchpoints |

The `status` field is preserved for backward compatibility with the existing email
pipeline. `relationship_stage` is the new primary state machine. Migration script
maps status → relationship_stage on all existing contacts.

---

## Geographic Intelligence

Managed by `geo_intelligence.py`. Three zones:

- **home** — Tenerife, Canary Islands, Spain
- **primary** — Netherlands, Ibiza, major Spanish cities
- **europe** — All European countries
- **worldwide** — Everything else

Rules by outreach goal:

| Goal | Home | Primary | Europe | Worldwide |
|---|---|---|---|---|
| `booking` | ✅ direct | ✅ direct | ✅ travel framing | ✅ future framing |
| `collaboration` (photographer) | ✅ | ❌ → downgrade to relationship | ❌ → downgrade | ❌ → downgrade |
| `relationship` | ✅ | ✅ | ✅ | ✅ |
| `music_share` | ✅ | ✅ | ✅ | ✅ |

Travel framing: "I travel Europe frequently and am always looking to connect..."  
Future framing: "If you ever need a DJ for your event..."

Photographer collab is **Tenerife-local only** — never pitch a shoot to someone
2,000+ km away.

---

## Discovery Pipeline — 5 Sources, Target 20–30/day

| Source | File | Daily Target | Method |
|---|---|---|---|
| A | Web search (expanded) | 5–8 | 8 queries, new persona query banks |
| B | Spotify playlist | 0–5 | Keep existing |
| C | Playlist contact mining | 0–3 | Keep existing |
| D | Ecstatic Dance directory | 3–5 | `ecstatic_dance_miner.py` |
| E | Playlist contact mining (YouTube) | 3–5 | Keep existing |

### Web Search Query Banks (Part A)

**Faith Creator (2 slots):**
```
"Christian" electronic music creator Instagram YouTube contact email 2026
"faith" "electronic music" OR "techno" lifestyle creator blog contact 2026
"Christian DJ" OR "faith and music" creator content contact 2026
"spiritual but not religious" electronic music creator contact 2026
```

**Ecstatic Dance (2 slots — supplement to miner):**
```
"ecstatic dance" organizer [REGION] contact email booking DJ 2026
"conscious dance" OR "authentic movement" event organizer contact email 2026
```

**Rave Photographer / Videographer (1 slot — Tenerife-first):**
```
"rave photographer" OR "festival photographer" Tenerife OR Spain OR Netherlands website contact
"festival videographer" electronic music events contact website email 2026
```

**Digital Nomad / Lifestyle (1 slot):**
```
"digital nomad" Tenerife OR "Canary Islands" creator lifestyle Instagram YouTube contact 2026
surfer lifestyle creator Tenerife electronic music contact Instagram 2026
```

**Church / Retreat (2 slots):**
```
"church" OR "Christian" youth event electronic music modern worship Spain Netherlands contact 2026
"Christian retreat" OR "faith retreat" music worship modern electronic contact email 2026
```

---

## Relationship Sequences (by persona)

### Booking personas (ecstatic_dance, church, retreat)
- **Touch 1:** Genuine observation about their event/community + "I'm a DJ in Tenerife
  who plays exactly this format. Would love to connect." — no music link yet.
- **Touch 2 (Day 10):** Share something useful (track suggestion, resource, intro).
- **Touch 3 (Day 21):** Soft pitch — "If you ever need music or a live DJ..."
- **Touch 4 (Day 40):** Graceful close — "I'll leave you to it — hope our paths cross."

### Rave photographer / sacred_artist
- **Touch 1:** Reference a specific photo/piece. Genuine. Zero music mention.
- **Touch 2 (Day 14):** Engage publicly with their recent work, then DM follow-up.
- **Touch 3 (Day 30):** "I'm playing [event] — I'd love a photographer who gets this."
  Only if in Tenerife zone; otherwise skip T3 and stay relationship.

### Faith creator
- **Touch 1:** Reference something specific from their content. Faith × music angle.
  Subtle — never lead with "I'm a Christian DJ."
- **Touch 2 (Day 10):** Share Living Water or Jericho. Let the music surface the connection.
- **Touch 3 (Day 28):** Conversation — what they do, what RJM does. No pitch.

### Digital nomad / surfer / lifestyle
- **Touch 1:** Tenerife angle or lifestyle overlap. "I'm based here too" or "Your content
  captures what I try to put into music."
- **Touch 2 (Day 14):** Value add — local tip, connection, or share.
- **Touch 3 (Day 30):** Music introduction, no pressure.

### Global rules
- Touch 1 is always about THEM. Never mention the music first.
- Follow-ups add value — never "just checking in."
- Reply at any stage → `responded` status + human-review flag.
- Max 4 touches before `dormant` (not deleted — resurfaces on next release).

---

## Brand Gate Updates

`brand_gate.py` gets two new contact-level filters (separate from content validation):

**Pagan/occult hard exclude:** Contact explicitly promotes pagan deities, occult rituals,
or drug-ceremony-as-spiritual-practice → auto-skip, log reason.

**Ecstatic Dance qualification:** Is this a sober, spiritually-open space? → qualify.
Test: "Would a sober Christian rave attendee attend this willingly?" Yes → qualify.

**Booking venue check:** Venues where being drunk/drugged is the primary selling point
→ classify as `audience` (potential listeners via content), never as `relationship` contact.

---

## Migration Strategy

One-time script `reclassify_contacts.py`:

| Old `type` | New `persona` |
|---|---|
| curator | curator |
| podcast | podcast |
| youtube | genre_creator |
| festival | event_promoter |
| booking_agent | event_promoter |
| wellness | retreat |
| blog | lifestyle_creator |
| community | community_leader |
| label | curator |

| Old `status` | New `relationship_stage` | Warmth score |
|---|---|---|
| new / verified / queued | discovered | 5 |
| sent | first_touch | 20 |
| followup_sent | first_touch | 30 |
| responded | responded | 60 |
| won | collaborating | 90 |
| skip / bounced | discovered | 0 |

The 27 existing `responded` contacts are the highest-priority warm leads —
reviewed manually before next send cycle.
