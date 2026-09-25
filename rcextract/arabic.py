"""Pre-shape Arabic so an engine that does not shape still draws it correctly.

Why this exists
---------------

Rift Apart's text engine ships HarfBuzz, but it does not run it on this text.
For the strings in ``d/localization`` it maps each codepoint through the font's
``cmap`` and advances to the right: no shaping, no bidi, no right-to-left.  Feed
it Arabic and you get isolated letterforms in reverse order.

None of the fonts in ``d/userinterface`` contains a single Arabic codepoint, so
before any of this the same text drew as tofu.  Shipping an Arabic font is
therefore necessary but not sufficient: the glyphs appear, but unjoined and
backwards.

What this module does
---------------------

It does the shaping at build time and hands the engine a finished glyph
sequence, so the engine's dumb left-to-right loop draws the right thing:

1. every maximal Arabic run is shaped with HarfBuzz, which joins the letters and
   returns them in visual (right to left) order;
2. each resulting glyph is written into the string as a codepoint -- an
   :data:`FE_LO`-:data:`FE_HI` Arabic Presentation Form where one exists, and
   otherwise a codepoint from the Supplementary Private Use Area that this
   module adds to the font's ``cmap``;
3. the runs are emitted in reverse logical order, which is what a left-to-right
   renderer needs in order to *look* right to left.

Latin words and digits keep logical order inside the reversed run list, which is
what the Unicode bidi algorithm prescribes for an RTL paragraph anyway, and
``<br>`` and ``<span>`` tags stay anchored where they were written.

Why the "UI" cut of Noto
------------------------

The obvious source font is the wrong one.  Noto Naskh Arabic and Noto Sans
Arabic build each letter from a zero-advance base glyph plus separate dot and
stroke components, positioned by GPOS mark attachment.  That is a legitimate
design and HarfBuzz plus GPOS renders it perfectly -- but this engine applies
neither, so the components would land in the wrong place.  The ``* UI`` cuts
ship self-contained letterforms: one glyph per character, no GPOS dependency.
Both cuts are ``unitsPerEm=1000``, so they merge into the UI font the game
actually loads without rescaling.

What this does not fix
----------------------

The engine is still not doing bidi.  Display and reading are correct, but
anything that depends on real text layout -- caret movement, text selection,
and possibly line wrapping at some widths -- will be wrong.  Making the game
shape Arabic natively would need it to resolve an Arabic *language* slot, which
is a different change; see the README.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
from typing import Callable, Iterable, Mapping, Sequence

__all__ = [
    "FE_LO", "FE_HI", "SPUA_LO", "SPUA_HI", "TAG_RE", "ARABIC_RE",
    "is_arabic", "is_arabic_letter", "arabic_codepoints",
    "runs_of", "encode_paragraph", "encode_string",
    "collect_runs", "tag_summary", "add_cmap", "build_merged_font", "font_cmap",
    "font_arabic_codepoints", "UI_FONT_ASSETS", "extract_ui_fonts",
    "PreShaper", "ShapeError",
]

#: The two assets the game loads for UI text, by table-of-contents index.
#: Both are Proxima Nova, ``unitsPerEm=1000``, and neither contains a single
#: Arabic codepoint -- which is why the font has to be replaced as well as the
#: strings.  A mod can repoint an asset at a new archive, so shipping new
#: versions of these two is the same operation as shipping new localisation
#: containers; see :mod:`rcextract.mod`.
UI_FONT_ASSETS = {"regular": 47779, "bold": 75673}


#: First codepoint of Arabic Presentation Forms-B.  These are the standard,
#: already-joined letterforms, so where one exists we use it in preference to
#: inventing a codepoint.
FE_LO, FE_HI = 0xFE70, 0xFF00

#: Supplementary Private Use Area-A.  Nothing legitimate lives here and no
#: shipped font uses it, so it is the safe place to put the joining forms that
#: Unicode never gave a presentation-form codepoint.  The BMP private-use areas
#: are not an option: the game's UI font already uses U+E000..U+E800,
#: U+F000..U+F800 and U+0100..U+0200.
SPUA_LO, SPUA_HI = 0xF0000, 0xFFFFD

#: The game's inline markup.  ``<br>`` and balanced ``<span class=...>`` pairs
#: are the only tags that occur in the Arabic translation.
TAG_RE = re.compile(r"<[^>]*>")

# Arabic, its supplement and extension, both presentation-form blocks, and the
# supplementary private-use range -- because that is where this module writes
# the forms Unicode has no codepoint for.  Anything emitted as Arabic has to
# classify as Arabic, or the run reordering below would pass it through
# unencoded and leave it looking Latin.
ARABIC_RE = re.compile(
    "["
    "؀-ۿ"                    # Arabic
    "ݐ-ݿ"                    # Arabic Supplement
    "ࢠ-ࣿ"                    # Arabic Extended-A
    "ﭐ-﷿"                    # Arabic Presentation Forms-A
    "ﹰ-﻿"                    # Arabic Presentation Forms-B
    "\U000f0000-\U000ffffd"   # Supplementary Private Use Area-A
    "]"
)

#: U+064B..U+0652, the Arabic combining marks (harakat).  They take no advance
#: of their own, so they ride along on whichever letter precedes them and must
#: not be counted as letters when checking that shaping preserved the text.
_MARKS = frozenset(range(0x064B, 0x0653))


class ShapeError(Exception):
    """Raised when text cannot be turned into a drawable glyph run."""


def is_arabic(ch: str) -> bool:
    """True for a character this module treats as part of an RTL run."""
    return bool(ARABIC_RE.match(ch))


def is_arabic_letter(ch: str) -> bool:
    """True for an Arabic letter, i.e. one that takes a joining form.

    Marks are excluded: a mark attaches to the letter before it rather than
    joining to it, so counting them as letters makes a round-trip check
    disagree with itself.
    """
    return is_arabic(ch) and ord(ch) not in _MARKS


def arabic_codepoints(texts: Iterable[str]) -> set:
    """Every Arabic codepoint that appears in `texts`."""
    return {ord(ch) for text in texts for ch in text if is_arabic(ch)}


# -- segmentation -----------------------------------------------------------
#
# Nothing in this half needs a third-party package, which is what makes it
# cheap to test.

#: Run classes used while scanning.  ``ARABIC`` never merges with anything,
#: ``GAP`` is whitespace, and ``OTHER`` is any other non-Arabic character.
_ARABIC_RUN, _GAP_RUN, _OTHER_RUN = 1, 0, 2


def _classify(text: str) -> list:
    """`text` as a list of ``(class, chunk)``, splitting where the class changes."""
    out = []
    cur = []
    cur_class = None
    for ch in text:
        cls = (_ARABIC_RUN if is_arabic(ch)
               else _GAP_RUN if ch.isspace() else _OTHER_RUN)
        if cur_class is not None and cls != cur_class:
            out.append((cur_class, "".join(cur)))
            cur = []
        cur.append(ch)
        cur_class = cls
    if cur:
        out.append((cur_class, "".join(cur)))
    return out


def runs_of(text: str) -> list:
    """Split `text` into ``(is_arabic, chunk)`` runs, in logical order.

    Splitting on script is what lets the reversal in :func:`encode_paragraph`
    keep Latin words and digits the right way round.  It is also right for
    joining: a space, a digit and a Latin letter are all non-joining in
    Unicode, so a letter pair either side of one of them has to be shaped
    separately anyway.

    Where whitespace lands is the subtle part, because the reversal moves whole
    runs and a space carried along with a run is carried to the wrong side of it.
    In ``إعداد مسبق 1`` a plain script split ends in the run ``" 1"``, and
    reversing moves that space out to the *left* of the 1, away from the word it
    separated.  Breaking it out as a run of its own puts the gap back where it
    was.

    But a gap is only its own run when Arabic is touching it.  Two non-Arabic
    runs with a space between them are one Latin phrase, and splitting that space
    off would reverse the phrase as well: ``PULSE 3D™`` would come back as
    ``3D™ PULSE``.  So a gap survives only if at least one neighbour is Arabic,
    and the rest are absorbed, after which neighbouring non-Arabic pieces are
    joined back up.
    """
    parts = _classify(text)

    kept = []
    for i, (cls, chunk) in enumerate(parts):
        if cls == _GAP_RUN:
            before = parts[i - 1] if i else None
            after = parts[i + 1] if i + 1 < len(parts) else None
            # A gap with Arabic on neither side is inside a single Latin
            # phrase: keep the phrase whole by folding the space back into the
            # run before it, where the join below will carry it along.  At a
            # string edge there is no neighbour to be wrong about, so it stays
            # split -- which puts a leading space at the far right and a
            # trailing one at the far left of an RTL paragraph, where they
            # belong.
            if (before is not None and after is not None
                    and before[0] != _ARABIC_RUN and after[0] != _ARABIC_RUN):
                kept[-1] = (kept[-1][0], kept[-1][1] + chunk)
                continue
        kept.append((cls, chunk))

    out = []
    for cls, chunk in kept:
        if out and out[-1][0] == _OTHER_RUN and cls == _OTHER_RUN:
            out[-1] = (_OTHER_RUN, out[-1][1] + chunk)
        else:
            out.append((cls, chunk))
    return [(cls == _ARABIC_RUN, chunk) for cls, chunk in out]


def encode_paragraph(paragraph: str, encode_run: Callable[[str], str]) -> str:
    """Encode one tag-free block of text into visual order.

    Reverses the order of the runs, not the characters within a run -- the whole
    point is that ``encode_run`` has already turned each Arabic run into its
    glyphs in visual order, so reversing the runs puts them back in the order a
    left-to-right renderer needs.
    """
    return "".join(encode_run(chunk) if is_ar else chunk
                   for is_ar, chunk in reversed(runs_of(paragraph)))


def encode_string(text: str, encode_run: Callable[[str], str]) -> str:
    """Encode a whole string, leaving every tag exactly where it was written.

    Tags split the string into blocks, and each block is encoded on its own.
    That is what makes the two awkward cases come out right without any bidi
    arithmetic of our own:

    * across a ``<br>``, each line is reversed internally and the lines keep
      their order, which is what the game wants -- it draws the lines itself;
    * an opening and closing ``<span>`` stay in place around text that is
      reversed between them, so the markup cannot be torn apart by the reversal.
    """
    out = []
    pos = 0
    for m in TAG_RE.finditer(text):
        if m.start() > pos:
            out.append(encode_paragraph(text[pos:m.start()], encode_run))
        out.append(m.group(0))
        pos = m.end()
    if pos < len(text):
        out.append(encode_paragraph(text[pos:], encode_run))
    return "".join(out)


def collect_runs(texts: Iterable[str]) -> list:
    """Every distinct Arabic run that :func:`encode_string` will ask to be shaped.

    Shaping is by far the slowest step and the same word recurs constantly
    across a UI string table, so the caller shapes each distinct run once.
    """
    seen = {}
    for text in texts:
        for is_ar, chunk in runs_of(TAG_RE.sub(" ", text)):
            if is_ar:
                seen.setdefault(chunk, None)
    return list(seen)


def tag_summary(texts: Iterable[str]) -> str:
    """A one-line description of the inline markup in `texts`, for a build log.

    Tags survive the reversal untouched, but their count is worth knowing before
    shipping: it is the number of places where a mistake in :func:`group_tags`
    would show up as visibly misplaced markup.
    """
    counts = {}
    for text in texts:
        for m in TAG_RE.finditer(text):
            counts[m.group(0)] = counts.get(m.group(0), 0) + 1
    if not counts:
        return "none"
    return ", ".join("%s x%d" % (tag, n)
                     for tag, n in sorted(counts.items(), key=lambda kv: -kv[1]))


# -- font surgery -----------------------------------------------------------
#
# From here down, fontTools is required.

def add_cmap(font, entries: Mapping[int, str]) -> int:
    """Give `font` a complete format 12 (full Unicode) ``cmap`` subtable.

    `entries` maps each codepoint to add to a glyph name.  The new subtable has
    to be *complete*, not merely carry the additions: HarfBuzz and FreeType
    rank a UCS-4 subtable above the BMP format 4 ones and then use only the
    subtable they picked, so a partial format 12 hides every original codepoint
    and the whole font renders as .notdef.  Returns the number of codepoints in
    the resulting subtable.
    """
    from fontTools.ttLib.tables._c_m_a_p import cmap_format_12

    full = dict(font.getBestCmap())
    for cp, name in entries.items():
        full.setdefault(cp, name)
    sub = cmap_format_12()
    sub.platformID, sub.platEncID, sub.language = 3, 10, 0
    sub.cmap = full
    keep = [t for t in font["cmap"].tables if t.format != 12]
    font["cmap"].tables = keep + [sub]
    return len(full)


def build_merged_font(base_ttf: str, noto_ttf: str, out_ttf: str,
                      wanted: set) -> str:
    """Graft Arabic glyphs and their shaping tables onto a base UI font.

    The Arabic half is subsetted to `wanted` with every layout feature and
    script retained, then merged onto `base_ttf`.  Names, ``unitsPerEm`` and the
    base font's own glyphs all survive, so the result is still the font the game
    asked for -- just with Arabic added.  Returns `out_ttf`.
    """
    from fontTools import subset
    from fontTools.merge import Merger

    options = subset.Options()
    options.layout_features = ["*"]
    options.layout_scripts = ["*"]
    options.name_IDs = [0, 1, 2, 3, 4, 5, 6]
    options.notdef_outline = False
    options.drop_tables = ["DSIG", "FFTM", "LTSH", "hdmx", "VDMX"]

    arabic_only = out_ttf + ".arabic.ttf"
    font = subset.load_font(noto_ttf, options)
    try:
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(unicodes=wanted)
        subsetter.subset(font)
        font.save(arabic_only)
    finally:
        # load_font keeps the source file open, and Merger wants paths, not
        # TTFont objects, so nothing else is going to close it for us.
        font.close()
    try:
        Merger().merge([base_ttf, arabic_only]).save(out_ttf)
    finally:
        if os.path.exists(arabic_only):
            os.unlink(arabic_only)
    return out_ttf


def font_cmap(data: bytes) -> dict:
    """``{codepoint: glyph id}`` as a font reader resolves it.

    That is the best subtable, not the union of every subtable -- the engine
    picks one the same way.
    """
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(data), lazy=True)
    try:
        gids = {name: i for i, name in enumerate(font.getGlyphOrder())}
        return {cp: gids[name] for cp, name in font.getBestCmap().items()}
    finally:
        font.close()


def font_arabic_codepoints(data: bytes) -> set:
    """The Arabic and presentation-form codepoints `data` already maps.

    Retail UI fonts return an empty set.  A font that *has* come through
    :class:`PreShaper` returns hundreds -- which is how a caller notices it is
    about to build on top of its own output rather than on the retail font.
    """
    from fontTools.ttLib import TTFont

    font = TTFont(io.BytesIO(data), lazy=True)
    try:
        return {cp for cp in font.getBestCmap() if is_arabic(chr(cp))}
    finally:
        font.close()


def extract_ui_fonts(root: str, styles=None) -> dict:
    """Pull the game's own UI fonts out of an install, as ``{style: ttf bytes}``.

    Reads whatever the table of contents currently points at, so this returns
    the Arabic-capable font if a mod is installed and the retail one otherwise.
    To build a font from scratch you want the retail one, so uninstall first --
    or check the result with :func:`font_arabic_codepoints`.
    """
    from .game import read_asset
    from .toc import Toc

    styles = tuple(styles or UI_FONT_ASSETS)
    toc = Toc.open(root)
    out = {}
    for style in styles:
        try:
            index = UI_FONT_ASSETS[style]
        except KeyError:
            raise ShapeError("no such style %r; have %s"
                             % (style, sorted(UI_FONT_ASSETS))) from None
        asset = toc.assets[index]
        out[style] = read_asset(root, toc.archive_name(asset.archive_index),
                                asset.offset, asset.size)
    return out


# -- shaping internals ------------------------------------------------------

def _materialise(font, work: str, name: str) -> str:
    """A path for `font`, which may be a path already or the bytes of a TTF.

    fontTools' ``Merger`` only accepts file paths, so bytes have to land on disk
    somewhere first.
    """
    if isinstance(font, (bytes, bytearray, memoryview)):
        path = os.path.join(work, name)
        with open(path, "wb") as fh:
            fh.write(bytes(font))
        return path
    if not os.path.isfile(font):
        raise ShapeError("no such font file: %s" % font)
    return font


def _presentation_forms(font) -> dict:
    """``{glyph name: lowest presentation-form codepoint}`` for one font.

    A name can be reachable through more than one presentation form, and
    ``cmap`` maps codepoints to names rather than the other way round, so this
    takes the lowest codepoint for each name.  Two distinct names can never
    claim the same codepoint, which is what makes one codepoint per shaped
    letterform well defined.
    """
    out = {}
    for cp, name in sorted(font.getBestCmap().items()):
        if FE_LO <= cp < FE_HI:
            out.setdefault(name, cp)
    return out


def _spans(clusters: list, run: str) -> list:
    """The source text each glyph of a shaped run came from.

    A cluster value is the index of a glyph's first source character, and a
    shaped RTL run comes back in visual order, so its cluster values count down
    from the end of the run.  Glyph *i* therefore owns the text from its own
    cluster up to the next one along, and the first glyph's cluster runs to the
    end of the run.  One cluster can yield several glyphs -- a base plus its
    marks -- and only the first of a repeated cluster value owns a span; the
    rest get ``""``.
    """
    out = []
    for i, start in enumerate(clusters):
        if i and start == clusters[i - 1]:
            out.append("")
        else:
            out.append(run[start:(clusters[i - 1] if i else len(run))])
    return out


def _shape_run(hb, shapers: dict, run: str):
    """Shape `run` in every style.  Returns ``(spans, {style: gids})``.

    Raises if a style did not shape right to left, or if two styles disagree
    about the glyph sequence or the clusters, since a single codepoint then
    could not name the same letterform in both.
    """
    per_style = {}
    clusters = None
    for style, shaper in shapers.items():
        buf = hb.Buffer()
        buf.add_str(run)
        buf.guess_segment_properties()
        hb.shape(shaper, buf)
        per_style[style] = [info.codepoint for info in buf.glyph_infos]
        these = [info.cluster for info in buf.glyph_infos]
        if these != sorted(these, reverse=True):
            raise ShapeError("%r did not shape right to left; clusters were %r"
                             % (run, these))
        if clusters is not None and these != clusters:
            raise ShapeError("%r clustered differently in %s: %r, not %r"
                             % (run, style, these, clusters))
        clusters = these
    if len({len(g) for g in per_style.values()}) != 1:
        raise ShapeError("%r shapes to a different number of glyphs in each "
                         "style -- %s -- so the styles are not compatible cuts "
                         "of one font" % (run, {s: len(g)
                                                for s, g in per_style.items()}))
    return _spans(clusters or [], run), per_style


# -- the shaper -------------------------------------------------------------

class PreShaper:
    """Turns Arabic text into codepoints that a non-shaping engine draws right.

    Build one per set of font styles.  The game loads a regular and a bold UI
    font, and a string is not told which of the two it will be drawn in, so both
    need the same Arabic treatment.  They are shaped in lockstep and paired by
    glyph *name*, so one codepoint names the same letterform in each.

    ::

        shaper = PreShaper(
            {"regular": "ProximaNova.ttf", "bold": "ProximaNova-Bold.ttf"},
            {"regular": "NotoSansArabicUI-Regular.ttf",
             "bold":    "NotoSansArabicUI-Bold.ttf"},
        )
        shaper.prepare(["الإعدادات", "Hello 5"])
        shaper.encode("الإعدادات")     # -> pre-shaped codepoints
        shaper.font_bytes("regular")    # -> the font to install as a mod asset

    All of the work happens in :meth:`prepare`; :meth:`encode` is a lookup after
    that.  The first style named is the reference: its glyph sequence is the one
    the encoded strings are built against.

    Each font may be given as a path or as the bytes of a TTF, so a caller that
    has already read a font out of the game need not write it to disk again.
    :func:`extract_ui_fonts` does that read.
    """

    def __init__(self, base_fonts: Mapping, noto_fonts: Mapping):
        if not base_fonts:
            raise ShapeError("no font styles given")
        if set(base_fonts) != set(noto_fonts):
            raise ShapeError("the base and Noto fonts must cover the same styles, "
                             "not %s and %s" % (sorted(base_fonts),
                                                sorted(noto_fonts)))
        self._styles = tuple(base_fonts)
        self._base = dict(base_fonts)
        self._noto = dict(noto_fonts)
        self._spans_of = {}                         # run -> [span, ...]
        self._gids = {s: {} for s in self._styles}  # style -> run -> [gid, ...]
        self._cp = {}                               # (style, gid) -> codepoint
        self._fonts = {}                            # style -> ttf bytes
        self._cmaps = {}                            # style -> {codepoint: gid}
        self._prepared = False

    @property
    def styles(self):
        return self._styles

    @property
    def codepoints(self) -> set:
        """Every codepoint this shaper wrote, across all styles."""
        return set(self._cp.values())

    @property
    def presentation_form_codepoints(self) -> set:
        """The shaped letterforms that Unicode already had a codepoint for."""
        return {cp for cp in self._cp.values() if cp < SPUA_LO}

    @property
    def private_use_codepoints(self) -> set:
        """The codepoints minted for shapes that Unicode gave no presentation form."""
        return {cp for cp in self._cp.values() if cp >= SPUA_LO}

    # -- construction -----------------------------------------------------

    def prepare(self, texts: Sequence[str]) -> "PreShaper":
        """Build the fonts and shape every run in `texts`.  Idempotent.

        `texts` must cover every string that will later be passed to
        :meth:`encode`; a run that was not prepared here raises rather than
        quietly coming out unshaped.
        """
        if self._prepared:
            return self

        # Check the arguments before importing anything.  A caller who points at
        # the wrong path should be told that, not told to install a package.
        for label, fonts in (("base", self._base), ("Noto", self._noto)):
            for style, font in fonts.items():
                if not isinstance(font, (bytes, bytearray, memoryview)) \
                        and not os.path.isfile(font):
                    raise ShapeError("no such %s font file for style %r: %s"
                                     % (label, style, font))

        import uharfbuzz as hb
        from fontTools.ttLib import TTFont

        texts = list(texts)
        # Subset the Arabic to what is actually used, plus every presentation
        # form: the encoded strings name the forms, so they have to be in the
        # font even where the source text never mentioned them.
        wanted = arabic_codepoints(texts) | set(range(FE_LO, FE_HI))

        work = tempfile.mkdtemp(prefix="rcextract-arabic-")
        try:
            paths = {s: os.path.join(work, s + ".ttf") for s in self._styles}
            for style in self._styles:
                base = _materialise(self._base[style], work, style + "-base.ttf")
                noto = _materialise(self._noto[style], work, style + "-noto.ttf")
                build_merged_font(base, noto, paths[style], wanted)

            shapers = {s: hb.Font(hb.Face(hb.Blob.from_file_path(paths[s])))
                       for s in self._styles}
            fonts = {s: TTFont(paths[s]) for s in self._styles}
            names = {s: fonts[s].getGlyphOrder() for s in self._styles}
            forms = {s: _presentation_forms(fonts[s]) for s in self._styles}

            cp_of_name = {}
            next_spua = SPUA_LO
            for run in collect_runs(texts):
                spans, per_style = _shape_run(hb, shapers, run)
                for style in self._styles:
                    for gid in per_style[style]:
                        name = names[style][gid]
                        if name == ".notdef":
                            raise ShapeError(
                                "%r needs a glyph the Arabic font does not have: "
                                "it resolved to .notdef in %s" % (run, style))
                        if name not in cp_of_name:
                            cp = forms[style].get(name)
                            if cp is None:
                                # A combining mark or an Arabic digit: shaped, but
                                # with no presentation form of its own to name it.
                                if next_spua > SPUA_HI:
                                    raise ShapeError("ran out of private-use "
                                                     "codepoints at %r" % run)
                                cp, next_spua = next_spua, next_spua + 1
                            cp_of_name[name] = cp
                        self._cp[(style, gid)] = cp_of_name[name]
                    self._gids[style][run] = per_style[style]
                self._spans_of[run] = spans

            # Publish each font with a codepoint minted for every shape Unicode
            # did not already have one for.
            for style in self._styles:
                extra = {cp: names[style][gid]
                         for (st, gid), cp in self._cp.items()
                         if st == style and cp >= SPUA_LO}
                if extra:
                    add_cmap(fonts[style], extra)
                # Save to memory, never back over the file HarfBuzz still has
                # mapped: rewriting a mapped file corrupts the shaper's view.
                buf = io.BytesIO()
                fonts[style].save(buf)
                data = buf.getvalue()
                self._fonts[style] = data
                self._cmaps[style] = font_cmap(data)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        self._prepared = True
        return self

    # -- use --------------------------------------------------------------

    def encode(self, text: str, style: str = None) -> str:
        """Encode one string into codepoints that draw as correct Arabic."""
        if not self._prepared:
            raise ShapeError("call prepare() before encode()")
        return encode_string(text, lambda run: self._encode_run(run, style))

    def encode_all(self, translations: Mapping) -> dict:
        """Encode a whole ``{key: text}`` table, keys unchanged.

        Takes a mapping rather than a sequence on purpose: iterating a dict
        would silently yield its keys.
        """
        return {key: self.encode(text) for key, text in translations.items()}

    def font_bytes(self, style: str) -> bytes:
        """The rebuilt font for `style`, ready to install as a mod asset."""
        if not self._prepared:
            raise ShapeError("call prepare() before font_bytes()")
        try:
            return self._fonts[style]
        except KeyError:
            raise ShapeError("no such style %r; have %s"
                             % (style, sorted(self._fonts))) from None

    def verify(self, texts: Sequence[str]) -> None:
        """Replay the engine on every run and raise if it would draw anything else.

        The engine looks each codepoint up in ``cmap`` and draws what it gets,
        one glyph per codepoint, left to right.  Resolving the encoded strings
        that way has to reproduce exactly the glyph sequence HarfBuzz produced
        from the original Arabic -- same glyphs, same order, ligatures and all.
        If that holds for every run, the page the engine draws is the page a
        shaper would draw.  Every style is checked, because a string may be
        drawn in any of them.
        """
        if not self._prepared:
            raise ShapeError("call prepare() before verify()")
        for style in self._styles:
            cmap = self._cmaps[style]
            for run, gids in self._gids[style].items():
                got = [cmap.get(ord(c)) for c in self._encode_run(run, style)]
                if got != gids:
                    raise ShapeError("%r resolves to glyphs %r in %s, expected %r"
                                     % (run, got, style, gids))
            for text in texts:
                for ch in self.encode(text, style):
                    cp = ord(ch)
                    if is_arabic(ch) and cp not in cmap:
                        raise ShapeError("U+%04X is not in the rebuilt %s font"
                                         % (cp, style))

    def _encode_run(self, chunk: str, style: str = None) -> str:
        spans = self._spans_of.get(chunk)
        if spans is None:
            raise ShapeError("%r was never shaped; pass a string containing it "
                             "to prepare() first" % chunk)
        # The glyphs come back as the run's blocks reversed, each block intact, so
        # putting the blocks back in order has to rebuild the run exactly.  This
        # catches a dropped letter, a reordered one, and a mark off its base.
        if "".join(reversed(spans)) != chunk:
            raise ShapeError("%r does not survive shaping: got %r"
                             % (chunk, "".join(reversed(spans))))
        style = self._styles[0] if style is None else style
        try:
            gids = self._gids[style][chunk]
        except KeyError:
            raise ShapeError("no such style %r; have %s"
                             % (style, sorted(self._styles))) from None
        return "".join(chr(self._cp[(style, gid)]) for gid in gids)
