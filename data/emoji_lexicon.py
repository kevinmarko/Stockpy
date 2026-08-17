"""data/emoji_lexicon.py — hand-curated Emoji Sentiment Lexicon.

Backs ``ml.models.emoji_sentiment.EmojiSentimentModel``'s real emoji-parsing
component. ``EMOJI_SENTIMENT`` maps ~100 common Unicode emoji to a manually
assigned sentiment polarity in [-1, +1] based on ordinary usage (a person
happy/positive emoji -> positive, crying/angry -> negative, neutral
objects/symbols -> near zero).

Honesty note: this is a HAND-CURATED lexicon, not a verbatim reproduction of
a specific third-party dataset (e.g. Kralj Novak et al. 2015's "Emoji
Sentiment Ranking") -- no such dataset is vendored, downloaded, or read at
runtime. This mirrors ``signals/news_catalyst.py``'s own ``_lexicon_sentiment``
keyword-lexicon convention: a real, deterministic, always-available fallback
that this codebase is upfront about being curated rather than sourced from a
specific published table. Coverage is intentionally a practical common
subset (~100 emoji), not exhaustive of the ~3,700+ Unicode emoji that exist
-- an emoji not in the table contributes nothing to a score (never a
fabricated guess).
"""
from typing import Dict, List, Optional, Tuple

EMOJI_SENTIMENT: Dict[str, float] = {
    # Strongly positive
    "😀": 0.8, "😃": 0.8, "😄": 0.9, "😁": 0.8, "😆": 0.7, "😅": 0.5,
    "🤣": 0.8, "😂": 0.7, "🙂": 0.5, "🙃": 0.3, "😊": 0.8, "😇": 0.7,
    "🥰": 0.9, "😍": 0.9, "🤩": 0.9, "😘": 0.8, "😗": 0.5, "😚": 0.6,
    "😙": 0.6, "😋": 0.6, "😛": 0.4, "😜": 0.5, "🤪": 0.5, "😝": 0.4,
    "🤗": 0.7, "🤭": 0.4, "🥳": 0.9, "😎": 0.6, "🤓": 0.4, "🧐": 0.1,
    "👍": 0.8, "👏": 0.7, "🙌": 0.8, "🤝": 0.6, "💪": 0.6, "🔥": 0.6,
    "✅": 0.7, "🎉": 0.9, "🎊": 0.8, "⭐": 0.6, "🌟": 0.6, "💯": 0.7,
    "❤️": 0.9, "💕": 0.8, "💖": 0.8, "💰": 0.6, "💵": 0.5, "📈": 0.7,
    "🚀": 0.8, "🙏": 0.4, "😻": 0.7,
    # Mildly positive / neutral-leaning
    "😉": 0.3, "😌": 0.3, "🤔": 0.0, "😐": 0.0, "😑": -0.1, "🤨": -0.1,
    "😶": 0.0, "🙄": -0.3, "😏": 0.1,
    # Mildly negative
    "😒": -0.4, "😕": -0.3, "🙁": -0.4, "☹️": -0.5, "😟": -0.4, "😔": -0.4,
    "😞": -0.5, "😖": -0.5, "😣": -0.4, "😩": -0.5, "😫": -0.5, "🥱": -0.2,
    "😤": -0.3, "😠": -0.6, "😡": -0.8, "🤬": -0.9, "🥵": -0.3, "🥶": -0.3,
    # Strongly negative
    "😢": -0.7, "😭": -0.8, "😨": -0.6, "😰": -0.6, "😥": -0.5, "😓": -0.4,
    "🤯": -0.5, "😱": -0.6, "😳": -0.2, "🥺": -0.3, "😪": -0.3, "😷": -0.3,
    "🤒": -0.4, "🤕": -0.4, "🤢": -0.7, "🤮": -0.8, "💀": -0.6, "☠️": -0.7,
    "👎": -0.7, "❌": -0.6, "⚠️": -0.4, "📉": -0.7, "💸": -0.5, "😾": -0.5,
    "😿": -0.6, "🚨": -0.5, "🐻": -0.4,  # "bear" market slang usage
    "🐂": 0.5,  # "bull" market slang usage
}

# Broad Unicode emoji ranges (emoticons, misc symbols & pictographs,
# transport, supplemental symbols, dingbats, misc symbols). Not exhaustive
# of every emoji-adjacent codepoint -- a practical net wide enough to find
# characters worth looking up in EMOJI_SENTIMENT, not a Unicode-standard
# emoji classifier.
#
# Deliberately plain ordinal (int, int) bounds checked via ord(), not a
# compiled `re` character class -- CodeQL's py/overly-large-range query
# models Python regex character-class ranges over its own UTF-16 code-unit
# representation, and every one of these bounds is a supplementary-plane
# (astral) codepoint above U+FFFF that only exists as a surrogate pair in
# that representation. That mismatch made the query misreport each of these
# ranges as "overlapping" a synthetic �-� (U+FFFD, the Unicode
# replacement character) placeholder it substitutes for the un-representable
# surrogate half -- a false positive against CodeQL alerts #17-#20, not a
# real defect (the ranges below are correctly ordered and mutually disjoint;
# Python 3 `str`/`ord()` addresses code points directly and has no surrogate
# ambiguity to trip over). This form sidesteps the query's astral-range
# blind spot entirely rather than relying on a suppression comment.
_EMOJI_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x1F300, 0x1F5FF),
    (0x1F600, 0x1F64F),
    (0x1F680, 0x1F6FF),
    (0x1F900, 0x1F9FF),
    (0x2600, 0x27BF),
    (0x1FA70, 0x1FAFF),
)


def _is_emoji_char(ch: str) -> bool:
    codepoint = ord(ch)
    return any(lo <= codepoint <= hi for lo, hi in _EMOJI_RANGES)


def extract_emojis(text: str) -> List[str]:
    """Return every character in ``text`` that falls in a known emoji
    Unicode range, in order, duplicates included (repeated emoji count
    toward the average, matching ordinary sentiment-lexicon convention)."""
    if not text:
        return []
    return [ch for ch in text if _is_emoji_char(ch)]


def score_text_emojis(text: str) -> Optional[float]:
    """Mean EMOJI_SENTIMENT polarity of emoji found in ``text`` that are
    present in the lexicon, or ``None`` when no scoreable emoji is found
    (never a fabricated 0.0 for "no emoji" -- CONSTRAINT #4; the caller
    decides how to treat "no signal")."""
    found = [EMOJI_SENTIMENT[e] for e in extract_emojis(text) if e in EMOJI_SENTIMENT]
    if not found:
        return None
    return float(sum(found) / len(found))
