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
