"""
Hook database — maps RJM tracks to their Bible verse sources and stores
viral hook variations for use in short-form content overlays.

Schema:
  tracks  — one row per track, with verified Bible verse reference
  hooks   — many hooks per track, different patterns, performance tracking

Pre-seeded with 4 verified tracks (lyrics transcribed via Whisper):
  JERICHO_FINAL          → Joshua 6:20
  Not_By_Might_FINAL     → Zechariah 4:6 + 4:10
  Let_My_People_Go_FINAL → Exodus 5:1
  Create_In_Me_A_Clean_Heart_FINAL → Psalm 51:10-12
"""

import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / 'hook_database.db'


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create database schema if it doesn't exist."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS tracks (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            filename_pattern    TEXT NOT NULL UNIQUE,
            track_name          TEXT NOT NULL,
            bible_book          TEXT NOT NULL,
            bible_chapter       INTEGER NOT NULL,
            bible_verse_start   INTEGER NOT NULL,
            bible_verse_end     INTEGER NOT NULL,
            verse_reference     TEXT NOT NULL,
            verse_text          TEXT NOT NULL,
            theme               TEXT NOT NULL,
            lyrics_excerpt      TEXT DEFAULT '',
            bpm                 INTEGER DEFAULT 0,
            created_at          TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS hooks (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            filename_pattern    TEXT NOT NULL,
            hook_text           TEXT NOT NULL,
            pattern             TEXT NOT NULL,
            bucket              TEXT NOT NULL DEFAULT 'reach',
            views               INTEGER DEFAULT 0,
            likes               INTEGER DEFAULT 0,
            shares              INTEGER DEFAULT 0,
            performance_score   REAL DEFAULT NULL,
            created_at          TEXT NOT NULL,
            FOREIGN KEY (filename_pattern) REFERENCES tracks(filename_pattern)
        );

        -- Composite index: covers the most common query pattern (track + bucket, sorted by score)
        CREATE INDEX IF NOT EXISTS idx_hooks_pattern_bucket
            ON hooks(filename_pattern, bucket, performance_score DESC);
        -- Retained for analytics queries that filter by score alone
        CREATE INDEX IF NOT EXISTS idx_hooks_score ON hooks(performance_score);
    """)
    conn.commit()
    conn.close()


def _track_exists(filename_pattern: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM tracks WHERE filename_pattern = ?", (filename_pattern,))
    result = cur.fetchone()
    conn.close()
    return result is not None


def seed_initial_data():
    """Insert the 4 pre-verified tracks and all their hooks."""
    init_db()

    now = datetime.now(timezone.utc).isoformat()

    # ──────────────────────────────────────────────────────────
    # TRACK 1: JERICHO
    # ──────────────────────────────────────────────────────────
    if not _track_exists('JERICHO'):
        add_track(
            filename_pattern='JERICHO',
            track_name='Jericho',
            bible_book='Joshua',
            bible_chapter=6,
            bible_verse_start=16,
            bible_verse_end=20,
            verse_reference='Joshua 6:20',
            verse_text=(
                'When the trumpets sounded, the army shouted, and at the sound of the trumpet, '
                'when the men gave a loud shout, the wall collapsed; so everyone charged straight '
                'in, and they took the city.'
            ),
            theme=(
                'The walls of Jericho fell when the people shouted together. '
                'Sound as weapon. Collective faith breaking walls.'
            ),
        )

        jericho_hooks = [
            # verse_ref
            ('Joshua 6:20 at 140 BPM', 'verse_ref', 'reach'),
            ('How does Joshua 6:20 sound in techno?', 'verse_ref', 'reach'),
            ('Joshua 6:20 but it\'s a rave', 'verse_ref', 'reach'),
            ('If Joshua 6:20 had a drop', 'verse_ref', 'reach'),
            ('Joshua 6:20 — the original wall collapse', 'verse_ref', 'reach'),
            ('What happens when Joshua 6:20 meets a subwoofer?', 'verse_ref', 'reach'),
            ('They never taught you Joshua 6:20 like this', 'verse_ref', 'reach'),
            ('Joshua 6:20 encoded in every kick drum', 'verse_ref', 'reach'),
            ('The original sound weapon: Joshua 6:20', 'verse_ref', 'depth'),
            ('Joshua 6:20 — walls fall at this frequency', 'verse_ref', 'depth'),

            # character
            ('If Joshua was a techno DJ, the walls would have fallen at 4am', 'character', 'reach'),
            ('When Joshua played the dancefloor, the walls came down', 'character', 'reach'),
            ('Joshua gave the people THIS', 'character', 'reach'),
            ('Joshua didn\'t ask nicely. He just turned up the sound.', 'character', 'reach'),
            ('Joshua: the original DJ who played until the walls fell', 'character', 'reach'),
            ('Imagine being in Joshua\'s army — seven days of circling, then one shout', 'character', 'depth'),
            ('Joshua never doubted the walls would fall. He just kept marching.', 'character', 'depth'),
            ('The commanders told Joshua it was impossible. He played louder.', 'character', 'reach'),
            ('Joshua\'s army didn\'t fight. They worshipped until the walls fell.', 'character', 'depth'),
            ('What Joshua knew: sound moves walls. Science confirmed it 3,000 years later.', 'character', 'depth'),

            # contrast
            ('3,200 years old. Written for tonight.', 'contrast', 'reach'),
            ('Written 1400 BCE. Built for 2026.', 'contrast', 'reach'),
            ('Ancient text. Modern frequencies.', 'contrast', 'reach'),
            ('They used trumpets. We use 30-inch subs.', 'contrast', 'reach'),
            ('Same instruction. Different instruments.', 'contrast', 'reach'),
            ('Jericho fell 3,200 years ago. The same sound is in this room tonight.', 'contrast', 'depth'),
            ('Bronze Age battle strategy: shout together. 2026 version: this drop.', 'contrast', 'reach'),
            ('No swords. No siege engines. Just sound and faith.', 'contrast', 'depth'),
            ('The oldest rave story in recorded history.', 'contrast', 'reach'),
            ('Before EDM existed, God was using sound to level walls.', 'contrast', 'reach'),

            # question
            ('What if the most powerful weapon ever deployed was a shout?', 'question', 'reach'),
            ('Why did they march in silence for six days before making a sound?', 'question', 'depth'),
            ('What would you do if God told you to win a war by shouting?', 'question', 'reach'),
            ('Can sound actually move walls?', 'question', 'reach'),
            ('What broke the walls of Jericho — the trumpet or the faith behind it?', 'question', 'depth'),
            ('What does a 3,200-year-old battle strategy have to do with a rave?', 'question', 'reach'),
            ('What if collective sound is still the most powerful force on earth?', 'question', 'depth'),
            ('Why do the lyrics say "the walls came down"? Because they actually did.', 'question', 'reach'),
            ('If sound collapsed walls in 1400 BCE, what is it doing to your chest tonight?', 'question', 'reach'),
            ('Have you ever been in a room where the bass was so loud it changed you?', 'question', 'reach'),

            # reveal
            ('This track has a secret (hint: Joshua 6:20)', 'reveal', 'reach'),
            ('The lyrics are 3,200 years old', 'reveal', 'reach'),
            ('Wait until you find out where JERICHO comes from', 'reveal', 'reach'),
            ('There\'s a Bible verse hidden in every layer of this track', 'reveal', 'reach'),
            ('The shout you hear is Joshua\'s army. I just remixed it.', 'reveal', 'reach'),
            ('Most people dance to this not knowing it\'s a war cry from 1400 BCE', 'reveal', 'reach'),
            ('The vocal sample is the exact moment the walls fell', 'reveal', 'depth'),
            ('What sounds like a drop is actually a 3,000-year-old battle', 'reveal', 'reach'),
            ('Every element of this track is in the text. Go find them.', 'reveal', 'depth'),
            ('The BPM was not random. Neither was anything else in this track.', 'reveal', 'depth'),

            # bold
            ('The walls came down.', 'bold', 'reach'),
            ('Sound is a weapon.', 'bold', 'reach'),
            ('They shouted. The wall collapsed. Take the city.', 'bold', 'reach'),
            ('When the trumpet sounded, everything changed.', 'bold', 'reach'),
            ('Faith loud enough to level stone.', 'bold', 'reach'),
            ('Collective faith breaks walls.', 'bold', 'depth'),
            ('The shout that changed history.', 'bold', 'reach'),
            ('Not a battle. A worship service.', 'bold', 'reach'),
            ('Seven days of obedience. One shout. Done.', 'bold', 'depth'),
            ('This is what obedience sounds like.', 'bold', 'depth'),

            # holy_rave
            ('The walls of Jericho fell at a rave.', 'holy_rave', 'reach'),
            ('God\'s battle plan was essentially a rave. March around. Shout.', 'holy_rave', 'reach'),
            ('3,200 years before the first club, God threw a rave and levelled a city.', 'holy_rave', 'reach'),
            ('Holy Rave: where Joshua\'s war cry becomes your bass line', 'holy_rave', 'reach'),
            ('If the Jericho army had a stage, this would be the set.', 'holy_rave', 'reach'),
            ('Sunday morning sermon, Saturday night sound.', 'holy_rave', 'reach'),
            ('Your pastor doesn\'t know what Joshua 6:20 sounds like at 140 BPM. Now you do.', 'holy_rave', 'reach'),
            ('This is what church sounds like when nobody has to whisper.', 'holy_rave', 'reach'),
            ('Techno temple, Jericho walls — tonight they fall.', 'holy_rave', 'reach'),
            ('Worship never needed pews. It needed a sound system and obedience.', 'holy_rave', 'depth'),

            # emotion
            ('That feeling when something enormous is about to break.', 'emotion', 'reach'),
            ('You\'ve been walking in circles. The shout is coming.', 'emotion', 'reach'),
            ('This is what it feels like right before everything changes.', 'emotion', 'reach'),
            ('The walls in your life — they come down the same way.', 'emotion', 'depth'),
            ('Marching in silence. Then everything at once.', 'emotion', 'reach'),
            ('For everyone who\'s been circling the same problem for six days.', 'emotion', 'depth'),
            ('The moment before breakthrough feels exactly like this.', 'emotion', 'reach'),
            ('Built for people who know what it\'s like to face something that looks impossible.', 'emotion', 'depth'),
            ('The bass drops. You remember you\'re not alone in this.', 'emotion', 'reach'),
            ('This is for the ones who kept marching when it made no sense.', 'emotion', 'depth'),
        ]

        for hook_text, pattern, bucket in jericho_hooks:
            add_hook('JERICHO', hook_text, pattern, bucket)

    # ──────────────────────────────────────────────────────────
    # TRACK 2: NOT BY MIGHT
    # ──────────────────────────────────────────────────────────
    if not _track_exists('NOT_BY_MIGHT'):
        add_track(
            filename_pattern='NOT_BY_MIGHT',
            track_name='Not By Might',
            bible_book='Zechariah',
            bible_chapter=4,
            bible_verse_start=6,
            bible_verse_end=10,
            verse_reference='Zechariah 4:6',
            verse_text=(
                'Not by might nor by power, but by my Spirit, says the LORD Almighty. '
                'Do not despise these small beginnings, for the LORD rejoices to see the work begin.'
            ),
            theme=(
                'Human effort cannot accomplish what the Spirit can. '
                'The small beginning matters. God works through weakness.'
            ),
        )

        not_by_might_hooks = [
            # verse_ref
            ('Zechariah 4:6 at 138 BPM', 'verse_ref', 'reach'),
            ('How does Zechariah 4:6 sound in techno?', 'verse_ref', 'reach'),
            ('Zechariah 4:6 but it\'s a rave', 'verse_ref', 'reach'),
            ('If Zechariah 4:6 had a drop', 'verse_ref', 'reach'),
            ('Zechariah 4:6 — the verse the driven need to hear', 'verse_ref', 'reach'),
            ('What does Zechariah 4:6 mean for a DJ playing 300 people?', 'verse_ref', 'depth'),
            ('They never taught you Zechariah 4:6 like this', 'verse_ref', 'reach'),
            ('Zechariah 4:6 encoded in every hi-hat', 'verse_ref', 'reach'),
            ('2,500 years of Zechariah 4:6 meeting this room right now', 'verse_ref', 'depth'),
            ('The small beginning: Zechariah 4:6', 'verse_ref', 'depth'),

            # character
            ('If Zerubbabel was a techno DJ he would play to empty rooms and not care', 'character', 'reach'),
            ('When Zerubbabel played the dancefloor, nobody laughed at the small start', 'character', 'reach'),
            ('Zerubbabel gave the people THIS', 'character', 'reach'),
            ('The prophet told Zerubbabel: not by might. He built anyway.', 'character', 'reach'),
            ('Zerubbabel: the builder who was told his start was too small to matter', 'character', 'depth'),
            ('An angel appeared to Zechariah and said: tell Zerubbabel — it\'s not about his power.', 'character', 'depth'),
            ('The hands that laid the foundation will also finish it. — Zechariah 4:9', 'character', 'depth'),
            ('Zerubbabel started with almost nothing. The angel said that was exactly the point.', 'character', 'depth'),
            ('Same hands that started it will finish it. Zerubbabel knew this.', 'character', 'reach'),
            ('What Zerubbabel understood that most founders never learn.', 'character', 'reach'),

            # contrast
            ('2,500 years old. Written for tonight.', 'contrast', 'reach'),
            ('Written 520 BCE. Built for 2026.', 'contrast', 'reach'),
            ('Ancient text. Modern frequencies.', 'contrast', 'reach'),
            ('They had no army. They rebuilt with a word from God.', 'contrast', 'reach'),
            ('Same instruction. Different instruments.', 'contrast', 'reach'),
            ('Spoken to a man rebuilding a temple with his bare hands. Now it\'s techno.', 'contrast', 'depth'),
            ('The Persian Empire could not stop this. Neither can your circumstances.', 'contrast', 'reach'),
            ('No military. No money. Just Spirit.', 'contrast', 'depth'),
            ('2,500 years before productivity culture, God said: your effort isn\'t the point.', 'contrast', 'reach'),
            ('Before hustle culture. Before optimisation. There was this.', 'contrast', 'reach'),

            # question
            ('What if your biggest limitation is how hard you\'re trying?', 'question', 'reach'),
            ('What does "not by might" mean when you\'re already exhausted?', 'question', 'depth'),
            ('Why does God say "do not despise small beginnings"?', 'question', 'reach'),
            ('What if the small thing you\'re doing right now is exactly where the Spirit moves?', 'question', 'depth'),
            ('What if everything you\'ve been forcing was meant to be surrendered?', 'question', 'reach'),
            ('Have you ever worked so hard at something that you forgot to let it breathe?', 'question', 'reach'),
            ('Why do the people who try hardest often burn out first?', 'question', 'depth'),
            ('What\'s the difference between effort and force?', 'question', 'reach'),
            ('What if your small start is the entire point?', 'question', 'reach'),
            ('Can something built on Spirit outlast something built on might?', 'question', 'depth'),

            # reveal
            ('This track has a secret (hint: Zechariah 4:6)', 'reveal', 'reach'),
            ('The lyrics are 2,500 years old', 'reveal', 'reach'),
            ('Wait until you find out where NOT BY MIGHT comes from', 'reveal', 'reach'),
            ('There\'s a conversation between an angel and a prophet in this track', 'reveal', 'depth'),
            ('The title isn\'t a metaphor. It\'s a direct quote from 520 BCE.', 'reveal', 'reach'),
            ('Most people sing along to this not knowing it\'s a prophetic decree', 'reveal', 'reach'),
            ('The "small beginnings" in this track? That\'s exactly where I started.', 'reveal', 'depth'),
            ('Every layer in this track is from one angel\'s message to one exhausted builder.', 'reveal', 'depth'),
            ('The drop is where the angel speaks. Listen again.', 'reveal', 'reach'),
            ('I built this track the same way Zerubbabel built the temple. Small. Faithful. Steady.', 'reveal', 'depth'),

            # bold
            ('Not by might. Not by power. By Spirit.', 'bold', 'reach'),
            ('Your effort is not the point.', 'bold', 'reach'),
            ('Do not despise small beginnings.', 'bold', 'reach'),
            ('The Spirit does what strategy cannot.', 'bold', 'depth'),
            ('Stop forcing. Start yielding.', 'bold', 'reach'),
            ('What you\'re trying to accomplish by working harder, Spirit does differently.', 'bold', 'depth'),
            ('God rejoices at the small start.', 'bold', 'reach'),
            ('The smallest step taken in Spirit is greater than the greatest leap taken by might.', 'bold', 'depth'),
            ('The hands that started this will finish it.', 'bold', 'reach'),
            ('Not by might. Full stop.', 'bold', 'reach'),

            # holy_rave
            ('A 2,500-year-old prophetic decree just became a rave.', 'holy_rave', 'reach'),
            ('God told a builder "not by might" and I turned it into techno.', 'holy_rave', 'reach'),
            ('Holy Rave: where the angel\'s decree to Zerubbabel becomes your bass line', 'holy_rave', 'reach'),
            ('If the prophet Zechariah had a soundsystem, it sounded like this.', 'holy_rave', 'reach'),
            ('Sunday sermon meets Saturday night: "not by might" at 138 BPM', 'holy_rave', 'reach'),
            ('Your pastor has never heard Zechariah 4:6 like this.', 'holy_rave', 'reach'),
            ('This is what church sounds like for people who can\'t sit still.', 'holy_rave', 'reach'),
            ('The angel\'s message to the exhausted builder — now with a 4/4 kick.', 'holy_rave', 'reach'),
            ('Not by might, not by power, but by Spirit — and by this sub bass.', 'holy_rave', 'reach'),
            ('Techno temple. Ancient decree. Let go.', 'holy_rave', 'depth'),

            # emotion
            ('For everyone who is tired of trying so hard.', 'emotion', 'reach'),
            ('You\'ve been running on effort. This is permission to stop.', 'emotion', 'reach'),
            ('That feeling when you realise you\'ve been carrying something you were never meant to carry.', 'emotion', 'depth'),
            ('Not by might. You can breathe now.', 'emotion', 'reach'),
            ('Built for the exhausted ones who are still showing up anyway.', 'emotion', 'depth'),
            ('The small start was always enough. You just didn\'t know it yet.', 'emotion', 'depth'),
            ('For the builders. For the people rebuilding after collapse. This is yours.', 'emotion', 'depth'),
            ('The bass is the breath you forgot to take.', 'emotion', 'reach'),
            ('Sometimes the most spiritual thing you can do is let go of the wheel.', 'emotion', 'depth'),
            ('This is for the people working so hard they forgot why they started.', 'emotion', 'reach'),
        ]

        for hook_text, pattern, bucket in not_by_might_hooks:
            add_hook('NOT_BY_MIGHT', hook_text, pattern, bucket)

    # ──────────────────────────────────────────────────────────
    # TRACK 3: LET MY PEOPLE GO
    # ──────────────────────────────────────────────────────────
    if not _track_exists('LET_MY_PEOPLE_GO'):
        add_track(
            filename_pattern='LET_MY_PEOPLE_GO',
            track_name='Let My People Go',
            bible_book='Exodus',
            bible_chapter=5,
            bible_verse_start=1,
            bible_verse_end=1,
            verse_reference='Exodus 5:1',
            verse_text=(
                'Afterward Moses and Aaron went to Pharaoh and said, '
                '"This is what the LORD, the God of Israel, says: Let my people go."'
            ),
            theme=(
                'Moses confronted the most powerful ruler on earth demanding freedom for the enslaved. '
                'God speaks to power through the powerless.'
            ),
        )

        let_my_people_go_hooks = [
            # verse_ref
            ('Exodus 5:1 at 135 BPM', 'verse_ref', 'reach'),
            ('How does Exodus 5:1 sound in techno?', 'verse_ref', 'reach'),
            ('Exodus 5:1 but it\'s a rave', 'verse_ref', 'reach'),
            ('If Exodus 5:1 had a drop', 'verse_ref', 'reach'),
            ('Exodus 5:1 — four words that changed history', 'verse_ref', 'reach'),
            ('What does Exodus 5:1 mean when it\'s 135 BPM?', 'verse_ref', 'depth'),
            ('They never taught you Exodus 5:1 like this', 'verse_ref', 'reach'),
            ('Exodus 5:1 — the most confrontational verse in the Bible', 'verse_ref', 'depth'),
            ('The shortest command in Exodus: 5:1', 'verse_ref', 'reach'),
            ('Exodus 5:1 encoded in every synth stab', 'verse_ref', 'reach'),

            # character
            ('If Moses was a techno DJ he would play to Pharaoh and not flinch', 'character', 'reach'),
            ('When Moses played the dancefloor, Pharaoh had no answer', 'character', 'reach'),
            ('Moses gave the people THIS', 'character', 'reach'),
            ('Moses walked into the most powerful room on earth with four words.', 'character', 'reach'),
            ('Moses had a stutter. He still delivered the message.', 'character', 'reach'),
            ('Moses: the reluctant prophet who became the most dangerous voice in Egypt', 'character', 'depth'),
            ('God didn\'t send a general. He sent a shepherd with a staff.', 'character', 'reach'),
            ('Moses was afraid. He went anyway. That\'s the entire story.', 'character', 'depth'),
            ('Imagine being Moses. Raised in the palace. Returning to free the people you left behind.', 'character', 'depth'),
            ('Pharaoh had the army. Moses had the word of God. We know how it ended.', 'character', 'reach'),

            # contrast
            ('3,400 years old. Written for tonight.', 'contrast', 'reach'),
            ('Written 1400 BCE. Built for 2026.', 'contrast', 'reach'),
            ('Ancient text. Modern frequencies.', 'contrast', 'reach'),
            ('They had no weapons. They had a word.', 'contrast', 'reach'),
            ('Said to Pharaoh. Now echoing in a club.', 'contrast', 'reach'),
            ('The most powerful ruler of the ancient world heard these four words. So did you.', 'contrast', 'depth'),
            ('Pharaoh\'s empire fell to four words and a God who keeps promises.', 'contrast', 'depth'),
            ('No army. No weapons. Just: let my people go.', 'contrast', 'reach'),
            ('3,400 years before liberation movements, this was the first demand for freedom.', 'contrast', 'depth'),
            ('The loudest thing ever said in Egypt was this quiet.', 'contrast', 'depth'),

            # question
            ('What gives a shepherd the courage to stand before a king and demand freedom?', 'question', 'depth'),
            ('Why did Moses keep going back to Pharaoh after every refusal?', 'question', 'depth'),
            ('What does "let my people go" mean when the people are still enslaved?', 'question', 'depth'),
            ('Who is your Pharaoh?', 'question', 'reach'),
            ('What are you still waiting for permission to walk away from?', 'question', 'reach'),
            ('What room have you been afraid to walk into?', 'question', 'reach'),
            ('Why did God choose the person least qualified to deliver the most important message?', 'question', 'depth'),
            ('What if "let my people go" is still being said tonight?', 'question', 'reach'),
            ('Can four words change the course of history?', 'question', 'reach'),
            ('What does freedom sound like in techno?', 'question', 'reach'),

            # reveal
            ('This track has a secret (hint: Exodus 5:1)', 'reveal', 'reach'),
            ('The lyrics are 3,400 years old', 'reveal', 'reach'),
            ('Wait until you find out where LET MY PEOPLE GO comes from', 'reveal', 'reach'),
            ('The four words in this track were originally said to Pharaoh of Egypt', 'reveal', 'reach'),
            ('Most people dance to this not knowing it\'s a 3,400-year-old liberation demand', 'reveal', 'reach'),
            ('Every time this plays, Moses\' words echo in a room Pharaoh will never enter.', 'reveal', 'depth'),
            ('The drop is the moment Moses said it. Right to his face.', 'reveal', 'reach'),
            ('This is the most ancient freedom chant in recorded history. Now it\'s techno.', 'reveal', 'depth'),
            ('The vocal was designed to sound like a decree. Because it is one.', 'reveal', 'depth'),
            ('I built this track because the demand for freedom never expires.', 'reveal', 'depth'),

            # bold
            ('Let my people go.', 'bold', 'reach'),
            ('God speaks to power through the powerless.', 'bold', 'depth'),
            ('Moses had nothing. God had everything. They went to Pharaoh.', 'bold', 'reach'),
            ('Freedom demanded. Freedom received.', 'bold', 'reach'),
            ('The demand was simple. The answer changed the world.', 'bold', 'reach'),
            ('Four words. One God. No army necessary.', 'bold', 'reach'),
            ('Pharaoh said no ten times. God said yes once.', 'bold', 'reach'),
            ('The enslaved were not forgotten. They were never forgotten.', 'bold', 'depth'),
            ('Liberation is not requested. It is declared.', 'bold', 'depth'),
            ('Let. My. People. Go.', 'bold', 'reach'),

            # holy_rave
            ('Moses\' demand to Pharaoh just became a rave.', 'holy_rave', 'reach'),
            ('God sent Moses to Pharaoh. I sent this track to the dancefloor.', 'holy_rave', 'reach'),
            ('Holy Rave: where Moses\' declaration to Pharaoh becomes your bass line', 'holy_rave', 'reach'),
            ('If Moses had a soundsystem instead of a staff, it sounded like this.', 'holy_rave', 'reach'),
            ('Sunday morning freedom song. Saturday night frequency.', 'holy_rave', 'reach'),
            ('Your pastor knows this verse. Not like this.', 'holy_rave', 'reach'),
            ('This is what church sounds like when the oppressed dance.', 'holy_rave', 'reach'),
            ('Exodus 5:1 was always meant to be heard at volume.', 'holy_rave', 'reach'),
            ('Liberation theology. Four-four time.', 'holy_rave', 'depth'),
            ('The Exodus was always a rave. It just needed the right producer.', 'holy_rave', 'reach'),

            # emotion
            ('For everyone who has been waiting for permission to be free.', 'emotion', 'reach'),
            ('The chains in your life — this is what it sounds like when they break.', 'emotion', 'reach'),
            ('That feeling when you finally say the thing you\'ve been afraid to say.', 'emotion', 'reach'),
            ('Built for the people who are still waiting to be let go.', 'emotion', 'depth'),
            ('Freedom doesn\'t feel like relief. It feels like this drop.', 'emotion', 'reach'),
            ('For everyone who has been enslaved to something that was never God\'s plan.', 'emotion', 'depth'),
            ('The bass is the sound of a door finally opening.', 'emotion', 'reach'),
            ('You don\'t have to ask permission anymore. You never did.', 'emotion', 'reach'),
            ('This is for the people marching toward something they\'ve never seen before.', 'emotion', 'depth'),
            ('The feeling you get when you realise you were always meant to be free.', 'emotion', 'reach'),
        ]

        for hook_text, pattern, bucket in let_my_people_go_hooks:
            add_hook('LET_MY_PEOPLE_GO', hook_text, pattern, bucket)

    # ──────────────────────────────────────────────────────────
    # TRACK 4: CREATE IN ME A CLEAN HEART
    # ──────────────────────────────────────────────────────────
    if not _track_exists('CREATE_CLEAN_HEART'):
        add_track(
            filename_pattern='CREATE_CLEAN_HEART',
            track_name='Create In Me A Clean Heart',
            bible_book='Psalms',
            bible_chapter=51,
            bible_verse_start=10,
            bible_verse_end=12,
            verse_reference='Psalm 51:10-12',
            verse_text=(
                'Create in me a clean heart, O God, and renew a right spirit within me. '
                'Cast me not away from your presence, and take not your Holy Spirit from me. '
                'Restore to me the joy of your salvation.'
            ),
            theme=(
                'King David wrote this after his greatest failure. '
                'Radical honesty with God. Restoration over perfection. '
                'The most vulnerable prayer ever written.'
            ),
        )

        clean_heart_hooks = [
            # verse_ref
            ('Psalm 51:10 at 128 BPM', 'verse_ref', 'reach'),
            ('How does Psalm 51:10-12 sound in techno?', 'verse_ref', 'reach'),
            ('Psalm 51:10 but it\'s a rave', 'verse_ref', 'reach'),
            ('If Psalm 51:10-12 had a drop', 'verse_ref', 'reach'),
            ('Psalm 51:10 — the prayer David wrote after everything fell apart', 'verse_ref', 'depth'),
            ('What does Psalm 51:10 mean when it\'s 128 BPM?', 'verse_ref', 'depth'),
            ('They never taught you Psalm 51 like this', 'verse_ref', 'reach'),
            ('Psalm 51:10-12 encoded in every reverb tail', 'verse_ref', 'reach'),
            ('3,000 years of Psalm 51 meeting this room right now', 'verse_ref', 'depth'),
            ('The prayer before this track: Psalm 51:10', 'verse_ref', 'depth'),

            # character
            ('If David was a techno DJ, he would have written Psalm 51 in the booth at 3am', 'character', 'reach'),
            ('When David played the dancefloor, it was always the most honest set', 'character', 'reach'),
            ('David gave the people THIS', 'character', 'reach'),
            ('David was king, warrior, poet — and he still needed this prayer.', 'character', 'reach'),
            ('David: the man after God\'s own heart, writing the most broken prayer ever', 'character', 'depth'),
            ('David didn\'t pretend he was okay. He wrote Psalm 51 instead.', 'character', 'reach'),
            ('King David at his lowest point wrote the most honest words in Scripture.', 'character', 'depth'),
            ('David had everything and lost his integrity. Then he wrote this.', 'character', 'depth'),
            ('What David understood: you can come to God exactly as you are.', 'character', 'reach'),
            ('The king fell. The king prayed. The king was restored. That\'s the whole story.', 'character', 'depth'),

            # contrast
            ('3,000 years old. Written for tonight.', 'contrast', 'reach'),
            ('Written 1000 BCE. Built for 2026.', 'contrast', 'reach'),
            ('Ancient text. Modern frequencies.', 'contrast', 'reach'),
            ('A king\'s prayer became a rave. Nothing changes.', 'contrast', 'reach'),
            ('Written in grief. Played in worship.', 'contrast', 'reach'),
            ('David wrote this after his greatest failure. It outlasted his kingdom by 3,000 years.', 'contrast', 'depth'),
            ('No throne. No crown. Just David and God and radical honesty.', 'contrast', 'depth'),
            ('The most powerful man in Israel was the most broken person in the room.', 'contrast', 'depth'),
            ('3,000 years before therapy, David invented radical emotional honesty with God.', 'contrast', 'reach'),
            ('The prayer of a broken king became the anthem of every honest person who followed.', 'contrast', 'depth'),

            # question
            ('What do you do when you\'ve made the worst mistake of your life?', 'question', 'reach'),
            ('Why did David pray "restore the joy" — not "restore the throne"?', 'question', 'depth'),
            ('What does it mean to ask God to "create" something in you — not fix, but create?', 'question', 'depth'),
            ('What if God is not done with you even after your worst failure?', 'question', 'reach'),
            ('What\'s the difference between guilt and repentance?', 'question', 'depth'),
            ('Why is the most powerful prayer also the most vulnerable?', 'question', 'depth'),
            ('Can you ask God for a clean heart while still holding onto what made it dirty?', 'question', 'depth'),
            ('What do you do with a joy you\'ve lost?', 'question', 'reach'),
            ('What if Psalm 51 was written for your 3am?', 'question', 'reach'),
            ('Have you ever prayed something you were afraid to say out loud?', 'question', 'reach'),

            # reveal
            ('This track has a secret (hint: Psalm 51:10-12)', 'reveal', 'reach'),
            ('The lyrics are 3,000 years old', 'reveal', 'reach'),
            ('Wait until you find out where CREATE IN ME A CLEAN HEART comes from', 'reveal', 'reach'),
            ('King David wrote this after committing adultery and ordering a man\'s death.', 'reveal', 'depth'),
            ('Most people cry to this not knowing it\'s a king\'s prayer after total collapse', 'reveal', 'reach'),
            ('The prayer in this track is the most honest thing ever recorded in Scripture.', 'reveal', 'depth'),
            ('The "create" in this title is intentional — David didn\'t ask God to fix him. He asked to be remade.', 'reveal', 'depth'),
            ('Every lyric in this track is verbatim from a 3,000-year-old prayer.', 'reveal', 'reach'),
            ('The reverb is long because some prayers need room to breathe.', 'reveal', 'depth'),
            ('I built this track the same way David wrote the psalm — at my lowest point.', 'reveal', 'depth'),

            # bold
            ('Create in me a clean heart.', 'bold', 'reach'),
            ('Restoration over perfection.', 'bold', 'reach'),
            ('Radical honesty with God. That\'s Psalm 51.', 'bold', 'depth'),
            ('Cast me not away from your presence.', 'bold', 'reach'),
            ('Restore to me the joy of your salvation.', 'bold', 'reach'),
            ('The most vulnerable prayer ever written is also the most powerful.', 'bold', 'depth'),
            ('God doesn\'t need you to be perfect. He needs you to be honest.', 'bold', 'reach'),
            ('A broken king. A faithful God. A new beginning.', 'bold', 'reach'),
            ('You can\'t clean your own heart. That\'s the whole point.', 'bold', 'depth'),
            ('Renew a right spirit within me. He meant it. God did it.', 'bold', 'depth'),

            # holy_rave
            ('David\'s prayer after his greatest failure just became a rave.', 'holy_rave', 'reach'),
            ('God heard a broken king at 1000 BCE. He\'s still listening tonight.', 'holy_rave', 'reach'),
            ('Holy Rave: where David\'s most vulnerable prayer becomes your bass line', 'holy_rave', 'reach'),
            ('If David had a soundsystem in Jerusalem, it sounded like this.', 'holy_rave', 'reach'),
            ('Sunday morning repentance song. Saturday night frequency.', 'holy_rave', 'reach'),
            ('Your pastor cried to Psalm 51. He\'s never heard it at 128 BPM.', 'holy_rave', 'reach'),
            ('This is what church sounds like for people who have fallen and gotten up.', 'holy_rave', 'reach'),
            ('Psalm 51 was always meant to be felt in your chest, not just read with your eyes.', 'holy_rave', 'reach'),
            ('The most honest prayer in the Bible. Now at club volume.', 'holy_rave', 'reach'),
            ('Clean heart. Loud bass. Same God.', 'holy_rave', 'reach'),

            # emotion
            ('For everyone who knows what it feels like to need a clean start.', 'emotion', 'reach'),
            ('That feeling when you\'ve been carrying something for too long and you finally put it down.', 'emotion', 'reach'),
            ('You don\'t have to pretend you\'re okay here.', 'emotion', 'reach'),
            ('Built for the people who came to the dancefloor with something heavy on their heart.', 'emotion', 'depth'),
            ('The bass is holding what you can\'t say out loud.', 'emotion', 'reach'),
            ('For the ones who know what it\'s like to lose the joy and desperately want it back.', 'emotion', 'depth'),
            ('Restoration sounds like this. Let it in.', 'emotion', 'reach'),
            ('This is what healing music sounds like.', 'emotion', 'reach'),
            ('Psalm 51 is for the 3am version of you. So is this track.', 'emotion', 'reach'),
            ('For everyone who has ever prayed "just don\'t give up on me."', 'emotion', 'depth'),
        ]

        for hook_text, pattern, bucket in clean_heart_hooks:
            add_hook('CREATE_CLEAN_HEART', hook_text, pattern, bucket)

    # Seed all 31 Spotify-live songs
    _seed_spotify_catalog()


def _seed_spotify_catalog():
    """
    All 31 Spotify-live songs — Bible verse data + superviral hooks.
    Each song gets reach / follow / spotify bucket hooks.
    Called once from seed_initial_data(); idempotent (INSERT OR IGNORE).
    """

    SONGS = [
        # ── ACTIVE FOCUS SONGS (full hook sets) ───────────────────────────────

        {
            'pattern': 'HALLELUYAH',
            'name': 'Halleluyah',
            'book': 'Psalms', 'chapter': 150, 'v_start': 1, 'v_end': 6,
            'ref': 'Psalm 150',
            'verse': (
                'Praise him with the sound of the trumpet; praise him with the lute and harp! '
                'Praise him with tambourine and dancing; praise him with strings and flute! '
                'Praise him with loud cymbals; praise him with resounding cymbals. '
                'Let everything that has breath praise the LORD.'
            ),
            'theme': 'Total praise — every instrument, every body, every breath. The dancefloor is scripture.',
            'hooks': [
                # reach
                ('The last psalm ends with a rave.', 'bold', 'reach'),
                ('Psalm 150 told you to dance. You just forgot.', 'contrast', 'reach'),
                ('This is what Psalm 150 sounds like at 128 BPM.', 'verse_ref', 'reach'),
                ('The Bible literally says "praise him with dancing." This is that.', 'reveal', 'reach'),
                ('"Let everything that has breath praise the LORD" — that includes this room.', 'verse_ref', 'reach'),
                ('God wrote the setlist. Psalm 150. Every instrument. No exceptions.', 'holy_rave', 'reach'),
                ('What if the rave was always the most Biblical thing you could do?', 'question', 'reach'),
                ('Psalm 150 was not a suggestion.', 'bold', 'reach'),
                ('Tambourines, trumpets, dancing. The Bible invented the rave.', 'contrast', 'reach'),
                ('Your church was too quiet. Psalm 150 was not.', 'holy_rave', 'reach'),
                ('Halleluyah is not a word. It\'s a command.', 'bold', 'reach'),
                ('3,000 years ago this was the instruction: louder. bigger. more.', 'contrast', 'reach'),
                ('The last song in the Psalms ends on a dancefloor. Read Psalm 150.', 'reveal', 'reach'),
                ('Everything that has breath. Everything. That means you.', 'emotion', 'reach'),
                ('This is the sound of Psalm 150 being obeyed.', 'holy_rave', 'reach'),
                # follow
                ('Every week we do this in Tenerife. Follow to come with us.', 'holy_rave', 'follow'),
                ('I make Psalm 150 into techno every week. Follow for more.', 'reveal', 'follow'),
                ('Robert-Jan Mastenbroek — Holy Rave, Tenerife. Follow if this is your frequency.', 'character', 'follow'),
                ('Sunset Sessions, free, every week. Follow and I\'ll tell you when.', 'holy_rave', 'follow'),
                ('If Psalm 150 sounds like this to you too — follow. You\'re home.', 'emotion', 'follow'),
                # spotify
                ('Halleluyah — full track on Spotify. Link in bio.', 'reveal', 'spotify'),
                ('Stream Halleluyah on Spotify. Save it. Share it.', 'bold', 'spotify'),
                ('Full version on Spotify — Psalm 150 at full volume.', 'verse_ref', 'spotify'),
                ('Add Halleluyah to your playlist. It\'s already on Spotify.', 'bold', 'spotify'),
                ('Save Halleluyah on Spotify. Play it loud. Psalm 150.', 'emotion', 'spotify'),
            ],
        },

        {
            'pattern': 'RENAMED',
            'name': 'Renamed',
            'book': 'Revelation', 'chapter': 2, 'v_start': 17, 'v_end': 17,
            'ref': 'Revelation 2:17',
            'verse': (
                'Whoever has ears, let them hear what the Spirit says to the churches. '
                'To the one who is victorious, I will give some of the hidden manna. '
                'I will also give that person a white stone with a new name written on it, '
                'known only to the one who receives it.'
            ),
            'theme': 'God gives a new name — identity beyond shame, beyond the past. The overcomer receives it.',
            'hooks': [
                # reach
                ('God gives you a new name. Revelation 2:17.', 'verse_ref', 'reach'),
                ('What if God already renamed you and you just don\'t know it yet?', 'question', 'reach'),
                ('A white stone with a new name. Revelation 2:17. That\'s this track.', 'reveal', 'reach'),
                ('You are not what they called you.', 'bold', 'reach'),
                ('New name. White stone. Nobody else gets to read it.', 'bold', 'reach'),
                ('The name they gave you is not the name God wrote.', 'contrast', 'reach'),
                ('Revelation 2:17 — a secret name given only to the one who overcomes.', 'verse_ref', 'reach'),
                ('What does it feel like to be renamed by the one who made you?', 'question', 'reach'),
                ('This is for everyone who has been defined by their worst moment.', 'emotion', 'reach'),
                ('Hidden manna. White stone. New name. That\'s the overcomer\'s reward.', 'verse_ref', 'reach'),
                ('The old name was a lie. The new name is in the stone.', 'contrast', 'reach'),
                ('You overcame it. The stone is waiting.', 'emotion', 'reach'),
                ('God\'s name for you is not failure, shame, or mistake.', 'bold', 'reach'),
                ('Renamed. Not updated. Renamed.', 'bold', 'reach'),
                ('This track is for everyone who needed to hear: that name was never yours.', 'emotion', 'reach'),
                # follow
                ('Follow for more Holy Rave. Tenerife. Revelation 2:17 at 130 BPM.', 'verse_ref', 'follow'),
                ('I make this music for the renamed ones. Follow if that\'s you.', 'emotion', 'follow'),
                ('Weekly Sunset Sessions in Tenerife — Holy Rave. Follow Robert-Jan Mastenbroek.', 'character', 'follow'),
                ('This is what I build in Tenerife every week. Follow to hear it live.', 'holy_rave', 'follow'),
                ('For the overcomers. Follow for more.', 'emotion', 'follow'),
                # spotify
                ('Renamed — full track on Spotify. Revelation 2:17 at full volume.', 'verse_ref', 'spotify'),
                ('Save Renamed on Spotify. This one is yours.', 'emotion', 'spotify'),
                ('Full track on Spotify — Robert-Jan Mastenbroek — Renamed.', 'bold', 'spotify'),
                ('Stream Renamed. Add it. Share it with someone who needs it.', 'emotion', 'spotify'),
                ('Renamed — on Spotify now. The full version hits harder.', 'reveal', 'spotify'),
            ],
        },

        {
            'pattern': 'FIRE IN OUR HANDS',
            'name': 'Fire In Our Hands',
            'book': 'Jeremiah', 'chapter': 23, 'v_start': 29, 'v_end': 29,
            'ref': 'Jeremiah 23:29',
            'verse': (
                '"Is not my word like fire," declares the LORD, '
                '"and like a hammer that breaks a rock in pieces?"'
            ),
            'theme': 'The word of God is fire — unstoppable, consuming, breaking what is hardened.',
            'hooks': [
                # reach
                ('"Is not my word like fire?" — Jeremiah 23:29. This is that fire.', 'verse_ref', 'reach'),
                ('Fire in our hands. Jeremiah 23:29.', 'verse_ref', 'reach'),
                ('God called his word fire. We called it a track.', 'contrast', 'reach'),
                ('The fire that breaks rock into pieces. Jeremiah 23:29.', 'verse_ref', 'reach'),
                ('What if the bass is the hammer and the rock is everything in the way?', 'question', 'reach'),
                ('Jeremiah 23:29 at 129 BPM. This is what fire sounds like.', 'verse_ref', 'reach'),
                ('2,600 years ago God said his word was fire. This is the remix.', 'contrast', 'reach'),
                ('Not metaphor. Fire. Hammer. Breaks rock. Jeremiah 23:29.', 'bold', 'reach'),
                ('The fire is in the room. You can feel it.', 'emotion', 'reach'),
                ('Sacred fire. Dancefloor. Same thing.', 'holy_rave', 'reach'),
                ('What does a word that breaks rock sound like in techno?', 'question', 'reach'),
                ('The hammer of God at 129 BPM.', 'bold', 'reach'),
                ('Fire that doesn\'t consume you. Fire that frees you.', 'contrast', 'reach'),
                ('This is what it sounds like when prophecy meets a subwoofer.', 'holy_rave', 'reach'),
                ('For everyone holding fire they don\'t know what to do with.', 'emotion', 'reach'),
                # follow
                ('Holy Rave — fire, techno, Tenerife. Follow Robert-Jan Mastenbroek.', 'holy_rave', 'follow'),
                ('Every week in Tenerife. Follow for the fire sessions.', 'character', 'follow'),
                ('If you feel this — follow. We do this every week at sunset.', 'emotion', 'follow'),
                ('This is what I build. Jeremiah 23:29 into electronic music. Follow for more.', 'reveal', 'follow'),
                ('Free weekly Sunset Sessions, Tenerife. Follow to find us.', 'holy_rave', 'follow'),
                # spotify
                ('Fire In Our Hands — on Spotify now. Full track. Link in bio.', 'bold', 'spotify'),
                ('Stream Fire In Our Hands. Robert-Jan Mastenbroek & LUCID. Spotify.', 'bold', 'spotify'),
                ('Jeremiah 23:29 — full version on Spotify. Save it.', 'verse_ref', 'spotify'),
                ('Add Fire In Our Hands to your playlist. It hits different on repeat.', 'emotion', 'spotify'),
                ('Full track on Spotify — Fire In Our Hands. This is the one.', 'reveal', 'spotify'),
            ],
        },

        # ── REMAINING 28 SONGS ────────────────────────────────────────────────

        {
            'pattern': 'SHEMA',
            'name': 'Shema',
            'book': 'Deuteronomy', 'chapter': 6, 'v_start': 4, 'v_end': 5,
            'ref': 'Deuteronomy 6:4-5',
            'verse': 'Hear, O Israel: The LORD our God, the LORD is one. Love the LORD your God with all your heart and with all your soul and with all your strength.',
            'theme': 'The foundational declaration of faith — undivided love, complete surrender.',
            'hooks': [
                ('The most important sentence ever spoken. Deuteronomy 6:4. In techno.', 'verse_ref', 'reach'),
                ('3,400 years old. Still the most important instruction ever given.', 'contrast', 'reach'),
                ('Hear, O Israel. Now hear it at 130 BPM.', 'verse_ref', 'reach'),
                ('The Shema is not a prayer. It\'s the foundation of everything.', 'bold', 'reach'),
                ('All your heart. All your soul. All your strength. This is that.', 'verse_ref', 'reach'),
                ('What does undivided love sound like? This.', 'question', 'reach'),
                ('The LORD is one. All your strength. Deuteronomy 6:4-5.', 'bold', 'reach'),
                ('This is the prayer Jewish people have said for 3,000 years. Now it\'s a rave.', 'holy_rave', 'reach'),
                ('With all your strength — including your legs.', 'emotion', 'reach'),
                ('The Shema at Sinai. The Shema on a dancefloor. Same truth.', 'contrast', 'reach'),
                ('Follow for more ancient text turned sacred techno.', 'holy_rave', 'follow'),
                ('Weekly Holy Rave, Tenerife. Deuteronomy 6 at the Atlantic. Follow.', 'character', 'follow'),
                ('Shema — full track on Spotify. The foundation.', 'bold', 'spotify'),
                ('Stream Shema on Spotify. All your heart. All your strength.', 'verse_ref', 'spotify'),
            ],
        },

        {
            'pattern': 'ABBA',
            'name': 'Abba',
            'book': 'Romans', 'chapter': 8, 'v_start': 15, 'v_end': 16,
            'ref': 'Romans 8:15-16',
            'verse': 'The Spirit you received does not make you slaves, so that you live in fear again; rather, the Spirit you received brought about your adoption to sonship. And by him we cry, "Abba, Father." The Spirit himself testifies with our spirit that we are God\'s children.',
            'theme': 'Adopted — not slaves, not strangers. Sons and daughters. The Spirit confirms it.',
            'hooks': [
                ('You are not a slave. You are a son. Romans 8:15.', 'bold', 'reach'),
                ('The Spirit says: you belong here. Romans 8:16.', 'verse_ref', 'reach'),
                ('Abba — Father. Not duty. Not fear. Sonship.', 'bold', 'reach'),
                ('Romans 8:15 — no more fear. That\'s this track.', 'verse_ref', 'reach'),
                ('What changes when you know you\'re adopted, not abandoned?', 'question', 'reach'),
                ('The Spirit testifies: you are a child of God. Not a slave. Not a stranger.', 'contrast', 'reach'),
                ('No spirit of fear. Spirit of adoption. Romans 8:15.', 'bold', 'reach'),
                ('God doesn\'t want your performance. He wants your presence. Abba.', 'emotion', 'reach'),
                ('For everyone who has ever felt like they didn\'t belong.', 'emotion', 'reach'),
                ('Romans 8:15 in four minutes of techno. You are home.', 'holy_rave', 'reach'),
                ('Holy Rave — where orphans become sons. Follow Robert-Jan Mastenbroek.', 'holy_rave', 'follow'),
                ('Tenerife Sunset Sessions. Free. Follow for the next one.', 'character', 'follow'),
                ('Abba — on Spotify now. Stream it. Share it.', 'bold', 'spotify'),
                ('Full track Abba on Spotify. Romans 8 at full volume.', 'verse_ref', 'spotify'),
            ],
        },

        {
            'pattern': 'MY HOPE IS IN YOU',
            'name': 'My Hope Is In You',
            'book': 'Psalms', 'chapter': 62, 'v_start': 5, 'v_end': 5,
            'ref': 'Psalm 62:5',
            'verse': 'Yes, my soul, find rest in God; my hope comes from him.',
            'theme': 'Hope is not optimism — it is rest in God. The soul commanded to stop striving.',
            'hooks': [
                ('My soul, find rest in God. My hope comes from him. Psalm 62:5.', 'verse_ref', 'reach'),
                ('Hope is not wishful thinking. It\'s rest. Psalm 62:5.', 'contrast', 'reach'),
                ('When everything else is gone, hope remains. This is that feeling.', 'emotion', 'reach'),
                ('Psalm 62 — the soul told to stop striving. This is the sound of that.', 'verse_ref', 'reach'),
                ('My hope is in you. Not in outcomes. Not in circumstances. In you.', 'bold', 'reach'),
                ('The bass is the rest Psalm 62:5 is talking about.', 'holy_rave', 'reach'),
                ('Rest in God sounds exactly like this when you close your eyes.', 'emotion', 'reach'),
                ('For everyone running on anxiety instead of hope.', 'emotion', 'reach'),
                ('Ancient rest for modern exhaustion. Psalm 62:5.', 'contrast', 'reach'),
                ('My hope is in you — not a feeling. A declaration.', 'bold', 'reach'),
                ('Follow for weekly Holy Rave, Tenerife. Rest for the restless.', 'emotion', 'follow'),
                ('Free weekly sessions, Tenerife. Follow Robert-Jan Mastenbroek.', 'character', 'follow'),
                ('My Hope Is In You — full track on Spotify now.', 'bold', 'spotify'),
                ('Stream My Hope Is In You. Psalm 62. Save it.', 'verse_ref', 'spotify'),
            ],
        },

        {
            'pattern': 'YOU SEE IT ALL',
            'name': 'You See It All',
            'book': 'Psalms', 'chapter': 139, 'v_start': 1, 'v_end': 4,
            'ref': 'Psalm 139:1-4',
            'verse': 'You have searched me, LORD, and you know me. You know when I sit and when I rise; you perceive my thoughts from afar. You discern my going out and my lying down; you are familiar with all my ways. Before a word is on my tongue you, LORD, know it completely.',
            'theme': 'Fully known and fully loved — the most radical comfort in scripture.',
            'hooks': [
                ('Before a word is on your tongue, he already knows it. Psalm 139.', 'verse_ref', 'reach'),
                ('You are completely known. And completely loved. That\'s Psalm 139.', 'bold', 'reach'),
                ('God sees it all. Every part. That\'s terrifying and the most comforting thing.', 'contrast', 'reach'),
                ('Psalm 139 — fully known, not exposed. Known and loved.', 'bold', 'reach'),
                ('What changes when you realise nothing is hidden but nothing is held against you?', 'question', 'reach'),
                ('He knows your going out and your lying down. He\'s still here.', 'verse_ref', 'reach'),
                ('This music is for the parts of you nobody else has seen.', 'emotion', 'reach'),
                ('You see it all — and you\'re still here. That\'s the miracle.', 'emotion', 'reach'),
                ('Psalm 139 on a dancefloor. The most honest place to be.', 'holy_rave', 'reach'),
                ('Fully searched. Fully known. Fully loved. This is that.', 'bold', 'reach'),
                ('Holy Rave — the place to be fully yourself. Follow Robert-Jan.', 'holy_rave', 'follow'),
                ('Tenerife. Every week. Free. Follow for the details.', 'character', 'follow'),
                ('You See It All — on Spotify. Full track. Stream it.', 'bold', 'spotify'),
                ('Psalm 139 at full volume on Spotify. Save this.', 'verse_ref', 'spotify'),
            ],
        },

        {
            'pattern': 'WHITE AS SNOW',
            'name': 'White As Snow',
            'book': 'Isaiah', 'chapter': 1, 'v_start': 18, 'v_end': 18,
            'ref': 'Isaiah 1:18',
            'verse': '"Come now, let us settle the matter," says the LORD. "Though your sins are like scarlet, they shall be as white as snow; though they are red as crimson, they shall be like wool."',
            'theme': 'Total cleansing — the most stained thing becomes the most clean. Not covered. Transformed.',
            'hooks': [
                ('"Though your sins are like scarlet, they shall be as white as snow." Isaiah 1:18.', 'verse_ref', 'reach'),
                ('Red as crimson. White as snow. That\'s the only deal God offers. Isaiah 1:18.', 'contrast', 'reach'),
                ('Not covered. Not managed. White as snow. That\'s the offer.', 'bold', 'reach'),
                ('Isaiah 1:18 is the cleanest verse in scripture. This is the track.', 'verse_ref', 'reach'),
                ('Come, let us settle the matter. God said that. Isaiah 1:18.', 'bold', 'reach'),
                ('2,700 years ago the invitation was the same: come. Let\'s deal with this.', 'contrast', 'reach'),
                ('The most stained thing becomes the most clean. That\'s not religion. That\'s Isaiah 1:18.', 'reveal', 'reach'),
                ('For everyone who thinks they\'ve gone too far.', 'emotion', 'reach'),
                ('Scarlet to snow. Isaiah 1:18 at 128 BPM.', 'verse_ref', 'reach'),
                ('What does forgiveness sound like? This.', 'question', 'reach'),
                ('Holy Rave — the place where the past doesn\'t stick. Follow for more.', 'holy_rave', 'follow'),
                ('Free weekly Sunset Sessions, Tenerife. Follow Robert-Jan Mastenbroek.', 'character', 'follow'),
                ('White As Snow — on Spotify. Isaiah 1:18. Stream it.', 'verse_ref', 'spotify'),
                ('Full track on Spotify. White As Snow. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'WAIT ON THE LORD',
            'name': 'Wait On The Lord',
            'book': 'Isaiah', 'chapter': 40, 'v_start': 31, 'v_end': 31,
            'ref': 'Isaiah 40:31',
            'verse': 'But those who hope in the LORD will renew their strength. They will soar on wings like eagles; they will run and not grow weary, they will walk and not be faint.',
            'theme': 'Waiting is not passive — it is the active posture that renews. The eagle doesn\'t flap. It soars.',
            'hooks': [
                ('They will soar on wings like eagles. Isaiah 40:31.', 'verse_ref', 'reach'),
                ('Run and not grow weary. Walk and not be faint. Isaiah 40:31.', 'verse_ref', 'reach'),
                ('Waiting on God renews your strength. That\'s not passive. That\'s powerful.', 'contrast', 'reach'),
                ('The eagle doesn\'t flap. It waits for the wind. Isaiah 40:31.', 'contrast', 'reach'),
                ('2,700 years old. Still the best instruction for exhausted people.', 'contrast', 'reach'),
                ('Those who hope in the LORD will renew their strength. This is what that feels like.', 'emotion', 'reach'),
                ('Isaiah 40:31 — the antidote to hustle culture.', 'question', 'reach'),
                ('What if waiting is the most powerful thing you can do right now?', 'question', 'reach'),
                ('Soaring. Not flapping. Isaiah 40:31 at 132 BPM.', 'verse_ref', 'reach'),
                ('For the ones who are running on empty. Isaiah 40:31 is yours.', 'emotion', 'reach'),
                ('Follow for Holy Rave — where the weary find their wings. Tenerife.', 'emotion', 'follow'),
                ('Robert-Jan Mastenbroek — weekly free sessions, Tenerife. Follow.', 'character', 'follow'),
                ('Wait On The Lord — full track on Spotify. Isaiah 40:31.', 'verse_ref', 'spotify'),
                ('Stream Wait On The Lord on Spotify. Save it for the hard days.', 'emotion', 'spotify'),
            ],
        },

        {
            'pattern': 'UNDER YOUR WINGS',
            'name': 'Under Your Wings',
            'book': 'Psalms', 'chapter': 91, 'v_start': 4, 'v_end': 4,
            'ref': 'Psalm 91:4',
            'verse': 'He will cover you with his feathers, and under his wings you will find refuge; his faithfulness will be your shield and rampart.',
            'theme': 'God as refuge — not distant but covering. Protection that is intimate.',
            'hooks': [
                ('Under his wings you will find refuge. Psalm 91:4.', 'verse_ref', 'reach'),
                ('His faithfulness is your shield. Psalm 91:4. This is that.', 'verse_ref', 'reach'),
                ('Not just protected. Covered. Under his wings. That\'s the difference.', 'contrast', 'reach'),
                ('Psalm 91 is the protection Psalm. This is the soundtrack.', 'bold', 'reach'),
                ('Safe doesn\'t mean distant. Psalm 91:4 — covered, not hidden.', 'contrast', 'reach'),
                ('The most intimate image of God in the Psalms. Feathers. Wings. Cover.', 'bold', 'reach'),
                ('What does sanctuary feel like at 128 BPM? This.', 'question', 'reach'),
                ('For everyone who needs to know they\'re covered.', 'emotion', 'reach'),
                ('Psalm 91:4 — ancient refuge in a modern room.', 'contrast', 'reach'),
                ('Under Your Wings — where the scattered find shelter.', 'emotion', 'reach'),
                ('Holy Rave is a sanctuary. Follow for weekly sessions, Tenerife.', 'holy_rave', 'follow'),
                ('Free. Every week. Tenerife. Follow Robert-Jan Mastenbroek.', 'character', 'follow'),
                ('Under Your Wings — on Spotify. Psalm 91. Stream it.', 'verse_ref', 'spotify'),
                ('Full track on Spotify. Under Your Wings. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'THUNDER',
            'name': 'Thunder',
            'book': 'Psalms', 'chapter': 29, 'v_start': 3, 'v_end': 9,
            'ref': 'Psalm 29',
            'verse': 'The voice of the LORD is over the waters; the God of glory thunders. The voice of the LORD is powerful; the voice of the LORD is majestic. The voice of the LORD breaks the cedars.',
            'theme': 'The voice of God as unstoppable force — thunder over the ocean, breaking cedars, shaking wilderness.',
            'hooks': [
                ('The voice of the LORD is over the waters. The God of glory thunders. Psalm 29.', 'verse_ref', 'reach'),
                ('The voice of the LORD breaks cedars. Psalm 29. Feel it.', 'verse_ref', 'reach'),
                ('Psalm 29 is the thunder Psalm. This is the track.', 'bold', 'reach'),
                ('Powerful. Majestic. Breaks cedars. That\'s not EDM. That\'s Psalm 29.', 'contrast', 'reach'),
                ('God of glory thunders. 3,000 years later it still sounds like this.', 'contrast', 'reach'),
                ('What does the voice of God sound like? Psalm 29 answers.', 'question', 'reach'),
                ('Thunder over the Atlantic. Psalm 29. Tenerife.', 'character', 'reach'),
                ('This is not metaphor. The voice of the LORD thunders. Psalm 29.', 'bold', 'reach'),
                ('The bass is the thunder David was writing about. Same frequency.', 'holy_rave', 'reach'),
                ('For everyone who needed to hear something louder than their fears.', 'emotion', 'reach'),
                ('Holy Rave — thunder sessions, Tenerife. Follow for more.', 'holy_rave', 'follow'),
                ('Free weekly Sunset Sessions. Tenerife. Follow Robert-Jan.', 'character', 'follow'),
                ('Thunder — full track on Spotify. Psalm 29 at volume.', 'verse_ref', 'spotify'),
                ('Stream Thunder on Spotify. Save it. Share it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'STRONG TOWER',
            'name': 'Strong Tower',
            'book': 'Proverbs', 'chapter': 18, 'v_start': 10, 'v_end': 10,
            'ref': 'Proverbs 18:10',
            'verse': 'The name of the LORD is a fortified tower; the righteous run to it and are safe.',
            'theme': 'God\'s name as fortress — not a building. A name. The righteous run, not walk.',
            'hooks': [
                ('The name of the LORD is a fortified tower. Proverbs 18:10.', 'verse_ref', 'reach'),
                ('The righteous run to it and are safe. They run. Proverbs 18:10.', 'verse_ref', 'reach'),
                ('Not a metaphor. A fortress. His name. Proverbs 18:10.', 'bold', 'reach'),
                ('What does it feel like to run to a name and be safe?', 'question', 'reach'),
                ('3,000 years ago — the same tower. Still standing. Proverbs 18:10.', 'contrast', 'reach'),
                ('The righteous don\'t walk. They run. Proverbs 18:10.', 'bold', 'reach'),
                ('A fortified tower in a name. Nothing in architecture compares.', 'contrast', 'reach'),
                ('For everyone who needed something that doesn\'t fall.', 'emotion', 'reach'),
                ('Strong Tower — where the running finds rest.', 'emotion', 'reach'),
                ('Proverbs 18:10 at 134 BPM. The fortress holds.', 'verse_ref', 'reach'),
                ('Holy Rave — the fortress on a dancefloor. Follow for more.', 'holy_rave', 'follow'),
                ('Free. Weekly. Tenerife. Follow Robert-Jan Mastenbroek.', 'character', 'follow'),
                ('Strong Tower — on Spotify. Proverbs 18:10. Full track.', 'verse_ref', 'spotify'),
                ('Stream Strong Tower on Spotify. Run to it.', 'emotion', 'spotify'),
            ],
        },

        {
            'pattern': 'RISE UP MY LOVE',
            'name': 'Rise Up My Love',
            'book': 'Song of Solomon', 'chapter': 2, 'v_start': 10, 'v_end': 13,
            'ref': 'Song of Solomon 2:10',
            'verse': 'My beloved spoke and said to me, "Arise, my darling, my beautiful one, come with me. See! The winter is past; the rains are gone. The flowers appear on the earth; the season of singing has come."',
            'theme': 'Invitation out of winter — the season of singing has come. Rise up.',
            'hooks': [
                ('"Arise, my darling, my beautiful one, come with me." Song of Solomon 2:10.', 'verse_ref', 'reach'),
                ('The winter is past. The rains are gone. The season of singing has come.', 'verse_ref', 'reach'),
                ('Song of Solomon is the most sensory book in the Bible. This is the track.', 'bold', 'reach'),
                ('Arise. The winter is over. Song of Solomon 2:10.', 'bold', 'reach'),
                ('What does the end of a long winter feel like at 128 BPM? This.', 'question', 'reach'),
                ('God said arise. He called you beautiful. He said come with me.', 'emotion', 'reach'),
                ('The season of singing has come. Are you ready?', 'question', 'reach'),
                ('3,000 years ago the most beautiful invitation was given. It\'s still open.', 'contrast', 'reach'),
                ('For everyone who has been in a long winter.', 'emotion', 'reach'),
                ('Song of Solomon 2:10 on a Tenerife dancefloor. Sunrise.', 'character', 'reach'),
                ('Follow for Holy Rave — Tenerife Sunset Sessions. The season of singing.', 'holy_rave', 'follow'),
                ('Robert-Jan Mastenbroek. Weekly free sessions. Tenerife. Follow.', 'character', 'follow'),
                ('Rise Up My Love — on Spotify. Song of Solomon 2:10.', 'verse_ref', 'spotify'),
                ('Full track on Spotify. Rise Up My Love. The winter is over.', 'emotion', 'spotify'),
            ],
        },

        {
            'pattern': 'QUIETED SOUL',
            'name': 'Quieted Soul',
            'book': 'Psalms', 'chapter': 131, 'v_start': 2, 'v_end': 2,
            'ref': 'Psalm 131:2',
            'verse': 'But I have calmed and quieted myself, I am like a weaned child with its mother; like a weaned child I am content.',
            'theme': 'The quieted soul — not suppressed, not distracted. Genuinely still. Trusting without demanding.',
            'hooks': [
                ('Psalm 131:2 — the quieted soul. Not suppressed. Genuinely still.', 'verse_ref', 'reach'),
                ('I have calmed and quieted my soul. Psalm 131. That\'s the work.', 'verse_ref', 'reach'),
                ('Like a weaned child with its mother. Content without demanding. Psalm 131:2.', 'verse_ref', 'reach'),
                ('The quieted soul is not passive. It\'s the hardest thing you\'ll do.', 'contrast', 'reach'),
                ('Stillness as an act of trust. Psalm 131:2.', 'bold', 'reach'),
                ('What does a genuinely quieted soul feel like? This.', 'question', 'reach'),
                ('The shortest Psalm. The deepest peace. Psalm 131.', 'contrast', 'reach'),
                ('Content. Still. Like a child with its mother. Psalm 131:2.', 'bold', 'reach'),
                ('For everyone who is learning to stop striving.', 'emotion', 'reach'),
                ('Quieted Soul — the counter-culture track. In a world of noise.', 'contrast', 'reach'),
                ('Holy Rave — where the loud leads to quiet. Follow for more.', 'holy_rave', 'follow'),
                ('Free weekly sessions, Tenerife. Follow Robert-Jan Mastenbroek.', 'character', 'follow'),
                ('Quieted Soul — on Spotify. Psalm 131. Full track.', 'verse_ref', 'spotify'),
                ('Stream Quieted Soul on Spotify. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'POWER FROM ABOVE',
            'name': 'Power From Above',
            'book': 'Acts', 'chapter': 1, 'v_start': 8, 'v_end': 8,
            'ref': 'Acts 1:8',
            'verse': 'But you will receive power when the Holy Spirit comes on you; and you will be my witnesses in Jerusalem, and in all Judea and Samaria, and to the ends of the earth.',
            'theme': 'Spirit power — not charisma, strategy, or talent. Power from above. To the ends of the earth.',
            'hooks': [
                ('You will receive power when the Holy Spirit comes on you. Acts 1:8.', 'verse_ref', 'reach'),
                ('To the ends of the earth. Acts 1:8. Tenerife is a good start.', 'character', 'reach'),
                ('Not human power. Power from above. Acts 1:8.', 'bold', 'reach'),
                ('Acts 1:8 — the original mission brief. Still active.', 'contrast', 'reach'),
                ('2,000 years ago: you will receive power. 2026: it\'s still available.', 'contrast', 'reach'),
                ('The ends of the earth starts here. Acts 1:8.', 'bold', 'reach'),
                ('What does Spirit power sound like at 130 BPM? This.', 'question', 'reach'),
                ('Not talent. Not strategy. Power from above. Acts 1:8.', 'bold', 'reach'),
                ('For everyone who has run out of their own power.', 'emotion', 'reach'),
                ('Power From Above — the Holy Rave track for the mission.', 'holy_rave', 'reach'),
                ('Holy Rave to the ends of the earth — starting Tenerife. Follow.', 'holy_rave', 'follow'),
                ('Follow Robert-Jan Mastenbroek. Weekly Sunset Sessions. Free.', 'character', 'follow'),
                ('Power From Above — on Spotify. Acts 1:8. Full track.', 'verse_ref', 'spotify'),
                ('Stream Power From Above on Spotify. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'LIVING WATER',
            'name': 'Living Water',
            'book': 'John', 'chapter': 4, 'v_start': 14, 'v_end': 14,
            'ref': 'John 4:14',
            'verse': '"Whoever drinks the water I give them will never thirst. Indeed, the water I give them will become in them a spring of water welling up to eternal life."',
            'theme': 'The water that ends thirst permanently — not a refill but a source that becomes internal.',
            'hooks': [
                ('"Whoever drinks the water I give them will never thirst." John 4:14.', 'verse_ref', 'reach'),
                ('A spring welling up to eternal life. Inside you. John 4:14.', 'verse_ref', 'reach'),
                ('Not a refill. A source. That\'s the difference. John 4:14.', 'contrast', 'reach'),
                ('Jesus said: this water becomes a spring in you. John 4:14.', 'bold', 'reach'),
                ('2,000 years ago a woman at a well heard this. It still applies.', 'contrast', 'reach'),
                ('What does never thirsting again feel like? This.', 'question', 'reach'),
                ('Living Water — the track the Samaritan woman deserved.', 'character', 'reach'),
                ('For everyone who keeps drinking and is still thirsty.', 'emotion', 'reach'),
                ('John 4:14 at 128 BPM. The water that doesn\'t run out.', 'verse_ref', 'reach'),
                ('Living water on a Tenerife dancefloor. John 4:14.', 'holy_rave', 'reach'),
                ('Follow for Holy Rave — where the thirsty find what they\'re looking for.', 'emotion', 'follow'),
                ('Free. Weekly. Tenerife. Robert-Jan Mastenbroek. Follow.', 'character', 'follow'),
                ('Living Water — on Spotify. John 4:14. Stream it.', 'verse_ref', 'spotify'),
                ('Full track on Spotify. Living Water. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'KAVOD',
            'name': 'Kavod',
            'book': 'Isaiah', 'chapter': 6, 'v_start': 3, 'v_end': 3,
            'ref': 'Isaiah 6:3',
            'verse': 'And they were calling to one another: "Holy, holy, holy is the LORD Almighty; the whole earth is full of his glory [kavod]."',
            'theme': 'Kavod — Hebrew for glory, weight, honour. The whole earth is saturated with it.',
            'hooks': [
                ('Kavod — the Hebrew word for glory. The whole earth is full of it. Isaiah 6:3.', 'verse_ref', 'reach'),
                ('Holy, holy, holy. The seraphim never stop. Isaiah 6:3.', 'verse_ref', 'reach'),
                ('The whole earth is full of his kavod. You\'re standing in it.', 'bold', 'reach'),
                ('Glory is not rare. It\'s everywhere. The Hebrew word is kavod.', 'reveal', 'reach'),
                ('Isaiah saw seraphim calling to each other. This is what they were saying.', 'character', 'reach'),
                ('What does a room full of kavod feel like? This exactly.', 'question', 'reach'),
                ('2,700 years ago Isaiah heard it. Tonight we hear it at 130 BPM.', 'contrast', 'reach'),
                ('Kavod — weight, honour, glory. Not abstract. Physical. This.', 'bold', 'reach'),
                ('The seraphim never stop singing. We\'re joining in tonight.', 'holy_rave', 'reach'),
                ('For everyone who has ever felt the weight of something holy in a room.', 'emotion', 'reach'),
                ('Holy Rave — where kavod fills the room. Follow for more.', 'holy_rave', 'follow'),
                ('Free weekly sessions, Tenerife. Follow Robert-Jan Mastenbroek.', 'character', 'follow'),
                ('Kavod — on Spotify. Isaiah 6:3. Full track.', 'verse_ref', 'spotify'),
                ('Stream Kavod on Spotify. Save it. The whole earth is full of it.', 'emotion', 'spotify'),
            ],
        },

        {
            'pattern': 'HOW GOOD AND PLEASANT',
            'name': 'How Good And Pleasant',
            'book': 'Psalms', 'chapter': 133, 'v_start': 1, 'v_end': 1,
            'ref': 'Psalm 133:1',
            'verse': 'How good and pleasant it is when God\'s people live together in unity!',
            'theme': 'Unity as blessing — when the scattered gather, something anointing flows.',
            'hooks': [
                ('"How good and pleasant it is when God\'s people live together in unity!" Psalm 133:1.', 'verse_ref', 'reach'),
                ('Psalm 133 is the shortest unity manifesto ever written. This is the track.', 'bold', 'reach'),
                ('What does unity feel like at 3am when everyone is still on the dancefloor?', 'question', 'reach'),
                ('How good and pleasant — David wrote this 3,000 years ago. Still true.', 'contrast', 'reach'),
                ('Psalm 133:1 — the anointing that flows when people gather as one.', 'verse_ref', 'reach'),
                ('Unity is not agreement. It\'s the thing that happens when people align. This is it.', 'contrast', 'reach'),
                ('Good. And pleasant. Both. At the same time. Psalm 133.', 'bold', 'reach'),
                ('Holy Rave — where strangers become one. Psalm 133:1.', 'holy_rave', 'reach'),
                ('For everyone who has felt something happen in a room full of people.', 'emotion', 'reach'),
                ('How Good And Pleasant — the sound of people together.', 'emotion', 'reach'),
                ('Follow for Holy Rave — the gathering. Tenerife weekly.', 'holy_rave', 'follow'),
                ('Free. Weekly. Every sunset. Robert-Jan Mastenbroek. Follow.', 'character', 'follow'),
                ('How Good And Pleasant — on Spotify. Psalm 133. Stream it.', 'verse_ref', 'spotify'),
                ('Full track on Spotify. How Good And Pleasant. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'HE REIGNS',
            'name': 'He Reigns',
            'book': 'Revelation', 'chapter': 19, 'v_start': 6, 'v_end': 6,
            'ref': 'Revelation 19:6',
            'verse': 'Then I heard what sounded like a great multitude, like the roar of rushing waters and like loud peals of thunder, shouting: "Hallelujah! For our Lord God Almighty reigns."',
            'theme': 'The multitude that shouts — like thunder, like rushing water. The Almighty reigns.',
            'hooks': [
                ('"Like the roar of rushing waters." "Like loud peals of thunder." Revelation 19:6.', 'verse_ref', 'reach'),
                ('A great multitude. Roar of rushing waters. Loud peals of thunder. That\'s the rave.', 'holy_rave', 'reach'),
                ('The Lord God Almighty reigns. Revelation 19:6. Still true.', 'bold', 'reach'),
                ('John heard a sound like rushing waters and thunder. This is it.', 'reveal', 'reach'),
                ('Hallelujah — for our Lord God Almighty reigns. Revelation 19:6.', 'verse_ref', 'reach'),
                ('2,000 years ago John described a rave. Revelation 19:6.', 'contrast', 'reach'),
                ('The throne room sounds like this. Revelation 19:6.', 'bold', 'reach'),
                ('Every beat is a declaration: He reigns. Revelation 19:6.', 'holy_rave', 'reach'),
                ('For the ones who needed to hear: He is still on the throne.', 'emotion', 'reach'),
                ('Like thunder. Like rushing water. He reigns.', 'bold', 'reach'),
                ('Holy Rave — the sound of Revelation 19:6. Follow for more.', 'holy_rave', 'follow'),
                ('Free weekly sessions. Tenerife. Robert-Jan Mastenbroek. Follow.', 'character', 'follow'),
                ('He Reigns — on Spotify. Revelation 19:6. Full track.', 'verse_ref', 'spotify'),
                ('Stream He Reigns. Save it. He reigns.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'HALALA KING JESUS',
            'name': 'Halala King Jesus',
            'book': 'Revelation', 'chapter': 19, 'v_start': 16, 'v_end': 16,
            'ref': 'Revelation 19:16',
            'verse': 'On his robe and on his thigh he has this name written: KING OF KINGS AND LORD OF LORDS.',
            'theme': 'The name above all names — written on his robe and his thigh. Undeniable authority.',
            'hooks': [
                ('King of Kings. Lord of Lords. Written on his robe. Revelation 19:16.', 'verse_ref', 'reach'),
                ('Halala — Zulu for praise. King Jesus. Every language. Every dancefloor.', 'reveal', 'reach'),
                ('KING OF KINGS AND LORD OF LORDS. Not a title. A name. Revelation 19:16.', 'bold', 'reach'),
                ('Written on his thigh. Revelation 19:16. No argument possible.', 'verse_ref', 'reach'),
                ('Halala King Jesus — where African praise meets global dancefloors.', 'character', 'reach'),
                ('2,000 years ago John saw this name. Tonight we say it.', 'contrast', 'reach'),
                ('What does it sound like when every tongue confesses? This.', 'question', 'reach'),
                ('Revelation 19 is the ultimate Holy Rave text. This is proof.', 'holy_rave', 'reach'),
                ('Every nation. Every language. King Jesus. This is the track.', 'bold', 'reach'),
                ('The name on his robe — King of Kings. We\'re just repeating it.', 'reveal', 'reach'),
                ('Follow for Holy Rave — where every nation praises. Tenerife.', 'holy_rave', 'follow'),
                ('Free weekly sessions. Robert-Jan Mastenbroek. Tenerife. Follow.', 'character', 'follow'),
                ('Halala King Jesus — on Spotify. Revelation 19. Full track.', 'verse_ref', 'spotify'),
                ('Stream Halala King Jesus. Save it. Share it globally.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'GOOD TO ME',
            'name': 'Good To Me',
            'book': 'Psalms', 'chapter': 73, 'v_start': 28, 'v_end': 28,
            'ref': 'Psalm 73:28',
            'verse': 'But as for me, it is good to be near God. I have made the Sovereign LORD my refuge; I will tell of all your deeds.',
            'theme': 'After questioning everything — the conclusion: nearness to God is good. Simple and final.',
            'hooks': [
                ('"It is good to be near God." Psalm 73:28. End of argument.', 'verse_ref', 'reach'),
                ('After all the questions, the conclusion: it is good to be near God.', 'contrast', 'reach'),
                ('Psalm 73 — the Psalm that almost lost faith and didn\'t. This is the turn.', 'reveal', 'reach'),
                ('Good to Me. Not good to others. Good. To. Me. Personally.', 'bold', 'reach'),
                ('Asaph nearly gave up. Psalm 73. Then he went near God. Changed everything.', 'character', 'reach'),
                ('What does it feel like to go near God and find him good?', 'question', 'reach'),
                ('Psalm 73:28 — the most honest conclusion to the longest doubt.', 'bold', 'reach'),
                ('Near God. Not performing for God. Near God. Psalm 73:28.', 'contrast', 'reach'),
                ('For the ones who are still processing whether God is actually good.', 'emotion', 'reach'),
                ('Good to Me — the track for the ones coming back.', 'emotion', 'reach'),
                ('Holy Rave — come near. Tenerife. Weekly. Follow Robert-Jan.', 'holy_rave', 'follow'),
                ('Free. Every week. Sunset Sessions. Follow for more.', 'character', 'follow'),
                ('Good To Me — on Spotify. Psalm 73:28. Stream it.', 'verse_ref', 'spotify'),
                ('Full track on Spotify. Good To Me. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'GIVE THANKS',
            'name': 'Give Thanks',
            'book': 'Psalms', 'chapter': 107, 'v_start': 1, 'v_end': 1,
            'ref': 'Psalm 107:1',
            'verse': 'Give thanks to the LORD, for he is good; his love endures forever.',
            'theme': 'Gratitude as declaration — not feeling but instruction. He is good. His love endures.',
            'hooks': [
                ('Give thanks to the LORD, for he is good. His love endures forever. Psalm 107:1.', 'verse_ref', 'reach'),
                ('His love endures forever. Psalm 107:1. That\'s not a feeling. That\'s a fact.', 'contrast', 'reach'),
                ('3,000 years ago they sang this. Tonight we dance to it.', 'contrast', 'reach'),
                ('Give thanks — not because it\'s easy. Because he is good. Psalm 107.', 'bold', 'reach'),
                ('His love endures forever. The most repeated phrase in the Psalms. This is why.', 'reveal', 'reach'),
                ('Gratitude is not a mood. It\'s a declaration. Psalm 107:1.', 'contrast', 'reach'),
                ('What does thanks sound like at 128 BPM? This.', 'question', 'reach'),
                ('For everyone who needed a reason to be grateful today.', 'emotion', 'reach'),
                ('His love endures. Even now. Psalm 107:1.', 'bold', 'reach'),
                ('Give Thanks — the ancient posture turned into a dancefloor moment.', 'holy_rave', 'reach'),
                ('Follow for Holy Rave — where gratitude is loud. Tenerife.', 'holy_rave', 'follow'),
                ('Free weekly sessions. Robert-Jan Mastenbroek. Follow.', 'character', 'follow'),
                ('Give Thanks — on Spotify. Psalm 107:1. Full track.', 'verse_ref', 'spotify'),
                ('Stream Give Thanks on Spotify. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'FIRST FACE',
            'name': 'First Face',
            'book': 'Colossians', 'chapter': 1, 'v_start': 15, 'v_end': 15,
            'ref': 'Colossians 1:15',
            'verse': 'The Son is the image of the invisible God, the firstborn over all creation.',
            'theme': 'Jesus as the visible face of the invisible God — the first image, the image that holds creation together.',
            'hooks': [
                ('The Son is the image of the invisible God. Colossians 1:15.', 'verse_ref', 'reach'),
                ('The first face you see when you see God. Colossians 1:15.', 'bold', 'reach'),
                ('The invisible became visible. Colossians 1:15. That\'s the miracle.', 'contrast', 'reach'),
                ('Firstborn over all creation. Not first created. Firstborn over it. Colossians 1:15.', 'reveal', 'reach'),
                ('What does the face of the invisible God look like? Colossians 1:15 answers.', 'question', 'reach'),
                ('Image of the invisible. The paradox that changes everything. Colossians 1:15.', 'bold', 'reach'),
                ('2,000 years ago this was declared. First Face. Still true.', 'contrast', 'reach'),
                ('For everyone who wanted to see God\'s face. He showed you.', 'emotion', 'reach'),
                ('First Face — Colossians 1:15 at 130 BPM.', 'verse_ref', 'reach'),
                ('The image of the invisible. Holy Rave in Tenerife.', 'holy_rave', 'reach'),
                ('Holy Rave — where you see the first face. Follow Robert-Jan.', 'holy_rave', 'follow'),
                ('Free weekly sessions. Tenerife. Follow for the next one.', 'character', 'follow'),
                ('First Face — on Spotify. Colossians 1:15. Stream it.', 'verse_ref', 'spotify'),
                ('Full track on Spotify. First Face. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'EXODUS',
            'name': 'Exodus',
            'book': 'Exodus', 'chapter': 14, 'v_start': 13, 'v_end': 14,
            'ref': 'Exodus 14:13-14',
            'verse': 'Moses answered the people, "Do not be afraid. Stand firm and you will see the deliverance the LORD will bring you today. The Egyptians you see today you will never see again. The LORD will fight for you; you need only to be still."',
            'theme': 'The LORD fights. You stand still. The sea opens. The impossible becomes the road.',
            'hooks': [
                ('"The LORD will fight for you; you need only to be still." Exodus 14:14.', 'verse_ref', 'reach'),
                ('Do not be afraid. Stand firm. Exodus 14:13.', 'bold', 'reach'),
                ('The sea opened because they stood still. Exodus 14:14.', 'reveal', 'reach'),
                ('3,400 years ago: stand still and watch what God does. Still the instruction.', 'contrast', 'reach'),
                ('The Egyptians you see today you will never see again. Exodus 14:13.', 'verse_ref', 'reach'),
                ('What does it feel like to stand still and watch the impossible open up?', 'question', 'reach'),
                ('Exodus 14:14 — when the battle belongs to the LORD, be still.', 'bold', 'reach'),
                ('Moses told them: the LORD will fight. He was right. Exodus 14.', 'character', 'reach'),
                ('The sea road is still open. Exodus 14. This is the soundtrack.', 'holy_rave', 'reach'),
                ('For everyone who is standing at their own Red Sea.', 'emotion', 'reach'),
                ('Follow for Holy Rave — Exodus sessions, Tenerife. Be still.', 'holy_rave', 'follow'),
                ('Free. Weekly. Sunset Sessions. Follow Robert-Jan Mastenbroek.', 'character', 'follow'),
                ('Exodus — on Spotify. Exodus 14:14. Full track.', 'verse_ref', 'spotify'),
                ('Stream Exodus on Spotify. Stand firm. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'CHAOS BENDS',
            'name': 'Chaos Bends',
            'book': 'Psalms', 'chapter': 46, 'v_start': 10, 'v_end': 10,
            'ref': 'Psalm 46:10',
            'verse': '"Be still, and know that I am God; I will be exalted among the nations, I will be exalted in the earth."',
            'theme': 'God speaks into chaos and it obeys. Stillness is not weakness — it is the response to the sovereign.',
            'hooks': [
                ('"Be still, and know that I am God." Psalm 46:10. Chaos bends.', 'verse_ref', 'reach'),
                ('Chaos bends when God speaks. Psalm 46:10.', 'bold', 'reach'),
                ('The earth moves, kingdoms fall, rivers roar. God says: be still. Psalm 46.', 'verse_ref', 'reach'),
                ('What does stillness in the middle of chaos sound like? This.', 'question', 'reach'),
                ('3,000 years ago David wrote Psalm 46 in a crisis. It still applies.', 'contrast', 'reach'),
                ('I will be exalted among the nations. God said that. Psalm 46:10.', 'bold', 'reach'),
                ('Chaos bends — because he is God and everything else isn\'t.', 'bold', 'reach'),
                ('Be still. Not passive. Not unaware. Still. Psalm 46:10.', 'contrast', 'reach'),
                ('For everyone in the middle of something that won\'t stop moving.', 'emotion', 'reach'),
                ('Psalm 46:10 at 136 BPM. Chaos bends. You stand still.', 'verse_ref', 'reach'),
                ('Holy Rave — the place chaos has no entrance. Follow for more.', 'holy_rave', 'follow'),
                ('Free weekly sessions. Tenerife. Follow Robert-Jan Mastenbroek.', 'character', 'follow'),
                ('Chaos Bends — on Spotify. Psalm 46:10. Full track.', 'verse_ref', 'spotify'),
                ('Stream Chaos Bends. Save it. Be still.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'BETTER IS ONE DAY',
            'name': 'Better Is One Day',
            'book': 'Psalms', 'chapter': 84, 'v_start': 10, 'v_end': 10,
            'ref': 'Psalm 84:10',
            'verse': 'Better is one day in your courts than a thousand elsewhere; I would rather be a doorkeeper in the house of my God than dwell in the tents of the wicked.',
            'theme': 'One day in the presence of God is worth more than a thousand elsewhere. The arithmetic of nearness.',
            'hooks': [
                ('"Better is one day in your courts than a thousand elsewhere." Psalm 84:10.', 'verse_ref', 'reach'),
                ('One day with God > a thousand days anywhere else. Psalm 84:10. The maths.', 'contrast', 'reach'),
                ('Psalm 84:10 is not metaphor. It\'s a calculation. One day wins.', 'bold', 'reach'),
                ('I would rather be a doorkeeper in God\'s house. Psalm 84:10.', 'verse_ref', 'reach'),
                ('What does one day in the courts of God feel like? This.', 'question', 'reach'),
                ('Better is one day. Not better eventually. Better. One day.', 'bold', 'reach'),
                ('3,000 years ago someone did the maths. Psalm 84:10. Still correct.', 'contrast', 'reach'),
                ('For everyone who came to the Sunset Sessions and understood Psalm 84:10.', 'holy_rave', 'reach'),
                ('The doorkeeper position is better than the VIP tent. Psalm 84:10.', 'reveal', 'reach'),
                ('Better Is One Day — the track that rewrites your calendar.', 'emotion', 'reach'),
                ('Holy Rave — better is one day. Follow for the weekly session.', 'holy_rave', 'follow'),
                ('Free. Every week. Tenerife at sunset. Follow Robert-Jan.', 'character', 'follow'),
                ('Better Is One Day — on Spotify. Psalm 84:10. Full track.', 'verse_ref', 'spotify'),
                ('Stream Better Is One Day. Save it. One day.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'AT THE DOOR',
            'name': 'At The Door',
            'book': 'Revelation', 'chapter': 3, 'v_start': 20, 'v_end': 20,
            'ref': 'Revelation 3:20',
            'verse': '"Here I am! I stand at the door and knock. If anyone hears my voice and opens the door, I will come in and eat with them, and they with me."',
            'theme': 'God doesn\'t force entry — he stands at the door and knocks. The invitation requires a response.',
            'hooks': [
                ('"I stand at the door and knock." Revelation 3:20. He\'s still there.', 'verse_ref', 'reach'),
                ('God knocks. He doesn\'t break in. Revelation 3:20.', 'bold', 'reach'),
                ('If anyone hears and opens — anyone. Revelation 3:20.', 'verse_ref', 'reach'),
                ('He will come in and eat with them. Not inspect them. Eat with them.', 'contrast', 'reach'),
                ('2,000 years ago this was the offer. Tonight it\'s still open.', 'contrast', 'reach'),
                ('What does it sound like when you open the door? This.', 'question', 'reach'),
                ('Here I am. At the door. Knocking. Revelation 3:20.', 'bold', 'reach'),
                ('Not a metaphor. The most personal invitation ever given. Rev 3:20.', 'bold', 'reach'),
                ('For everyone who has heard knocking and kept waiting.', 'emotion', 'reach'),
                ('At The Door — the track for the moment you open it.', 'emotion', 'reach'),
                ('Holy Rave — the door is always open. Follow Robert-Jan. Tenerife.', 'holy_rave', 'follow'),
                ('Free. Weekly. Sunset. Follow for the next one.', 'character', 'follow'),
                ('At The Door — on Spotify. Revelation 3:20. Full track.', 'verse_ref', 'spotify'),
                ('Stream At The Door. Open it. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'HE IS THE LIGHT',
            'name': 'He Is The Light',
            'book': 'John', 'chapter': 8, 'v_start': 12, 'v_end': 12,
            'ref': 'John 8:12',
            'verse': 'When Jesus spoke again to the people, he said, "I am the light of the world. Whoever follows me will never walk in darkness, but will have the light of life."',
            'theme': 'Not a light — the light. Whoever follows will never walk in darkness. A promise and an identity.',
            'hooks': [
                ('"I am the light of the world." John 8:12. Not a light. THE light.', 'verse_ref', 'reach'),
                ('Whoever follows me will never walk in darkness. John 8:12.', 'verse_ref', 'reach'),
                ('The light of life. Not light at the end of a tunnel. Life itself as light.', 'contrast', 'reach'),
                ('2,000 years ago Jesus said this. Still true. John 8:12.', 'contrast', 'reach'),
                ('What does never walking in darkness feel like? This.', 'question', 'reach'),
                ('He is the light. Everything else is a derivative. John 8:12.', 'bold', 'reach'),
                ('John 8:12 at 128 BPM. Never walk in darkness.', 'verse_ref', 'reach'),
                ('He Is The Light — for everyone who has lost their way in the dark.', 'emotion', 'reach'),
                ('The light of the world sounds exactly like this.', 'holy_rave', 'reach'),
                ('Light at the rave. Light in the Psalms. Same source. John 8:12.', 'contrast', 'reach'),
                ('Holy Rave — walk in the light. Tenerife. Follow Robert-Jan.', 'holy_rave', 'follow'),
                ('Free weekly sessions. Follow for the next Sunset Session.', 'character', 'follow'),
                ('He Is The Light — on Spotify. John 8:12. Full track.', 'verse_ref', 'spotify'),
                ('Stream He Is The Light on Spotify. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'LORD IN THE FULLNESS',
            'name': 'Lord In The Fullness',
            'book': 'Colossians', 'chapter': 1, 'v_start': 19, 'v_end': 19,
            'ref': 'Colossians 1:19',
            'verse': 'For God was pleased to have all his fullness dwell in him.',
            'theme': 'All fullness — not a portion, not an anointing, but the complete fullness of God inhabiting Christ.',
            'hooks': [
                ('All his fullness dwells in him. Colossians 1:19. All of it.', 'verse_ref', 'reach'),
                ('Not a portion. Not an anointing. All fullness. Colossians 1:19.', 'bold', 'reach'),
                ('God was pleased to fill Christ with everything. Colossians 1:19.', 'verse_ref', 'reach'),
                ('What does fullness sound like when it fills a room? This.', 'question', 'reach'),
                ('2,000 years ago Paul wrote Colossians 1:19. The fullness has not diminished.', 'contrast', 'reach'),
                ('Lord in the fullness — not halfway. Not partially. Completely.', 'bold', 'reach'),
                ('The fullness of God in one person. Colossians 1:19. That\'s the miracle.', 'reveal', 'reach'),
                ('For everyone who has been given only pieces and needs the whole.', 'emotion', 'reach'),
                ('Fullness. The word is pleroma in Greek. It means everything.', 'reveal', 'reach'),
                ('Colossians 1:19 at 130 BPM. Lord in the fullness.', 'verse_ref', 'reach'),
                ('Holy Rave — full, not empty. Follow Robert-Jan Mastenbroek.', 'holy_rave', 'follow'),
                ('Free weekly sessions. Tenerife at sunset. Follow.', 'character', 'follow'),
                ('Lord In The Fullness — on Spotify. Colossians 1:19. Stream it.', 'verse_ref', 'spotify'),
                ('Full track on Spotify. Lord In The Fullness. Save it.', 'bold', 'spotify'),
            ],
        },

        {
            'pattern': 'WAIT ON THE LORD',   # already added above — skip duplicate
            'name': '__SKIP__',
            'book': 'skip', 'chapter': 0, 'v_start': 0, 'v_end': 0,
            'ref': '', 'verse': '', 'theme': '', 'hooks': [],
        },
    ]

    for song in SONGS:
        if song['name'] == '__SKIP__':
            continue
        pattern = song['pattern']
        if _track_exists(pattern):
            continue

        add_track(
            filename_pattern=pattern,
            track_name=song['name'],
            bible_book=song['book'],
            bible_chapter=song['chapter'],
            bible_verse_start=song['v_start'],
            bible_verse_end=song['v_end'],
            verse_reference=song['ref'],
            verse_text=song['verse'],
            theme=song['theme'],
        )

        for hook_text, hook_pattern, bucket in song['hooks']:
            add_hook(pattern, hook_text, hook_pattern, bucket)

    # ── Patch original 4 tracks with follow + spotify bucket hooks ────────────
    # The original seed only had reach/depth. Daily run needs follow & spotify.
    _patch_missing_bucket_hooks()


def _patch_missing_bucket_hooks():
    """
    Add follow + spotify hooks to tracks that were seeded before those buckets existed.
    Uses SELECT-before-INSERT to stay idempotent.
    """
    conn = _get_conn()

    def _has_bucket(pattern, bucket):
        r = conn.execute(
            "SELECT 1 FROM hooks WHERE filename_pattern=? AND bucket=? LIMIT 1",
            (pattern, bucket)
        ).fetchone()
        return r is not None

    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()

    def _add(pattern, hook_text, hook_pattern, bucket):
        conn.execute(
            "INSERT INTO hooks (filename_pattern, hook_text, pattern, bucket, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pattern, hook_text, hook_pattern, bucket, now)
        )

    patches = {
        'JERICHO': {
            'follow': [
                ('Follow for Holy Rave — where walls come down. Tenerife weekly.', 'holy_rave'),
                ('Weekly Sunset Sessions, Tenerife. Free. Follow Robert-Jan Mastenbroek.', 'character'),
                ('I build Joshua 6:20 into techno every week. Follow for more.', 'reveal'),
            ],
            'spotify': [
                ('Jericho — full track on Spotify. Joshua 6:20 at full volume.', 'verse_ref'),
                ('Stream Jericho on Spotify. Save it. Share it.', 'bold'),
                ('Full version on Spotify — the walls fall harder.', 'reveal'),
            ],
        },
        'NOT_BY_MIGHT': {
            'follow': [
                ('Follow for Holy Rave — Zechariah 4:6 in techno, Tenerife weekly.', 'holy_rave'),
                ('Weekly free sessions, Tenerife. Follow Robert-Jan Mastenbroek.', 'character'),
                ('I build prophetic decrees into electronic music. Follow for more.', 'reveal'),
            ],
            'spotify': [
                ('Not By Might — full track on Spotify. Zechariah 4:6 at volume.', 'verse_ref'),
                ('Stream Not By Might on Spotify. Save it.', 'bold'),
                ('Full track on Spotify — let it go. Not by might.', 'emotion'),
            ],
        },
        'LET_MY_PEOPLE_GO': {
            'follow': [
                ('Holy Rave — freedom sessions, Tenerife. Follow Robert-Jan.', 'holy_rave'),
                ('Weekly free Sunset Sessions. Follow for the next one.', 'character'),
                ('Exodus 5:1 in techno every week. Follow for more.', 'reveal'),
            ],
            'spotify': [
                ('Let My People Go — on Spotify. Exodus 5:1 at full volume.', 'verse_ref'),
                ('Stream Let My People Go on Spotify. Save it.', 'bold'),
                ('Full track on Spotify. The decree still stands.', 'bold'),
            ],
        },
        'CREATE_CLEAN_HEART': {
            'follow': [
                ('Holy Rave — where the broken heart finds its song. Follow Robert-Jan.', 'emotion'),
                ('Weekly sessions, Tenerife. Free. Follow for the next one.', 'character'),
                ('Psalm 51 in techno every week. Follow for more.', 'reveal'),
            ],
            'spotify': [
                ('Create In Me A Clean Heart — on Spotify. Psalm 51 at volume.', 'verse_ref'),
                ('Stream Create In Me A Clean Heart on Spotify. Save it.', 'bold'),
                ('Full track on Spotify. Psalm 51. The prayer that still works.', 'emotion'),
            ],
        },
    }

    for pattern, buckets in patches.items():
        for bucket, hooks in buckets.items():
            if not _has_bucket(pattern, bucket):
                for hook_text, hook_pattern in hooks:
                    _add(pattern, hook_text, hook_pattern, bucket)

    conn.commit()
    conn.close()


def get_hooks_for_track(filename: str, bucket: str = None) -> list[dict]:
    """
    Find hooks for a track by matching filename pattern.
    Returns list of dicts: {hook_text, pattern, bucket, performance_score}
    Sorted by: performance_score DESC (nulls last), then pattern variety.
    """
    conn = _get_conn()
    cur = conn.cursor()

    # Determine which filename_pattern matches
    cur.execute("SELECT filename_pattern FROM tracks")
    patterns = [row['filename_pattern'] for row in cur.fetchall()]
    matched_pattern = None
    upper_filename = filename.upper()
    for p in patterns:
        if p.upper() in upper_filename:
            matched_pattern = p
            break

    if not matched_pattern:
        conn.close()
        return []

    if bucket:
        cur.execute("""
            SELECT hook_text, pattern, bucket, performance_score
            FROM hooks
            WHERE filename_pattern = ? AND bucket = ?
            ORDER BY
                CASE WHEN performance_score IS NULL THEN 1 ELSE 0 END,
                performance_score DESC,
                pattern
        """, (matched_pattern, bucket))
    else:
        cur.execute("""
            SELECT hook_text, pattern, bucket, performance_score
            FROM hooks
            WHERE filename_pattern = ?
            ORDER BY
                CASE WHEN performance_score IS NULL THEN 1 ELSE 0 END,
                performance_score DESC,
                pattern
        """, (matched_pattern,))

    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def add_track(filename_pattern, track_name, bible_book, bible_chapter,
              bible_verse_start, bible_verse_end, verse_reference,
              verse_text, theme, lyrics_excerpt='', bpm=0):
    """Insert a new track into the database."""
    conn = _get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute("""
        INSERT OR IGNORE INTO tracks
            (filename_pattern, track_name, bible_book, bible_chapter,
             bible_verse_start, bible_verse_end, verse_reference,
             verse_text, theme, lyrics_excerpt, bpm, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (filename_pattern, track_name, bible_book, bible_chapter,
          bible_verse_start, bible_verse_end, verse_reference,
          verse_text, theme, lyrics_excerpt, bpm, now))
    conn.commit()
    conn.close()


def add_hook(filename_pattern, hook_text, pattern, bucket='reach'):
    """Insert a new hook for a track."""
    conn = _get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    cur.execute("""
        INSERT OR IGNORE INTO hooks
            (filename_pattern, hook_text, pattern, bucket, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (filename_pattern, hook_text, pattern, bucket, now))
    conn.commit()
    conn.close()


def record_performance(hook_text: str, views: int, likes: int, shares: int):
    """
    Record performance metrics for a hook and update its performance score.
    performance_score = (likes*2 + shares*5) / max(views, 1) * 1000
    """
    conn = _get_conn()
    cur = conn.cursor()
    score = (likes * 2 + shares * 5) / max(views, 1) * 1000
    cur.execute("""
        UPDATE hooks
        SET views = views + ?,
            likes = likes + ?,
            shares = shares + ?,
            performance_score = ?
        WHERE hook_text = ?
    """, (views, likes, shares, score, hook_text))
    conn.commit()
    conn.close()


def get_best_hooks(limit: int = 10) -> list[dict]:
    """Return top performing hooks across all tracks."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT h.hook_text, h.pattern, h.bucket, h.performance_score,
               t.track_name, t.verse_reference
        FROM hooks h
        JOIN tracks t ON t.filename_pattern = h.filename_pattern
        WHERE h.performance_score IS NOT NULL
        ORDER BY h.performance_score DESC
        LIMIT ?
    """, (limit,))
    rows = cur.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_track_info(filename_pattern: str) -> dict:
    """Return full track record as dict, or empty dict if not found."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM tracks WHERE filename_pattern = ?", (filename_pattern,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else {}


if __name__ == '__main__':
    init_db()
    seed_initial_data()
    print(f"Database initialised at: {DB_PATH}")
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as n FROM tracks")
    print(f"Tracks: {cur.fetchone()['n']}")
    cur.execute("SELECT COUNT(*) as n FROM hooks")
    print(f"Hooks:  {cur.fetchone()['n']}")
    conn.close()
