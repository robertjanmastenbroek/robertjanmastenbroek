# Viral Single-Track Visualizer Study — Psytrance vs Organic House

**Date:** 2026-04-22
**Corpus:** 20 videos, 172M combined views, all verified via yt-dlp
**Source data:** `content/images/viral_visualizer_research/manifest.json` + bucket thumbnails/storyboards
**Purpose:** Ground the Holy Rave YouTube long-form motion engine (`content_engine/youtube_longform/motion.py`) in observed reality — System A (organic, 128–138 BPM) vs System B (psytrance, 140+ BPM).

---

## Sourcing & gaps

- **Psytrance bucket (10 videos):** clean. 8 of 10 uploaded 2023+, two classics kept for corpus coverage (Astrix "High Street" 2023-09 and Astrix "Coolio" 2025 provide the fractal-architecture reference). Channels covered: Omiki Official (×3), Astrix (×4), Ace Ventura (×2), Vini Vici (×1).
- **Organic bucket (10 videos):** all 10 uploaded 2023+. **7 of 10 come from one channel — Keinemusik.** This is not a sampling artifact; it is the finding. Cafe De Anatolia, Sol Selectas, Bedouin, Anjunadeep, All Day I Dream — the labels we treat as doctrinal references — publish almost NO single-track visualizers. Their top-viewed content is multi-hour DJ mixes (Cafe De Anatolia "Desert Music 2024" 1.8M views / 122 min) or live festival sets (Anjunadeep Open Air London 500K / 80 min). The single-track visualizer format on the organic side effectively = Keinemusik + a thin tail of Ben Böhmer / Monolink / Innellea / Acid Arab / Be Svendsen. Ben Böhmer supplies the "Anjunadeep-tasteful" pole.
- **Sample bias caveat:** Monolink's biggest single-track visualizers (Sirens, Burning Sun, Return To Oz remix) predate 2023 and were excluded per spec. If we widen to 2020+ the organic bucket gets visually more interesting — noted for any future research task.

---

## Psytrance patterns — what the 10 psy visualizers share

1. **One dominant hero per video, ornate to the point of excess.** Aztec warrior (Wana), gold-chrome priestess army (Sofia), pharaoh fractal head (High Street), third-eye android (Coolio), shaman-priestess (Dunya), cathedral eye-of-Providence (Ziran), Amazon high-priestess (Maana), mandala yogi (Ranji-remix covers). The thumbnail-framing is always a single subject centered dead-front.
2. **Dark-to-ignition palette** — coal/indigo ground with gold, amber, turquoise, blood-red highlights. Astrix-brand releases push into neon maximalism; Omiki/Vini Vici stay in cinematic low-key. Both rely on fire/bioluminescence as lighting source.
3. **Ornament density as genre marker.** Feather crowns, headdresses, tribal paint, gemstone inlays, bone-jewellery, fractal stained glass. Visual cost per frame is huge and that's the point — viewers use "how ornate is it" as a quality signal.
4. **Morph/scene-chain structure** on the big releases (Wana, Sofia, Maana, Dunya): 6–10 distinct keyframes — hero → establishing temple → sacred architecture → altar/fire scene → hero variant → wide-crowd → loop back. This matches what `motion.py` is already doing for Jericho / Selah.
5. **Static cover fallback on smaller releases** (Todo, Home Alone). Name-act videos still clear 1M+ views with a single image + title-card typography. A strong thumbnail alone can carry a release.
6. **Subject bias:** indigenous / pre-modern / temple cultures — Mesoamerican, Amazonian, Egyptian, pan-tribal. Rarely modern, never urban (outside of fractal-architecture which reads as sacred architecture, not a city).
7. **Live-nature crossover (Ace Ventura "Mindshift"):** some releases use dawn-meadow / forest-stream footage inside the video while the thumbnail does the tribal-hero clickbait job. Two-register formula.

## Organic house patterns — what the 10 organic visualizers share

1. **Near-static.** Eight of ten have a visibly identical storyboard across 25–30 tile positions — the video is essentially a static cover with micro-motion. The two exceptions: Positions (beat-cut lyric video) and Rust (slow macro push).
2. **No human hero (usually).** Of the 10, only "Say What" (crew photo) and "Positions" (anonymous body fragments) feature people in the visualizer body. The rest are either pure abstract pigment (Rapture, L.I.F.E, One Last Call, Rust) or one-tone graphic covers with typography (Move, Thandaza, Crazy For It, See You Again).
3. **Handmade typography as the visual lead.** Blackletter-graffiti (Move, Say What), scrawled acid-yellow handwriting (Rapture, L.I.F.E, Positions), airbrushed marker (Thandaza), blood-red brush (Crazy For It). The typography IS the visualizer; the image underneath is decoration.
4. **Palette restraint — one or two hues.** Each cover commits hard: Move = vermilion, See You Again = fire-red, Crazy For It = monochrome + blood, Rapture = flesh-pink, Rust = mustard-bouquet, One Last Call = lavender-cobalt. The discipline is strict; no rainbow.
5. **Zero scene diversity.** No multi-scene arcs, no morph chains. The video is one image/one loop. This is the opposite of Wana's 7-keyframe chain.
6. **Brand-as-aesthetic.** The Keinemusik hand-font + off-kilter layout is itself the signal. Viewers recognize 7 frames in that it's Keinemusik and click. Aesthetic value flows from brand equity, not from production budget.
7. **The ornate-organic pole exists but it's tiny** — Acid Arab's "Sayarat 303", some Sabo releases, early Monolink — calligraphic/Middle-Eastern poster art. Not the mainstream of the genre's top chart.

## Key distinctions — System A vs System B (updated doctrine)

| Dimension            | System B (psytrance, 140+)         | System A (organic, 128–138)       |
|----------------------|------------------------------------|------------------------------------|
| Subject              | One ornate human/spirit hero       | Abstract matter or static graphic  |
| Scene count          | 6–10 morphing keyframes            | 1 looping shot                     |
| Camera motion        | Continuous morph, zoom, pan        | Near-static, slow push at most     |
| Ornament density     | Maximalist                         | Minimalist                         |
| Palette              | Dark base + gold/fire/cyan accents | One or two hues, restrained        |
| Typography           | Subtle or absent                   | Hand-drawn, dominant, brand-signal |
| Production style     | Cinematic render / AI maximalism   | Painterly loop / graphic-design    |
| Virality lever       | "How stunning is the hero"         | "How strong is the brand stamp"    |

**The current motion.py header calls the reference "Omiki & Vegas — Wana".** That is correct for System B but explicitly wrong for System A — Wana's 9-keyframe chain is NOT how Keinemusik / Ben Böhmer / Acid Arab build organic-house visualizers. System A should NOT be a morph chain at all; it should be a high-quality single-anchor composition (still or slow-loop) that leans on handwritten type and palette restraint.

**Recommended doctrinal edit to `motion.py`:** split the reference block into two.
- System B reference: Omiki "Wana" → 6-10 keyframes, morph-chain, ornate-hero compositions.
- System A reference: Keinemusik "Move" / Ben Böhmer "Rust" → 1 hero composition, slow/static, handwritten title typography, one dominant warm hue, no scene diversity. Selah's 9-unique-keyframe build is an anomaly for the organic bucket — the algorithm rewards restraint here, not variety.

---

## Recommended keyframe vocabulary for System A (organic, 128–138)

**Format directive:** single hero still (or 10–20 sec slow-loop), not a morph chain. Two sub-modes:

### A-abstract (Keinemusik-side, 60% of System A output)
- **Subject:** pigment/marble swirl, fluid-paint holes, macro-floral bouquet, single hand-lettered word.
- **Palette commitments:** pick ONE dominant warm hue per release — `#b8532a` terracotta, `#c8883a` ochre, `#d4af37` liturgical gold, oxblood, rose-peach. Supporting hue strictly from `#1a2a4a` indigo-night, `#0a0a0a` ink black, `#ffffff` bone, or a single acid-yellow/acid-lime for typography.
- **Typography:** hand-drawn title, off-kilter, large — it is the hero, not the image. Serif or scrawl, never clean sans.
- **Composition rules:** one subject centered or slightly off-center, negative space ≥60% of frame, flat-lay or extreme macro, zero depth tricks.
- **Track fits:** Selah (Psalm 46 "be still"), Living Water, Renamed, Side By Side. "Be still" compositions — still water, single handpan, unrolled scroll, one oil lamp, one loaf of bread, one dove feather.

### A-cinematic (Ben Böhmer / Innellea / Acid Arab-side, 40% of System A)
- **Subject:** macro organic matter (bouquet, olive branch, running water, coals, sand drifting), one calm close-up human-ish presence (hands on handpan, cloaked figure at ridge, single dancer at sunset).
- **Palette:** golden hour, dusk teal, fog, dawn pink — always ONE key light source.
- **Composition rules:** shallow depth of field, slow push-in or pull-out, no hard cuts, thumbnail = single arresting macro (bouquet > face).
- **Track fits:** He Is The Light, Fire In Our Hands (slow intro side), Selah bridge.

### System A keyframe mine (10 ready concepts)
1. Single-tone vermilion field, blackletter-graffiti Hebrew word "רָנָה" (ranah, "song") smeared across middle.
2. Macro-bouquet of olive + wheat + pomegranate blossom, prismatic flare, dawn light.
3. Fluid-pigment marbling in terracotta + indigo + ochre, slow recirculation.
4. Torn-paper collage of hand-textures (psalmist's hands, potter's hands, shepherd's hands) on one-color field, redacted sleeves in blood-red.
5. Static oil-lamp in dark doorway, ONE flame flicker loop, all else motionless.
6. Dusty unrolled scroll under angled sunbeam, hand-lettered English title floating.
7. Single cedar branch on bone-white field with "Lebanon" handwritten in charcoal.
8. Macro close-up of handpan skin — dimples, fingerprints, dust — with "Selah" in off-white serif.
9. Still well-water reflecting a sliver of moon, title set in Cormorant Garamond.
10. Flat-lay of bread + salt + oil + wine on worn linen, Scripture reference micro-text corner.

---

## Recommended keyframe vocabulary for System B (psytrance, 140+)

**Format directive:** keep the current motion.py morph-chain architecture. 6–10 unique keyframes, adjacent-frame seamless morph, loop back at the end.

### Subject vocabulary (one ornate hero per keyframe)
- Iron Age Hebrew warrior with bronze kopis and blue tzitzit (Jericho anchor — already in motion.py).
- Priestess with frankincense censer, white linen and indigo sash (Kadosh).
- Fire-circle dancers around a night altar, sparks rising (Halleluyah).
- Temple gate at dusk, seven trumpeters silhouetted on the ramparts.
- Hebrew scribe illuminating a Paleo-Hebrew scroll by oil-lamp light.
- Shema cloak-figure silhouette under a deep-night Milky Way, single menorah in foreground.
- Prophet on desert ridge, cloak whipping, eyes closed mid-utterance.
- Lion of Judah carved on stone temple portal, golden hour.
- Single falling ember sparking a dry-grass fire (opens into crowd).
- Cosmic throne-room vision — wheels within wheels, ophanim — strictly rare, used once per story.

### Composition rules
- Hero dead-center, framed from chest up or in Aztec/Maana-style bust — not full body, not landscape.
- Dust particles, embers, breath-mist, light shafts in EVERY frame — this is the "cinematic" tax.
- Palette: locked to core tokens + earth accents. Dark `#0a0a0a` base, `#d4af37` liturgical gold on ornament, `#b8532a` terracotta as accent warm, `#1a2a4a` indigo-night as cool shadow. Never neon — that's Astrix territory, not Holy Rave.
- Depth of field shallow, subject tack-sharp against blurred temple/desert/crowd.
- Every frame a valid 1280×720 thumbnail.

### Morph-chain rhythm
- 140+ BPM ⇒ faster morphs, heavier dust/ember motion, zoom-in/zoom-out oscillation every 10s.
- Finale morph must loop back to keyframe 1 with at least 3 seconds of frame-share — the seam stays invisible.

---

## 10 fresh story ideas for upcoming Holy Rave tracks

### System A (organic, 128–138) — 5 pitches, minimalist/single-anchor

1. **Renamed (128, Isaiah 62) — "The Crown"**
   Pitch line 1: One still — a golden bridal crown resting on weathered olive-wood, dusty sunbeam cutting across.
   Pitch line 2: Whole 7-min loop is a slow push from 2m wide to 30cm close, no cut.
   Pitch line 3: Handwritten "Renamed" in acid-gold across the lower third, Hebrew "שְׁמִי חָדָשׁ" micro-text corner.

2. **Living Water (124, John 4) — "The Jar"**
   Pitch line 1: A clay Samarian jar on a stone well-rim at noon, one drip rolling down the side.
   Pitch line 2: Static composition, the only motion is ONE drip per 4 bars and heat-shimmer behind.
   Pitch line 3: Palette: bone + terracotta + midday white — a "be still" composition that refuses the algorithm's usual dopamine.

3. **He Is The Light (128, John 8) — "The Flame"**
   Pitch line 1: A single oil-lamp flame in a pitch-black stone chamber, flame sways with the track's kick.
   Pitch line 2: 95% black frame, 5% gold flame — extreme palette restraint; thumbnail is unforgettable.
   Pitch line 3: Title in smallcaps serif floats in the black half, "יהוה אורי" micro-Hebrew ghosted beneath.

4. **Side By Side (130, English unreleased) — "The Handprints"**
   Pitch line 1: Flat-lay of two weathered hands, palms up on linen — one man's, one woman's, not touching but aligned.
   Pitch line 2: Micro-motion is a slow breath-rise in the fingers. That's it.
   Pitch line 3: Palette: olive skin + linen ivory + single strand of indigo thread looping between.

5. **Selah (130, Psalm 46) — "The Still Water"**
   Pitch line 1: Pre-dawn oasis pool, mirror-still, one stone dropped every 16 bars creating a single ripple ring.
   Pitch line 2: Extreme restraint; one rhythm event per half-minute, otherwise fully still.
   Pitch line 3: Title "Selah" in Cormorant Garamond reflected off the water surface, Scripture corner-tag.

### System B (psytrance, 140+) — 5 pitches, ornate morph-chain

6. **Halleluyah (140, ecstatic) — "The Fire Circle"**
   Pitch line 1: 8 keyframes — lone torch-bearer → ring of dancers → drummer bust → crowd bathed in sparks → night sky fractal → priestess face → warrior face → back to torch.
   Pitch line 2: Every frame fire-lit, orange-amber dominant, indigo-night shadows, tzitzit + linen costume.
   Pitch line 3: Camera rotates 360° across the chain so keyframe 9 matches keyframe 1.

7. **Jericho (140, Joshua 6) — "The Seventh Circuit"** (update the existing build)
   Pitch line 1: Keep the warrior anchor but add a keyframe pair: trumpet-bearing Levite at wall-foot, and crumbling ramparts mid-fall with golden dust.
   Pitch line 2: Ensure one nighttime-crowd wide shot and one hero-bust alternate between each major landmark.
   Pitch line 3: Loop closes on warrior's eyes opening as the first stone falls.

8. **Kadosh (142, Hebrew unreleased) — "The Holy of Holies"**
   Pitch line 1: 9 keyframes — censer in priest's hand → cedar doorway → ark glimpse → cloud-pillar → priestess silhouette → scribe at scroll → prostrate figure → lion-carved lintel → back to censer.
   Pitch line 2: Palette exclusively gold + bone linen + cedar brown + pillar-cloud white — NO flame (this is reverence, not ecstasy).
   Pitch line 3: Slowest morph cadence in the B catalogue; "holy" reads as restraint, not intensity.

9. **Storm Over The Jordan (145 placeholder, unreleased psy) — "The Crossing"**
   Pitch line 1: River waters parting at night under lightning-cracked sky, hero priest raising staff, tribes crossing dry-shod behind.
   Pitch line 2: 7 keyframes alternating priest-bust / wide-tribe / water-wall detail / sky-lightning / ark-bearers / far shore / staff-raised loop-back.
   Pitch line 3: Palette: cobalt storm + gold staff + bone-linen — the only hot color is the ark's gold.

10. **The Valley of Dry Bones (143 placeholder, unreleased) — "Breath"**
    Pitch line 1: 10 keyframes — single skull in dust → ribcage wind-stirred → sinew forming → flesh re-gathering → army rising → warrior bust → priest's breath → tribes reunited → lion on standard → back to skull.
    Pitch line 2: Sand-dust + bone-white + ember-red palette, breath-mist mandatory in every frame.
    Pitch line 3: Progression from death → life is the narrative spine; final loop crossfade from warrior bust back to single skull creates a "rebirth wheel" that runs forever.

---

## Summary recommendations

1. **Edit `motion.py` header** to split the reference model: Wana = System B ONLY. System A needs its own doctrine rooted in Keinemusik / Ben Böhmer — single anchor, handwritten typography, palette restraint, near-zero scene diversity. Selah's 9-unique-keyframe build is overbuilt for System A; simpler = more algorithmic.
2. **For System A, stop cost-multiplying.** Current Selah build = ~$10.81/publish. A matching-virality A-build following the Keinemusik formula would be 1 Flux keyframe ($0.075) + a 10-second Kling loop ($0.84) + 6-minute loopback Shotstack render. ~$1.50/publish. 7× cheaper at probably equal retention for the organic bucket.
3. **Keep the System B cinematic pipeline unchanged** — the morph-chain is exactly right for psytrance; Wana and Maana validate the formula in the field.
4. **Earth-accent palette (terracotta / indigo-night / ochre) is directly echoed in both buckets' top performers** — the recent rebrand landed correctly.
5. **Don't try to clone the Keinemusik brand-stamp look until Holy Rave has the brand equity to get away with minimalism.** Until then, System A should lean Ben Böhmer/Anjunadeep-tasteful (macro-organic + serif typography) rather than Keinemusik-minimalist (hand-scrawl + flat color). The tasteful-cinematic pole is achievable with the current Flux+Kling rig; the minimalist pole requires brand equity that unlocks later.
