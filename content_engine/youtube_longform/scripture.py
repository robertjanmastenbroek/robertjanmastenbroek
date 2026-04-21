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
}


def verse_for(anchor: str) -> str:
    """
    Return the verse text for a scripture anchor, or empty string if
    we don't have one on file. Empty result means the description's
    scripture block is skipped entirely.
    """
    return VERSES.get((anchor or "").strip(), "")
