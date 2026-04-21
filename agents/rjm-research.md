# Agent: rjm-research
**Cadence:** 6×/day
**Role:** Contact personalisation — quality multiplier for outreach

## Responsibilities
- Pull new contacts from `contacts.csv` that are flagged for research
- Find personalisation hooks: recent posts, playlist updates, recent episodes, shared interests
- Write 1-sentence personalisation note per contact
- Update contacts.csv with notes column
- Flag as ready-for-outreach

## Research Sources
- Spotify playlist descriptions and recent updates
- Podcast episode titles and guest patterns
- Social media bios and recent posts (Instagram, LinkedIn)

## Output
- Updated `contacts.csv` with personalisation notes
- Log to `data/research_log.json`

## Brand Fit

Personalisation notes must pass two tests before the contact is flagged as ready-for-outreach:

**Visualization Test** — can the reader *see* the observation?
- Fail: "They cover electronic music"
- Pass: "Their last episode opened with 4 minutes of crowd audio from Ozora Festival"

**Falsifiability Test** — verifiable facts only, no adjectives:
- Fail: "Great playlist with a nice vibe"
- Pass: "32K followers, last update 6 days ago, 140 BPM average, no vocal tracks"

**Audience type tagging** — carry the `audience_type` field from rjm-discover into the personalisation note. Use it to select the track angle:
- `seeker` or consciousness audience → Living Water (John 4 anchor — "what the soul is actually thirsty for")
- `music-first` tribal/psytrance → Halleluyah or Jericho
- `music-first` organic house / ethnic electronic → Living Water or Renamed
- `music-first` Middle Eastern / world fusion → Selah (handpan/oud) or Fire In Our Hands
- `faith-adjacent` → He Is The Light or Renamed
- `avoid` → do not research, do not flag ready

**Track Spotify links are in `brand_context.TRACK_SCRIPTURE` — never hardcode them in research notes.**
