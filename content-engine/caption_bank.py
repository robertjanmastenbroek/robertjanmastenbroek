"""
Caption bank — unique platform captions per song, drawn randomly each run.

Every post gets a fresh combination of:
  - A hook variant from the database (80+ per song)
  - A TikTok caption (10+ per song)
  - An Instagram caption (10+ per song)
  - A YouTube title + description (10+ per song)

With 4 songs × 10 caption variants × 80 hooks = thousands of unique daily combos.
"""

import random
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Per-song caption pools ────────────────────────────────────────────────────
# Each entry: (tiktok_caption, tiktok_hashtags, ig_caption, ig_hashtags, yt_title, yt_description)
# Key: lowercase song identifier (matches hook_generator._match_track logic)

CAPTION_POOLS = {

    "jericho": {
        "tiktok": [
            ("the walls came down. just like that. 🔥", "#holyrave #jericho #melodictechno #rave #tenerife #sunsetsessions #techno #undergroundtechno"),
            ("joshua 6:20 at 136 bpm 👁️", "#holyrave #biblicaltechno #jericho #sacredtechno #melodictechno #rave #undergroundtechno #sunsetsessions"),
            ("they shouted and the walls fell. we're still shouting.", "#holyrave #jericho #melodictechno #sacredmusic #tenerife #rave #techno #sunsetsessions"),
            ("this drop is 3000 years old 🌊", "#holyrave #jericho #melodictechno #ancienttruthfuturesound #techno #electronicworship #rave #sunsetsessions"),
            ("what happens when faith hits 136 bpm", "#holyrave #jericho #sacredtechno #christianrave #melodictechno #rave #undergroundtechno #electronicmusic"),
            ("the frequency that breaks walls 🏛️", "#holyrave #jericho #techno #sunsetsessions #melodictechno #sacredmusic #tenerife #electronicworship"),
            ("not the rave you expected. exactly the one you needed.", "#holyrave #jericho #melodictechno #rave #sacredtechno #undergroundtechno #sunsetsessions #tenerife"),
            ("ancient battle cry. modern dancefloor.", "#holyrave #jericho #ancienttruthfuturesound #melodictechno #techno #rave #electronicworship #sunsetsessions"),
            ("every week in tenerife. no walls can stand.", "#holyrave #sunsetsessions #jericho #melodictechno #tenerife #rave #undergroundtechno #sacredmusic"),
            ("joshua walked these walls 3000 years ago 🔥", "#holyrave #jericho #biblicaltechno #melodictechno #sacredtechno #rave #sunsetsessions #tenerife"),
        ],
        "instagram": [
            ("The walls of Jericho fell when the people shouted together.\nThis is what that sounds like on a dancefloor.\n\nEvery week in Tenerife. Free entry. In the name of Jesus.", "#holyrave #jericho #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno"),
            ("Joshua 6:20 — the walls collapsed at the sound of the shout.\n\nSome songs are built to break things open. Jericho is one of them.", "#holyrave #jericho #sunsetsessions #melodictechno #sacredtechno #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #christianrave #biblicaltechno"),
            ("Sacred. Loud. Unstoppable.\n\nFree weekly Sunset Sessions in Tenerife. Jericho is what we play when we mean it.", "#holyrave #jericho #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #dancefloor"),
            ("136 BPM. The same frequency as faith on a Friday night.\n\nJericho — out now on Spotify. Link in bio.", "#holyrave #jericho #sunsetsessions #melodictechno #newmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #spotify #sacredtechno"),
            ("Some places you can only enter through sound.\n\nJericho. Ancient truth. Future frequency.", "#holyrave #jericho #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #sacredtechno"),
            ("The dust on the floor. The bass in your chest. The name of Jesus on your lips.\n\nThis is what worship looks like in 2026.", "#holyrave #jericho #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #christianrave #dancefloor"),
            ("Not by might. Not by power. By sound — and something greater.\n\nJericho. Every week. Sunset Sessions, Tenerife.", "#holyrave #jericho #sunsetsessions #melodictechno #sacredtechno #tenerife #rave #undergroundtechno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #techno"),
            ("They said walls don't fall that way.\nJoshua walked anyway.\n\nWe play Jericho for people who are still walking.", "#holyrave #jericho #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #ancienttruthfuturesound #robertjanmastenbroek #biblicaltechno #dancefloor #sacredmusic"),
        ],
        "youtube": [
            ("Jericho — Holy Rave Tenerife | Sacred Melodic Techno", "Joshua 6:20 at 136 BPM. The walls fell at the sound.\nRobert-Jan Mastenbroek — Ancient Truth. Future Sound.\nFree weekly Sunset Sessions in Tenerife. Link in bio."),
            ("If Joshua Had a Sound System — Holy Rave Jericho", "3000-year-old battle cry. Modern dancefloor. This is Jericho.\nRobert-Jan Mastenbroek — Ancient Truth. Future Sound.\nFree weekly events in Tenerife. All welcome."),
            ("Holy Rave — Jericho at 136 BPM | Sunset Sessions Tenerife", "The walls of Jericho collapsed at the sound of the shout. We haven't stopped.\nRobert-Jan Mastenbroek — streaming on Spotify now. Link in bio."),
            ("What Does Joshua 6:20 Sound Like in Techno?", "Sacred melodic techno rooted in scripture. Jericho — out now.\nRobert-Jan Mastenbroek | Ancient Truth. Future Sound.\nFree Sunset Sessions every week in Tenerife."),
            ("The Drop That Breaks Walls — Holy Rave Tenerife", "136 BPM. In the name of Jesus. This is Jericho.\nRobert-Jan Mastenbroek — Ancient Truth. Future Sound. Out now on Spotify."),
            ("Sacred Music for the Dancefloor — Jericho | Holy Rave", "Ancient walls fell at the sound. Some nights the music does the same thing.\nFree weekly events in Tenerife. Robert-Jan Mastenbroek on Spotify now."),
        ],
    },

    "halleluyah": {
        "tiktok": [
            ("this is what praise sounds like at 128 bpm 🙌", "#holyrave #hallelujah #melodictechno #rave #tenerife #sunsetsessions #techno #electronicworship"),
            ("you weren't expecting hallelujah on a dancefloor", "#holyrave #halleluyah #melodictechno #sacredtechno #christianrave #rave #sunsetsessions #tenerife"),
            ("the word is 3000 years old. the drop is brand new. 🔥", "#holyrave #halleluyah #melodictechno #ancienttruthfuturesound #techno #electronicworship #rave #sunsetsessions"),
            ("halleluyah isn't a church word. it's a war cry.", "#holyrave #halleluyah #sacredtechno #melodictechno #rave #undergroundtechno #sunsetsessions #tenerife"),
            ("praise that hits different at midnight 🌙", "#holyrave #halleluyah #melodictechno #electronicworship #sacredmusic #tenerife #rave #sunsetsessions"),
            ("every dancefloor needs this song once 👁️", "#holyrave #halleluyah #melodictechno #rave #sacredtechno #christianrave #sunsetsessions #techno"),
            ("when the music and the meaning finally match", "#holyrave #halleluyah #melodictechno #electronicworship #sunsetsessions #tenerife #rave #undergroundtechno"),
            ("psalm 150:4 said praise him with dancing. we took notes.", "#holyrave #halleluyah #psalm150 #melodictechno #sacredtechno #christianrave #rave #sunsetsessions"),
            ("free. weekly. tenerife. halleluyah. 🕊️", "#holyrave #sunsetsessions #halleluyah #melodictechno #tenerife #rave #electronicworship #sacredmusic"),
            ("the whole dancefloor felt it and nobody could explain why", "#holyrave #halleluyah #melodictechno #sacredmusic #rave #undergroundtechno #sunsetsessions #tenerife"),
        ],
        "instagram": [
            ("Halleluyah — the most ancient word of praise, at 128 BPM.\n\nEvery week in Tenerife. Free. In the name of Jesus.", "#holyrave #halleluyah #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #dancefloor"),
            ("Praise doesn't have a dress code.\n\nSunset Sessions — free weekly gatherings where this music lives.", "#holyrave #halleluyah #sunsetsessions #melodictechno #electronicworship #tenerife #rave #undergroundtechno #techno #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #christianrave #psytrance #tribaltechno"),
            ("They asked why I mix faith and techno.\n\nBecause Psalm 150 says praise Him with dancing. I took that seriously.", "#holyrave #halleluyah #sunsetsessions #melodictechno #psalm150 #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #robertjanmastenbroek #christianrave #ancienttruthfuturesound #dancefloor"),
            ("Halleluyah — out now on Spotify. Ancient word. New frequency.\n\nThis is what worship sounds like when it stops being polite.", "#holyrave #halleluyah #sunsetsessions #melodictechno #newmusic #spotify #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #sacredtechno"),
            ("The dancefloor and the altar aren't as far apart as you think.\n\nHalleluyah. Every Friday. Tenerife.", "#holyrave #halleluyah #sunsetsessions #melodictechno #electronicworship #tenerife #rave #undergroundtechno #techno #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #christianrave"),
            ("Some songs break something loose in you.\n\nHalleluyah is one of those songs. Out now — link in bio.", "#holyrave #halleluyah #sunsetsessions #melodictechno #sacredtechno #tenerife #rave #undergroundtechno #techno #electronicworship #ancienttruthfuturesound #robertjanmastenbroek #newmusic #spotify #dancefloor"),
            ("Not church. Not club. Something in between and more honest than both.\n\nHalleluyah. Sunset Sessions. Free entry.", "#holyrave #halleluyah #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #christianrave"),
            ("When the bass drops and everyone in the room knows something holy just happened.\n\nThat moment is what I produce music for.", "#holyrave #halleluyah #sunsetsessions #melodictechno #electronicworship #tenerife #rave #undergroundtechno #techno #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #tribaltechno #dancefloor #sacredtechno"),
        ],
        "youtube": [
            ("Halleluyah — Sacred Melodic Techno | Holy Rave Tenerife", "The most ancient word of praise at 128 BPM.\nRobert-Jan Mastenbroek — Ancient Truth. Future Sound.\nFree weekly Sunset Sessions in Tenerife. All welcome."),
            ("Psalm 150 on a Dancefloor — Halleluyah | Holy Rave", "Praise Him with dancing. We took that literally.\nRobert-Jan Mastenbroek — streaming on Spotify now. Link in bio."),
            ("Holy Rave Tenerife — Halleluyah | Free Weekly Events", "Sacred electronic music every week in Tenerife. Free entry. In the name of Jesus.\nRobert-Jan Mastenbroek | Ancient Truth. Future Sound."),
            ("What Does Halleluyah Sound Like in Techno?", "Ancient praise. Modern frequency. This is what worship looks like in 2026.\nRobert-Jan Mastenbroek — Halleluyah out now on Spotify."),
            ("Halleluyah at 128 BPM — Sacred Electronic Worship", "The dancefloor and the altar aren't as far apart as you think.\nRobert-Jan Mastenbroek | Free Sunset Sessions every week in Tenerife."),
            ("The Rave That Praises Jesus — Halleluyah | Robert-Jan Mastenbroek", "No ticket. No agenda. Just sound and something sacred.\nHalleluyah — streaming now. Ancient Truth. Future Sound."),
        ],
    },

    "renamed": {
        "tiktok": [
            ("god changed his name. changed everything. 🔥", "#holyrave #renamed #melodictechno #rave #tenerife #sunsetsessions #sacredtechno #undergroundtechno"),
            ("jacob wrestled with god and got a new name. this track is that.", "#holyrave #renamed #melodictechno #genesis32 #sacredtechno #christianrave #rave #sunsetsessions"),
            ("when you meet god you don't walk out the same 👁️", "#holyrave #renamed #melodictechno #ancienttruthfuturesound #techno #electronicworship #rave #sunsetsessions"),
            ("abraham. jacob. you. renamed by something greater.", "#holyrave #renamed #sacredtechno #melodictechno #rave #undergroundtechno #sunsetsessions #tenerife"),
            ("identity change at 130 bpm 🌊", "#holyrave #renamed #melodictechno #electronicworship #sacredmusic #tenerife #rave #sunsetsessions"),
            ("this song is about becoming who you actually are", "#holyrave #renamed #melodictechno #rave #sacredtechno #christianrave #sunsetsessions #techno"),
            ("he no longer called him jacob. he called him israel. 🕊️", "#holyrave #renamed #genesis32 #melodictechno #electronicworship #sunsetsessions #tenerife #rave"),
            ("a new name. a new walk. a new frequency.", "#holyrave #renamed #melodictechno #sacredmusic #rave #undergroundtechno #sunsetsessions #ancienttruthfuturesound"),
            ("what if the rave was the place you got renamed? 🔥", "#holyrave #sunsetsessions #renamed #melodictechno #tenerife #rave #electronicworship #sacredtechno"),
            ("free weekly. tenerife. every friday. renamed.", "#holyrave #renamed #sunsetsessions #melodictechno #tenerife #rave #sacredmusic #undergroundtechno"),
        ],
        "instagram": [
            ("Jacob wrestled all night and came out limping — and renamed.\n\nSome encounters change everything. Renamed is about those encounters.", "#holyrave #renamed #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #genesis32"),
            ("He no longer called him Jacob. He called him Israel.\n\nGenesis 32. Every week on the dancefloor in Tenerife.", "#holyrave #renamed #sunsetsessions #melodictechno #genesis32 #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #christianrave #tribaltechno"),
            ("There are moments that mark you.\nAfter which you are not the same person.\n\nRenamed. Out now on Spotify.", "#holyrave #renamed #sunsetsessions #melodictechno #spotify #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #newmusic #sacredtechno"),
            ("Abraham. Jacob. Paul. History is full of people God renamed.\n\nThis track is about that moment. Free Sunset Sessions, Tenerife.", "#holyrave #renamed #sunsetsessions #melodictechno #electronicworship #tenerife #rave #undergroundtechno #techno #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #christianrave"),
            ("Identity is not fixed. You can be renamed.\n\nThat's the ancient truth behind this track.", "#holyrave #renamed #sunsetsessions #melodictechno #sacredtechno #tenerife #rave #undergroundtechno #techno #electronicworship #ancienttruthfuturesound #robertjanmastenbroek #psytrance #dancefloor #sacredmusic"),
            ("He walked with a limp after that night. But he had a new name.\n\nRenamed — electronic worship for people mid-transformation.", "#holyrave #renamed #sunsetsessions #melodictechno #electronicworship #tenerife #rave #undergroundtechno #genesis32 #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #sacredtechno"),
            ("The dancefloor is where I've seen the most people encounter something real.\n\nRenamed is for them.", "#holyrave #renamed #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #christianrave #dancefloor"),
            ("You are not who you were. Not anymore.\n\nRenamed. Free. Weekly. Tenerife.", "#holyrave #renamed #sunsetsessions #melodictechno #sacredtechno #tenerife #rave #undergroundtechno #techno #electronicworship #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #sacredmusic"),
        ],
        "youtube": [
            ("Renamed — Holy Rave Tenerife | Sacred Melodic Techno", "Jacob wrestled all night and came out renamed. This track is that story.\nRobert-Jan Mastenbroek — Ancient Truth. Future Sound.\nFree weekly Sunset Sessions in Tenerife."),
            ("Genesis 32 at 130 BPM — Renamed | Holy Rave", "He walked with a limp. But he had a new name.\nRobert-Jan Mastenbroek — streaming on Spotify now. Link in bio."),
            ("What Does Being Renamed by God Sound Like?", "Abraham. Jacob. You. Renamed by something greater.\nRobert-Jan Mastenbroek | Ancient Truth. Future Sound. Out now on Spotify."),
            ("Holy Rave Tenerife — Renamed | Sacred Electronic Music", "Identity is not fixed. You can be renamed.\nFree weekly events in Tenerife. Robert-Jan Mastenbroek — Ancient Truth. Future Sound."),
            ("Renamed — Electronic Worship for People Mid-Transformation", "The moment of encounter that changes everything. This is that moment.\nRobert-Jan Mastenbroek | Renamed out now on Spotify."),
            ("Jacob Wrestled God — Renamed | Holy Rave Sacred Techno", "Some nights the music does what nothing else can.\nRenamed. Free Sunset Sessions. Tenerife. Every week.", ),
        ],
    },

    "fire in our hands": {
        "tiktok": [
            ("tongues of fire. dancefloor. tenerife. 🔥", "#holyrave #fireinourhands #melodictechno #rave #tenerife #sunsetsessions #sacredtechno #electronicworship"),
            ("acts 2 called. it sounds like this.", "#holyrave #fireinourhands #acts2 #melodictechno #sacredtechno #christianrave #rave #sunsetsessions"),
            ("the holy spirit and the subwoofer. same vibe. 🌊", "#holyrave #fireinourhands #melodictechno #ancienttruthfuturesound #techno #electronicworship #rave #sunsetsessions"),
            ("fire in our hands. not afraid to use it.", "#holyrave #fireinourhands #sacredtechno #melodictechno #rave #undergroundtechno #sunsetsessions #tenerife"),
            ("pentecost was the original rave 👁️", "#holyrave #fireinourhands #pentecost #melodictechno #electronicworship #sacredmusic #tenerife #rave"),
            ("when the fire falls in a place that wasn't expecting it 🔥", "#holyrave #fireinourhands #melodictechno #rave #sacredtechno #christianrave #sunsetsessions #techno"),
            ("this collab hits different when you know the scripture behind it", "#holyrave #fireinourhands #melodictechno #electronicworship #sunsetsessions #tenerife #rave #undergroundtechno"),
            ("robert-jan & lucid. fire in our hands. go stream it. 🕊️", "#holyrave #fireinourhands #melodictechno #sacredtechno #rave #undergroundtechno #sunsetsessions #spotify"),
            ("free weekly events. tenerife. fire every time. 🔥", "#holyrave #sunsetsessions #fireinourhands #melodictechno #tenerife #rave #electronicworship #sacredmusic"),
            ("this is what happens when two producers who love jesus make a track together", "#holyrave #fireinourhands #melodictechno #sacredmusic #rave #undergroundtechno #sunsetsessions #tenerife"),
        ],
        "instagram": [
            ("Acts 2 — tongues of fire, a rushing wind, and 120 people who couldn't explain what was happening.\n\nFire In Our Hands. Every week in Tenerife.", "#holyrave #fireinourhands #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #acts2"),
            ("Robert-Jan Mastenbroek & LUCID — two producers, one mission.\n\nFire In Our Hands. Out now on Spotify.", "#holyrave #fireinourhands #sunsetsessions #melodictechno #collab #spotify #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #newmusic"),
            ("Jeremiah 5:14 — 'I will make my words in your mouth a fire.'\n\nThis is what that sounds like in 2026.", "#holyrave #fireinourhands #sunsetsessions #melodictechno #electronicworship #tenerife #rave #undergroundtechno #techno #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #christianrave"),
            ("The fire didn't stop at Pentecost.\n\nFree weekly Sunset Sessions in Tenerife. Come feel what we mean.", "#holyrave #fireinourhands #sunsetsessions #melodictechno #sacredtechno #tenerife #rave #undergroundtechno #techno #electronicworship #ancienttruthfuturesound #robertjanmastenbroek #psytrance #dancefloor #sacredmusic"),
            ("Some nights you walk out of a room and something has shifted.\n\nFire In Our Hands is for those nights.", "#holyrave #fireinourhands #sunsetsessions #melodictechno #electronicworship #tenerife #rave #undergroundtechno #techno #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #sacredtechno"),
            ("The dancefloor is sacred ground when the music is.\n\nRobert-Jan Mastenbroek & LUCID — Fire In Our Hands. Link in bio.", "#holyrave #fireinourhands #sunsetsessions #melodictechno #spotify #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #newmusic #sacredtechno"),
            ("We made this track because we believe sound carries something.\n\nFire In Our Hands. Streaming now. Free events every week in Tenerife.", "#holyrave #fireinourhands #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #christianrave #dancefloor"),
            ("When two people who love Jesus make a track together and the fire shows up.\n\nFire In Our Hands — out now. Ancient Truth. Future Sound.", "#holyrave #fireinourhands #sunsetsessions #melodictechno #sacredtechno #tenerife #rave #undergroundtechno #techno #electronicworship #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #collab"),
        ],
        "youtube": [
            ("Fire In Our Hands — Holy Rave Tenerife | Sacred Melodic Techno", "Robert-Jan Mastenbroek & LUCID — Acts 2 on a dancefloor.\nAncient Truth. Future Sound.\nFree weekly Sunset Sessions in Tenerife. All welcome."),
            ("Pentecost Was the Original Rave — Fire In Our Hands | Holy Rave", "Tongues of fire. A rushing wind. 120 people who couldn't explain it.\nRobert-Jan Mastenbroek & LUCID — streaming on Spotify now."),
            ("What Does Acts 2 Sound Like in Techno?", "Sacred melodic techno rooted in scripture. Fire In Our Hands — out now.\nRobert-Jan Mastenbroek | Ancient Truth. Future Sound. Free events in Tenerife."),
            ("Holy Rave — Fire In Our Hands | Robert-Jan Mastenbroek & LUCID", "The fire didn't stop at Pentecost. We're still holding it.\nFree weekly events in Tenerife. Link in bio for Spotify."),
            ("Sacred Fire on the Dancefloor — Holy Rave Tenerife", "When the music and the mission are the same thing.\nRobert-Jan Mastenbroek & LUCID — Fire In Our Hands out now on Spotify."),
            ("Fire In Our Hands — Electronic Worship | Sunset Sessions Tenerife", "Two producers. One mission. Sacred ground on the dancefloor.\nFire In Our Hands — Ancient Truth. Future Sound. Out now.", ),
        ],
    },
}

# ── Generic fallback pool (used when song isn't in the bank) ──────────────────
GENERIC_CAPTIONS = {
    "tiktok": [
        ("sacred music for every dancefloor 🌊", "#holyrave #melodictechno #rave #undergroundtechno #electronicmusic #sunsetsessions #tenerife #techno"),
        ("nobody expected worship to sound like this 👁️", "#holyrave #melodictechno #sacredtechno #christianrave #electronicworship #rave #sunsetsessions #tenerife"),
        ("free. weekly. tenerife. you're invited. 🔥", "#holyrave #sunsetsessions #melodictechno #tenerife #rave #electronicworship #sacredmusic #undergroundtechno"),
        ("126 bpm. in the name of jesus.", "#holyrave #melodictechno #sacredtechno #christianrave #rave #sunsetsessions #tenerife #electronicworship"),
        ("ancient truth. future sound.", "#holyrave #melodictechno #ancienttruthfuturesound #techno #electronicworship #rave #sunsetsessions #sacredmusic"),
        ("the dancefloor is sacred when the music is 🕊️", "#holyrave #melodictechno #electronicworship #sacredmusic #rave #undergroundtechno #sunsetsessions #tenerife"),
        ("this is what the sunset sessions sound like from the inside", "#holyrave #sunsetsessions #melodictechno #tenerife #rave #sacredtechno #undergroundtechno #electronicworship"),
        ("electronic worship. free entry. tenerife. every week. 🌅", "#holyrave #sunsetsessions #melodictechno #electronicworship #tenerife #rave #sacredmusic #undergroundtechno"),
    ],
    "instagram": [
        ("Sacred music for the dancefloor.\n\nEvery week in Tenerife. Free entry. All welcome.", "#holyrave #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #dancefloor"),
        ("The dust on the floor. The bassline in the chest. Every week in Tenerife — no ticket, no agenda.\n\nThis is the Sunset Sessions.", "#holyrave #sunsetsessions #melodictechno #sacredtechno #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #dancefloor #christianrave"),
        ("Not church. Not club. Something more honest than both.\n\nSunset Sessions — free, weekly, in the name of Jesus.", "#holyrave #sunsetsessions #melodictechno #electronicworship #tenerife #rave #undergroundtechno #techno #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #tribaltechno #christianrave #dancefloor"),
        ("Ancient Truth. Future Sound.\n\nThis is what electronic worship sounds like when it stops trying to be acceptable.", "#holyrave #sunsetsessions #melodictechno #electronicmusic #tenerife #rave #undergroundtechno #techno #electronicworship #sacredmusic #ancienttruthfuturesound #robertjanmastenbroek #psytrance #sacredtechno #christianrave"),
    ],
    "youtube": [
        ("Holy Rave Tenerife — Sacred Electronic Music Every Week", "Robert-Jan Mastenbroek — Ancient Truth. Future Sound.\nFree weekly Sunset Sessions in Tenerife. All welcome. Link in bio."),
        ("Sacred Melodic Techno | Holy Rave Sunset Sessions Tenerife", "Electronic worship every week in Tenerife. Free entry. In the name of Jesus.\nRobert-Jan Mastenbroek | Ancient Truth. Future Sound."),
        ("Holy Rave — Electronic Worship for the Dancefloor", "Not church. Not club. Something more honest than both.\nRobert-Jan Mastenbroek | Free Sunset Sessions Tenerife. Spotify link in bio."),
    ],
}


# ── Public interface ──────────────────────────────────────────────────────────

def _match_song(song_path: str) -> str:
    """Map a song file path to a key in CAPTION_POOLS."""
    name = Path(song_path).stem.lower()
    for key in CAPTION_POOLS:
        if key in name:
            return key
    return None


def get_unique_captions(song_path: str, seed: int = None) -> dict:
    """
    Return a unique set of captions for this song.

    seed: use datetime.now().timetuple().tm_yday + run_count for daily rotation.
          None = fully random.

    Returns:
    {
      'tiktok':   (caption, hashtags),
      'instagram': (caption, hashtags),
      'youtube':  (title, description),
    }
    """
    rng = random.Random(seed)
    key = _match_song(song_path)

    if key and key in CAPTION_POOLS:
        pool = CAPTION_POOLS[key]
    else:
        pool = GENERIC_CAPTIONS
        if song_path:
            logger.info(f"No specific captions for {Path(song_path).name} — using generic pool")

    tiktok   = rng.choice(pool["tiktok"])
    instagram = rng.choice(pool["instagram"])
    youtube  = rng.choice(pool["youtube"])

    return {
        "tiktok":    {"caption": tiktok[0],    "hashtags": tiktok[1]},
        "instagram": {"caption": instagram[0], "hashtags": instagram[1]},
        "youtube":   {"title": youtube[0],     "description": youtube[1]},
    }
