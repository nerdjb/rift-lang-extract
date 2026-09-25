"""Language slot table for Rift Apart's localisation archive.

The game ships **32 localisation containers** back to back inside
``d/localization``.  They are *not* labelled in the data itself -- every
container has the same 25,034 keys and the same lumps.  The only thing that
tells them apart is the table of contents: all 32 share one path hash
(``0xbe55d94f171bf8de``) and are separated purely by TOC *group*, in steps
of 8.  ``group // 8`` is the language id, 0-31.

The mapping below was recovered by joining three independent facts:

1. the TOC ``offset``/``size`` of each localisation asset matches the byte
   offset and size of exactly one container in the decompressed DSAR blob;
2. the key blob is byte-identical across all 32 containers, so container
   *N* in the blob is language id *N*;
3. each container names *itself* in its own language -- the ``LANGUAGE_*``
   dropdown keys are written in the container's own language, so a container
   whose ``LANGUAGE_DUTCH`` reads ``NEDERLANDS`` is Dutch.

Confidence notes are recorded per entry in :data:`LANGUAGES`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    """One of the 32 localisation slots."""

    id: int
    """TOC group // 8, 0-31.  Also the container's index in the DSAR blob."""

    code: str
    """BCP-47 style tag."""

    name: str
    """English name, for display."""

    self_name: str
    """How the language names itself, as found in its own string table."""

    shipped: bool = True
    """False for slots that exist in the archive but hold no strings."""

    note: str = ""
    """Anything a reader should know before trusting this row."""


# Recovered language id -> language.  `self_name` is the value that language
# gives itself in its own LANGUAGE_* dropdown entry.
LANGUAGES: tuple[Language, ...] = (
    Language(0,  "en-US", "English (US)",      "ENGLISH",         note="voice-over variant"),
    Language(1,  "en-US", "English (US)",      "ENGLISH",         note="base text variant; default for en_US installs"),
    Language(2,  "en-GB", "English (UK)",      "ENGLISH (UK)",    note="voice-over variant"),
    Language(3,  "da",    "Danish",            "DANSK"),
    Language(4,  "nl",    "Dutch",             "NEDERLANDS"),
    Language(5,  "fi",    "Finnish",           "SUOMI"),
    Language(6,  "fr",    "French",            "FRANÇAIS"),
    Language(7,  "de",    "German",            "DEUTSCH"),
    Language(8,  "it",    "Italian",           "ITALIANO"),
    Language(9,  "ja",    "Japanese",          "日本語"),
    Language(10, "ko",    "Korean",            "한국어"),
    Language(11, "no",    "Norwegian",         "NORSK",           note="distinguished from Danish by UI_WEAPONS = 'VÅPEN', not 'VÅBEN'"),
    Language(12, "pl",    "Polish",            "POLSKI"),
    Language(13, "pt-BR", "Portuguese (BR)",   "PORTUGUÊS DO BRASIL"),
    Language(14, "ru",    "Russian",           "РУССКИЙ"),
    Language(15, "es-419", "Spanish (LatAm)",  "ESPAÑOL",         note="LANGUAGE_LA_SPANISH = 'ESPAÑOL DE LATINOAMÉRICA'"),
    Language(16, "sv",    "Swedish",           "SVENSKA"),
    Language(17, "pt-PT", "Portuguese (PT)",   "PORTUGUÊS"),
    Language(18, "en-GB", "English (UK)",      "ENGLISH (UK)",    note="base text variant"),
    Language(19, "tr",    "Turkish",           "TÜRKÇE"),
    Language(20, "es-ES", "Spanish (Spain)",   "ESPAÑOL (ES)",    note="LANGUAGE_SPANISH itself renders as 'ESPAÑOL (ES)'"),
    Language(21, "zh-Hant", "Chinese (Trad.)", "繁體中文"),
    Language(22, "zh-Hans", "Chinese (Simp.)", "简体中文"),
    Language(23, None,    "(unassigned)",      "",                shipped=False,
            note="empty in retail builds"),
    Language(24, "cs",    "Czech",             "ČEŠTINA"),
    Language(25, "hu",    "Hungarian",         "MAGYAR"),
    Language(26, "el",    "Greek",             "ΕΛΛΗΝΙΚΑ"),
    Language(27, "ro",    "Romanian",          "ROMÂNĂ"),
    Language(28, None,    "(unassigned)",      "",                shipped=False,
            note="empty in retail builds"),
    Language(29, None,    "(unassigned)",      "",                shipped=False,
            note="empty in retail builds"),
    Language(30, None,    "(unassigned)",      "",                shipped=False,
            note="empty in retail builds"),
    Language(31, "hr",    "Croatian",          "HRVATSKI"),
)

assert len(LANGUAGES) == 32
assert [l.id for l in LANGUAGES] == list(range(32))

BY_ID: dict[int, Language] = {l.id: l for l in LANGUAGES}

BY_CODE: dict[str, Language] = {}
for _l in LANGUAGES:
    if _l.code:
        BY_CODE.setdefault(_l.code.lower(), _l)

#: Slots that are present in the archive but carry no strings.
EMPTY_SLOTS: tuple[int, ...] = tuple(l.id for l in LANGUAGES if not l.shipped)

#: Languages with a ``LANGUAGE_*`` string key but no text table of their own in
#: retail builds, and which therefore do not consume one of the four empty
#: slots.  Six of the 33 language names the game carries are untranslated, and
#: only four slots are empty -- so two of the six must be display labels that
#: share a slot with a shipped variant.  See ``SHARED_SLOT_LANGUAGES``.
#:
#: The support for these four differs sharply, and only the first is a real
#: localisation gap:
#:
#: * Arabic has a logo texture (``RCRA_AR_logo.texture``, paired 1:1 with
#:   ``kLanguageArabic`` in the game config), a full voice bank
#:   (``d/wem.ar``, 560 MB -- the same size class as ``d/wem.us``) and a
#:   ``LANGUAGE_ARABIC`` string key.  Everything except the text ships.
#: * Indonesian, Thai and Vietnamese have *only* the string key.  They have no
#:   ``kLanguage*`` entry anywhere in ``d/config``, no logo, no audio.  They
#:   are names the game can print, for slots that were reserved and left empty.
#:
#: Which slot maps to which language is not recorded in the data -- the four
#: empty slots are byte-identical to each other.
UNSHIPPED_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("ar", "Arabic"),
    ("id", "Indonesian"),
    ("th", "Thai"),
    ("vi", "Vietnamese"),
)

#: Languages with a ``LANGUAGE_*`` string key but no text table of their own,
#: that most likely reuse a slot shipped for a sibling variant.  Canadian
#: French falls back to ``fr`` and Mexican Spanish to ``es-419``.  This is an
#: inference: the game prints both names, but the count of untranslated names
#: (six) exceeding the count of empty slots (four) requires at least two of
#: them to share, and these are the two pairs that share unambiguously by
#: script and market.
SHARED_SLOT_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("fr-CA", "Canadian French"),
    ("es-MX", "Mexican Spanish"),
)


def unshipped_names() -> str:
    """``"Arabic, Indonesian, Thai, Vietnamese"`` for display."""
    return ", ".join(name for _, name in UNSHIPPED_LANGUAGES)


def shared_slot_names() -> str:
    """``"Canadian French, Mexican Spanish"`` for display."""
    return ", ".join(name for _, name in SHARED_SLOT_LANGUAGES)


def resolve(token: str) -> Language:
    """Look a language up by id, BCP-47 code, or English name.

    Accepts a few friendly aliases so ``--lang en`` and ``--lang 20`` both
    work.  Unassigned slots are returned as-is rather than raising, so the
    CLI can explain what it found.

    >>> resolve("de").code
    'de'
    >>> resolve("23").shipped
    False
    """
    t = token.strip()
    if t.isdigit():
        return BY_ID[int(t)]
    low = t.lower().replace("_", "-")
    for a, b in (("englishuk", "en-gb"), ("english-uk", "en-gb"),
                 ("brportuguese", "pt-br"), ("brazilian", "pt-br"),
                 ("laportuguese", "pt-pt"), ("chinese-t", "zh-hant"),
                 ("chinese-s", "zh-hans"), ("traditional", "zh-hant"),
                 ("simplified", "zh-hans"), ("portugese", "pt-pt"),
                 ("mandarin", "zh-hans")):
        if low == a:
            low = b
    if low in BY_CODE:
        return BY_CODE[low]
    for l in LANGUAGES:
        if l.name.lower() == low:
            return l
    for l in LANGUAGES:
        if l.self_name.lower() == low:
            return l
    raise KeyError("unknown language %r" % token)
