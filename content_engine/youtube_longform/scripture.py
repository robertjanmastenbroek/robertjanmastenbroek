"""
scripture.py — Verse text for each SCRIPTURE_ANCHOR.

Used by publisher._compose_description to render a "Scripture:" block
in the video description when a track has an anchor but no explicit
lyrics. Serves two functions:

  1. SEO — long-tail search matches for biblical phrases ("joshua 6",
     "walls of jericho fell", "psalm 46 be still", etc.)
  2. Subtle Salt — a scriptural passage in the description is context,
     not a sermon. Viewers who know recognize it; others read it as
     poetic flavor tied to the track title.

All verses are from the World English Bible (WEB) — public domain, no
licensing friction. ESV/NIV/KJV are copyrighted and would risk DMCA
headaches if we ever scale to enough uploads to show up on rights-holder
radar.
"""
from __future__ import annotations


# ─── Anchor → verse text ─────────────────────────────────────────────────────
# Keep each block under ~200 words — the description is capped at 5000
# chars total and we need room for links + hashtag stack.

VERSES: dict[str, str] = {
    # Joshua 6 — walls of Jericho fall on the seventh day
    "Joshua 6": (
        "Yahweh said to Joshua, 'Behold, I have given Jericho into your hand, "
        "with its king and its mighty men of valor. All of your men of war "
        "shall march around the city, going around the city once. You shall "
        "do this six days. Seven priests shall bear seven shofars of rams' "
        "horns before the ark. On the seventh day, you shall march around "
        "the city seven times, and the priests shall blow the shofars. It "
        "shall be that when they make a long blast with the ram's horn, and "
        "when you hear the sound of the shofar, all the people shall shout "
        "with a great shout; and the wall of the city shall fall down flat.' "
        "— Joshua 6:2-5"
    ),

    # Psalm 46 — "Be still, and know that I am God"
    "Psalm 46": (
        "God is our refuge and strength, a very present help in trouble. "
        "Therefore we won't be afraid, though the earth changes, though the "
        "mountains are shaken into the heart of the seas. There is a river, "
        "the streams of which make the city of God glad. God is within her. "
        "She shall not be moved. God will help her at dawn. 'Be still, and "
        "know that I am God.' "
        "— Psalm 46:1-5, 10"
    ),

    # Isaiah 62 — "you shall be called by a new name"
    "Isaiah 62": (
        "For Zion's sake I will not hold my peace. For Jerusalem's sake I "
        "will not rest until her righteousness shines out like the dawn, "
        "and her salvation like a burning lamp. The nations will see your "
        "righteousness, and all kings your glory. You will be called by a "
        "new name, which Yahweh's mouth will name. You will also be a crown "
        "of beauty in Yahweh's hand, and a royal diadem in your God's hand. "
        "— Isaiah 62:1-3"
    ),

    # John 4 — woman at the well, living water
    "John 4": (
        "Jesus answered her, 'Everyone who drinks of this water will thirst "
        "again, but whoever drinks of the water that I will give him will "
        "never thirst again; but the water that I will give him will become "
        "in him a well of water springing up to eternal life.' The woman "
        "said to him, 'Sir, give me this water, so that I don't get thirsty.' "
        "— John 4:13-15"
    ),

    # John 8 — "I am the light of the world"
    "John 8": (
        "Again, therefore, Jesus spoke to them, saying, 'I am the light of "
        "the world. He who follows me will not walk in the darkness, but "
        "will have the light of life.' "
        "— John 8:12"
    ),

    # Exodus 14 — parting of the Red Sea
    "Exodus 14": (
        "Moses said to the people, 'Don't be afraid. Stand still, and see "
        "the salvation of Yahweh which he will work for you today; for you "
        "will never again see the Egyptians whom you have seen today. "
        "Yahweh will fight for you, and you shall be still.' Yahweh said to "
        "Moses, 'Why do you cry to me? Speak to the children of Israel, that "
        "they go forward. Lift up your rod and stretch out your hand over "
        "the sea and divide it.' "
        "— Exodus 14:13-16"
    ),

    # Romans 8:15 — "Abba, Father"
    "Romans 8:15": (
        "For you didn't receive the spirit of bondage again to fear, but "
        "you received the Spirit of adoption, by whom we cry, 'Abba! Father!' "
        "The Spirit himself testifies with our spirit that we are children "
        "of God. "
        "— Romans 8:15-16"
    ),

    # Isaiah 6:3 — "Holy, Holy, Holy" (KADOSH)
    "Isaiah 6:3": (
        "One called to another, and said, 'Holy, holy, holy, is Yahweh of "
        "Armies! The whole earth is full of his glory!' The foundations of "
        "the thresholds shook at the voice of him who called, and the house "
        "was filled with smoke. "
        "— Isaiah 6:3-4"
    ),

    # Deuteronomy 6:4 — the SHEMA
    "Deuteronomy 6:4": (
        "Hear, Israel: Yahweh is our God. Yahweh is one. You shall love "
        "Yahweh your God with all your heart, with all your soul, and with "
        "all your might. These words, which I command you today, shall be "
        "on your heart; and you shall teach them diligently to your "
        "children, and shall talk of them when you sit in your house, and "
        "when you walk by the way, and when you lie down, and when you "
        "rise up. You shall bind them for a sign on your hand, and they "
        "shall be for frontlets between your eyes. You shall write them "
        "on the door posts of your house and on your gates. "
        "— Deuteronomy 6:4-9"
    ),

    # Zechariah 4:6 — "Not by might, nor by power, but by my Spirit"
    "Zechariah 4:6": (
        "The angel who talked with me returned and awakened me, as a man "
        "who is awakened out of his sleep. He said to me, 'What do you "
        "see?' I said, 'I have seen, and behold, a lamp stand all of gold, "
        "with its bowl on the top of it, and its seven lamps on it; there "
        "are seven pipes to each of the lamps which are on the top of it; "
        "and two olive trees by it, one on the right side of the bowl, "
        "and the other on the left side of it.' Then he answered and "
        "spoke to me, saying, 'This is the word of Yahweh to Zerubbabel, "
        "saying, Not by might, nor by power, but by my Spirit, says "
        "Yahweh of Armies.' "
        "— Zechariah 4:1-6"
    ),

    # Numbers 14:21 — KAVOD (glory filling the earth)
    "Numbers 14:21": (
        "But in very deed—as I live, and as all the earth shall be filled "
        "with Yahweh's glory—all those men who have seen my glory, and my "
        "signs, which I worked in Egypt and in the wilderness, yet have "
        "tempted me these ten times, and have not listened to my voice, "
        "surely they shall not see the land which I swore to their fathers. "
        "— Numbers 14:21-23"
    ),

    # Genesis 1:2 — RUACH hovering over the waters
    "Genesis 1:2": (
        "In the beginning, God created the heavens and the earth. The earth "
        "was formless and empty. Darkness was on the surface of the deep, "
        "and God's Spirit (ruach) was hovering over the surface of the "
        "waters. God said, 'Let there be light,' and there was light. God "
        "saw the light, and saw that it was good. God divided the light "
        "from the darkness. "
        "— Genesis 1:1-4"
    ),

    # Ezekiel 37 — valley of dry bones (alternative source for RUACH)
    "Ezekiel 37": (
        "Yahweh's hand was on me, and he brought me out in Yahweh's Spirit, "
        "and set me down in the middle of the valley; and it was full of "
        "bones. He caused me to pass by them all around: and behold, there "
        "were very many in the open valley; and behold, they were very dry. "
        "He said to me, 'Son of man, can these bones live?' I answered, "
        "'Lord Yahweh, you know.' Then he said to me, 'Prophesy over these "
        "bones, and tell them, you dry bones, hear Yahweh's word. The Lord "
        "Yahweh says to these bones: Behold, I will cause breath (ruach) "
        "to enter into you, and you shall live.' So I prophesied as I was "
        "commanded. As I prophesied, there was a noise, and behold, a "
        "shaking; and the bones came together, bone to its bone. I saw, "
        "and behold, there were sinews on them, and flesh came up, and "
        "skin covered them above; but there was no breath in them. "
        "— Ezekiel 37:1-8"
    ),
}


def verse_for(anchor: str) -> str:
    """
    Return the verse text for a scripture anchor, or empty string if
    we don't have one on file. Empty result means the description's
    scripture block is skipped entirely.
    """
    return VERSES.get((anchor or "").strip(), "")
