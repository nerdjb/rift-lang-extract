"""Tests for :mod:`rcextract.arabic`, the Arabic pre-shaper.

Two tiers, because the two halves have very different requirements.

The *segmentation* half -- :func:`runs_of`, :func:`encode_string` -- is where
the interesting logic lives and it needs nothing but the standard library, so it
is tested in full, always.  The property under test is the one that matters: the
output has to draw right to left, so every Arabic block is reversed, every Latin
word and digit sequence keeps its order, every character survives exactly once,
and every tag stays where it was written.

The *font* half needs fontTools, uharfbuzz and a Noto "UI" cut, so it is skipped
unless they are all present.  What it checks is the whole safety argument:
resolve the encoded strings the way the game will, one glyph per codepoint, and
require the HarfBuzz glyph sequence back.

    RCEXTRACT_GAME=/path/to/'Ratchet & Clank - Rift Apart' \\
        python -m unittest tests.test_arabic
"""

from __future__ import annotations

import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rcextract.arabic import (  # noqa: E402
    ARABIC_RE,
    FE_HI,
    FE_LO,
    SPUA_HI,
    SPUA_LO,
    TAG_RE,
    PreShaper,
    ShapeError,
    arabic_codepoints,
    collect_runs,
    encode_paragraph,
    encode_string,
    font_arabic_codepoints,
    font_cmap,
    is_arabic,
    is_arabic_letter,
    runs_of,
    tag_summary,
)

NOTO_DIR = os.environ.get("RCEXTRACT_NOTO", "/usr/share/fonts/noto")
NOTO = {
    "regular": os.path.join(NOTO_DIR, "NotoSansArabicUI-Regular.ttf"),
    "bold": os.path.join(NOTO_DIR, "NotoSansArabicUI-Bold.ttf"),
}
needs_noto = unittest.skipUnless(
    all(os.path.isfile(p) for p in NOTO.values()),
    "no Noto Sans Arabic UI at %s" % NOTO_DIR)

try:
    import fontTools  # noqa: F401
    import uharfbuzz  # noqa: F401
    have_fonts = True
except ImportError:
    have_fonts = False
needs_fonts = unittest.skipUnless(
    have_fonts and all(os.path.isfile(p) for p in NOTO.values()),
    "needs uharfbuzz, fontTools and the Noto Sans Arabic UI cut")


# Real strings, plus the things that make Arabic awkward: a mark that has to stay
# on its base, a Latin word inside an Arabic sentence, digits, and the game's
# own markup.
WORDS = ["الإعدادات", "تحميل اللعبة", "الأسلحة", "لا", "لغرضٍ", "عناصر"]
MIXED = ["تحميل اللعبة الآن", "اضغط على زر Start", "3D noises off",
         # A brand name with a space and a trademark: splitting this gap out
         # would reverse the two halves of the name.
         "سماعة PULSE 3D™ اللاسلكية",
         # A number that must stay in front of the word it follows.
         "إعداد مسبق 1",
         # The shape the game uses for a settings list: an indent entity, a
         # label, a literal ">" and a value.  This is where the gap rule has to
         # put four separate gaps in four different places.
         "&emsp;&ensp;الحركة المبسّطة > مفعّلة",
         "[GAMEPAD_ANNOTATION_INPUT] إعداد الاهتزاز > بدون"]
MARKUP = ["السطر الأول<br>السطر الثاني",
          "<span class='emphasis'>مهم</span> جدا"]
ALL = WORDS + MIXED + MARKUP

# Strings with tashkeel, written out by codepoint.  Authoring these as literal
# text is a trap: an editor that applies the bidi algorithm can reorder the
# marks relative to their letters, and a test that silently tests the wrong
# string still passes.
GHAR = "\u0644\u063A\u0631\u0636\u064D"        # لغرض  + damma
MUHAMMAD = "\u0645\u064F\u062D\u064E\u0645\u0651\u064E\u062F"   # محمَّد
MARKED = (GHAR, MUHAMMAD, "\u0628\u064E")      # بَ


def blocks_of(run: str):
    """Split a run into letter-plus-following-marks blocks, in logical order.

    This is the shape of a shaped run: a letter and its marks stay glued
    together, and the blocks come back from HarfBuzz in the opposite order.
    """
    out, cur = [], ""
    for ch in run:
        cur += ch
        if is_arabic_letter(ch):
            out.append(cur)
            cur = ""
    if cur:
        # A mark with no letter of its own still belongs to the one before it.
        if out:
            out[-1] += cur
        else:
            out.append(cur)
    return out


def reverse_blocks(text: str) -> str:
    """A stand-in shaper: reverse each run's blocks, keeping Latin logical.

    The assertions that matter hold for any correct run reversal, not for one
    particular shaped result, so this needs no font.
    """
    return encode_string(text, lambda run: "".join(reversed(blocks_of(run))))


def bare(text: str) -> str:
    return TAG_RE.sub("", text)


def stretches_of(text: str) -> list:
    """The maximal non-Arabic stretches of `text`, stripped and sorted.

    One stretch is one run the reversal is not allowed to touch, so the
    multiset of stretches has to survive the encoding untouched -- only their
    order may change.  This is the sharpest available statement of "Latin,
    digits and markup come through exactly as written", and it is what caught
    both halves of the gap rule.

    Two details earn their keep.  Edges are stripped, because a space moving
    from one side of a stretch to the other is a gap run landing correctly.  And
    tags become a sentinel rather than a space, because each tag-delimited block
    is encoded on its own and a block boundary is not a join.
    """
    out, cur = [], []
    for ch in TAG_RE.sub("\x00", text):
        if is_arabic(ch) or ch == "\x00":
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return sorted(s.strip() for s in out if s.strip())


# -- the ranges the encoding depends on -------------------------------------

class ArabicRangeTests(unittest.TestCase):
    def test_letters_punctuation_and_tatweel_are_arabic(self):
        for ch in "ابتثجحخدذرزسشصضطظعغفقكلمنهوي" + "،؛؟" + "ـ":
            self.assertTrue(is_arabic(ch), repr(ch))

    def test_marks_are_arabic_but_not_letters(self):
        for cp in range(0x064B, 0x0653):
            ch = chr(cp)
            self.assertTrue(is_arabic(ch), "U+%04X" % cp)
            self.assertFalse(is_arabic_letter(ch), "U+%04X" % cp)
        self.assertTrue(is_arabic_letter("ب"))

    def test_latin_digits_and_punctuation_are_not_arabic(self):
        for ch in "ABZaz09 \t\n<>&%-#":
            self.assertFalse(is_arabic(ch), repr(ch))

    def test_presentation_forms_and_private_use_are_arabic(self):
        # If these were not, the reordering would hand them back unshaped and
        # the engine would draw them left to right.
        for cp in (FE_LO, FE_LO + 1, FE_HI - 1, SPUA_LO, SPUA_LO + 1, SPUA_HI):
            self.assertTrue(is_arabic(chr(cp)), "U+%06X" % cp)

    def test_bmp_private_use_is_not_arabic(self):
        # The BMP private-use areas are unusable: the game's UI font already
        # occupies U+E000..U+E800, U+F000..U+F800 and U+0100..U+0200.
        for cp in (0xE000, 0xE800, 0xF000, 0xF800, 0x0100, 0x0200):
            self.assertFalse(is_arabic(chr(cp)), "U+%04X" % cp)

    def test_supplementary_private_use_area_is_private(self):
        self.assertGreater(SPUA_LO, 0xFFFF)
        self.assertLessEqual(SPUA_HI, 0x10FFFD)
        self.assertFalse(is_arabic(chr(SPUA_HI + 1)))

    def test_the_regex_matches_one_character_only(self):
        # ARABIC_RE.match must not match a prefix, or is_arabic("aب") is true.
        self.assertIsNone(ARABIC_RE.match("aب"))
        self.assertIsNone(ARABIC_RE.match("1ب"))

    def test_arabic_codepoints_ignores_latin(self):
        self.assertEqual(arabic_codepoints(["Hi اب"]), {0x0627, 0x0628})
        self.assertEqual(arabic_codepoints(["Hi 42"]), set())


# -- splitting a string into runs -------------------------------------------

class RunSplittingTests(unittest.TestCase):
    def test_single_script_is_one_run(self):
        self.assertEqual(runs_of("ابت"), [(True, "ابت")])
        self.assertEqual(runs_of("abc"), [(False, "abc")])

    def test_alternating_scripts_split(self):
        self.assertEqual(runs_of("ابCDج"),
                         [(True, "اب"), (False, "CD"), (True, "ج")])

    def test_spaces_form_their_own_run(self):
        self.assertEqual(runs_of("اب جد"),
                         [(True, "اب"), (False, " "), (True, "جد")])

    def test_empty_string_has_no_runs(self):
        self.assertEqual(runs_of(""), [])

    def test_single_character(self):
        self.assertEqual(runs_of("ب"), [(True, "ب")])

    def test_marks_travel_with_their_letter(self):
        # A mark must not split the word, or its base would lose its shape.
        self.assertEqual(runs_of("بَ"), [(True, "بَ")])
        self.assertEqual(runs_of("بَتَ"), [(True, "بَتَ")])

    def test_whitespace_is_a_run_of_its_own(self):
        # Not cosmetic: the reversal moves whole runs, so a space left attached
        # to a Latin run travels to the wrong side of it.
        self.assertEqual(runs_of("اب 1"),
                         [(True, "اب"), (False, " "), (False, "1")])
        self.assertEqual(runs_of("اب Open"),
                         [(True, "اب"), (False, " "), (False, "Open")])
        self.assertEqual(runs_of("اب  Open"),
                         [(True, "اب"), (False, "  "), (False, "Open")])

    def test_a_gap_inside_a_latin_phrase_is_not_split_out(self):
        # The other half of the rule, and the one that punishes getting it
        # wrong the other way: a space between two non-Arabic runs separates
        # words of one phrase, and splitting it would reverse the phrase too.
        self.assertEqual(runs_of("PULSE 3D"),
                         [(False, "PULSE 3D")])
        self.assertEqual(runs_of("3D noises off"),
                         [(False, "3D noises off")])
        self.assertEqual(runs_of("PULSE 3D™ اب"),
                         [(False, "PULSE 3D™"), (False, " "), (True, "اب")])
        # A run built from several pieces must come back as one string, not as
        # a list of them, so the join has to happen too and not just the drop.
        self.assertEqual(runs_of("ب PULSE 3D™ اب"),
                         [(True, "ب"), (False, " "), (False, "PULSE 3D™"),
                          (False, " "), (True, "اب")])

    def test_phrase_order_survives_the_reversal(self):
        # The end-to-end form of the test above: reversing the runs must not
        # swap the words of a Latin phrase, and must still put the gaps back.
        self.assertEqual(encode_paragraph("PULSE 3D™ اب", lambda s: s),
                         "اب PULSE 3D™")

    def test_runs_reassemble_into_the_original(self):
        for text in ALL:
            self.assertEqual("".join(c for _ar, c in runs_of(text)), text, text)


# -- markup ------------------------------------------------------------------

class MarkupTests(unittest.TestCase):
    def test_tags_are_left_exactly_where_they_were_written(self):
        for text in ALL:
            self.assertEqual(TAG_RE.findall(reverse_blocks(text)),
                             TAG_RE.findall(text), text)

    def test_text_before_the_first_tag_is_kept(self):
        self.assertEqual(reverse_blocks("أب<br>"), "بأ<br>")

    def test_text_after_the_last_tag_is_kept(self):
        # The regression that matters: a trailing block after the final tag has
        # to be encoded, not dropped.
        self.assertEqual(reverse_blocks("<br>ج"), "<br>ج")

    def test_a_br_splits_the_string_into_independently_reversed_lines(self):
        # The game lays the lines out itself, so each line is reversed on its
        # own and the lines keep their order.
        self.assertEqual(reverse_blocks("أب<br>ج"), "بأ<br>ج")
        self.assertEqual(reverse_blocks("اب<br>جد"), "با<br>دج")

    def test_a_span_stays_wrapped_around_the_same_text(self):
        self.assertEqual(reverse_blocks("<span class='x'>أب</span>"),
                         "<span class='x'>بأ</span>")

    def test_a_span_in_the_middle_keeps_both_ends_in_place(self):
        out = reverse_blocks("ا<span class='x'>ب</span>ج")
        self.assertEqual(TAG_RE.findall(out), ["<span class='x'>", "</span>"])
        self.assertEqual(bare(out), "ابج")

    def test_consecutive_brs_are_all_kept(self):
        text = "أ<br><br>ب"
        self.assertEqual(TAG_RE.findall(reverse_blocks(text)),
                         TAG_RE.findall(text))
        self.assertEqual(bare(reverse_blocks(text)), bare(text))

    def test_encode_paragraph_takes_one_tag_free_block(self):
        self.assertEqual(encode_paragraph("أب", lambda r: r[::-1]), "بأ")
        self.assertEqual(encode_paragraph("ab", lambda r: r[::-1]), "ab")

    def test_tag_summary_counts_each_tag(self):
        self.assertEqual(tag_summary(["a<br>b<br>c"]), "<br> x2")
        self.assertEqual(tag_summary(["plain"]), "none")
        # The real corpus: 227 line breaks.
        self.assertEqual(tag_summary(["x<br>y"] * 227), "<br> x227")


# -- the reversal itself -----------------------------------------------------

class ReversalTests(unittest.TestCase):
    def test_arabic_comes_out_reversed(self):
        self.assertEqual(reverse_blocks("ابت"), "تبا")
        self.assertEqual(reverse_blocks("ا"), "ا")
        self.assertEqual(reverse_blocks("لغ"), "غل")

    def test_letter_plus_mark_blocks_come_back_reversed_but_intact(self):
        for word in (GHAR, MUHAMMAD):
            blocks = blocks_of(word)
            self.assertGreater(len(blocks), 1, word)
            self.assertEqual(reverse_blocks(word),
                             "".join(reversed(blocks)), word)

    def test_marks_stay_glued_to_their_letter(self):
        # A mark owns no advance of its own; if it were emitted as a separate
        # codepoint the engine would draw it as a lone floating diacritic.
        for word in MARKED:
            out = reverse_blocks(word)
            for block in blocks_of(word):
                self.assertIn(block, out, "%r lost the block %r" % (word, block))

    def test_br_stays_between_the_same_two_lines(self):
        self.assertEqual(reverse_blocks("أب<br>ج"), "بأ<br>ج")

    def test_latin_keeps_its_order_inside_an_rtl_paragraph(self):
        # The Unicode bidi algorithm keeps an LTR run logical, so "Open" must
        # still read "Open" when the line is drawn right to left -- and the gap
        # between the two scripts has to land between them, not past the 1.
        self.assertEqual(reverse_blocks("اب Open"), "Open با")
        self.assertEqual(reverse_blocks("اب Open جد"), "دج Open با")

    def test_a_trailing_number_keeps_the_space_in_front_of_it(self):
        # The regression this guards: the run " 1" used to be reversed as a
        # whole, landing the gap on the far side of the digit.
        self.assertEqual(reverse_blocks("اب 1"), "1 با")
        # إعداد مسبق 1  ->  1 قبسم دادعإ
        self.assertEqual(reverse_blocks("إعداد مسبق 1"),
                         "1 " + reverse_blocks("مسبق") + " "
                         + reverse_blocks("إعداد"))

    def test_digits_keep_their_order(self):
        self.assertEqual(reverse_blocks("اب 42"), "42 با")

    def test_a_latin_phrase_on_its_own_is_left_alone(self):
        # The other half of the gap rule.  A phrase with no Arabic in it is one
        # run, so there is nothing to reverse: breaking its spaces out would
        # swap the words, which is what the run split used to do.
        self.assertEqual(reverse_blocks("3D noises"), "3D noises")
        self.assertEqual(reverse_blocks("PULSE 3D™"), "PULSE 3D™")

    def test_a_symmetric_neutral_run_survives(self):
        # "(3D)" has a space on each side, so reversing the run as a whole
        # leaves it in the gap between the two Arabic words.
        self.assertEqual(reverse_blocks("أ (3D) ب"), "ب (3D) أ")

    def test_every_source_character_survives_exactly_once(self):
        for text in ALL:
            out = reverse_blocks(text)
            self.assertEqual(sorted(bare(out)), sorted(bare(text)), text)

    def test_every_non_arabic_stretch_survives_verbatim(self):
        # The reversal may reorder stretches -- that is the whole point -- but
        # never rewrite one.  A brand name, a settings row and an indent entity
        # all live or die on this.
        for text in ALL:
            out = reverse_blocks(text)
            self.assertEqual(stretches_of(out), stretches_of(text), text)

    def test_every_space_survives(self):
        # Not implied by the two tests above: a space that migrates from the
        # start of a line to its end is still there, and that is the fix the
        # gap run exists to make, so the count is the thing to pin down.
        for text in ALL:
            self.assertEqual(bare(reverse_blocks(text)).count(" "),
                             bare(text).count(" "), text)

    def test_the_number_of_codepoints_never_exceeds_the_source(self):
        # A shaped run is at most one codepoint per block, never one per
        # character, so pre-shaping can only ever shorten Arabic.
        for text in ALL:
            self.assertLessEqual(len(bare(reverse_blocks(text))),
                                 len(bare(text)), text)


class CollectRunTests(unittest.TestCase):
    def test_deduplicates_in_first_seen_order(self):
        self.assertEqual(collect_runs(["اب", "جد", "اب"]), ["اب", "جد"])

    def test_tags_do_not_fuse_two_words_into_one_run(self):
        # "أ" and "ب" are separate words either side of a <br> and must not
        # become the run "أب", which would shape them as a joined pair.
        self.assertEqual(collect_runs(["أ<br>ب"]), ["أ", "ب"])

    def test_only_arabic_runs_are_collected(self):
        self.assertEqual(collect_runs(["ab 12 <br>"]), [])

    def test_every_run_the_encoder_asks_for_is_collected(self):
        # collect_runs and encode_string have to agree, or encode() raises at
        # the worst possible moment.
        for text in ALL:
            for is_ar, chunk in runs_of(TAG_RE.sub(" ", text)):
                if is_ar:
                    self.assertIn(chunk, collect_runs([text]), text)


# -- argument checking, which needs no font at all ---------------------------

class ShapeErrorTests(unittest.TestCase):
    def _shaper(self):
        return PreShaper({"regular": "a.ttf"}, {"regular": "b.ttf"})

    def test_encode_before_prepare_is_refused(self):
        with self.assertRaisesRegex(ShapeError, "prepare"):
            self._shaper().encode("ابت")

    def test_font_bytes_before_prepare_is_refused(self):
        with self.assertRaisesRegex(ShapeError, "prepare"):
            self._shaper().font_bytes("regular")

    def test_verify_before_prepare_is_refused(self):
        with self.assertRaisesRegex(ShapeError, "prepare"):
            self._shaper().verify(["ابت"])

    def test_styles_must_match(self):
        with self.assertRaisesRegex(ShapeError, "same styles"):
            PreShaper({"regular": "a"}, {"bold": "b"})

    def test_no_styles_is_refused(self):
        with self.assertRaisesRegex(ShapeError, "no font styles"):
            PreShaper({}, {})

    def test_missing_font_file_is_reported(self):
        shaper = PreShaper({"regular": "/nonexistent.ttf"},
                           {"regular": "/nonexistent2.ttf"})
        with self.assertRaisesRegex(ShapeError, "no such font file"):
            shaper.prepare(["ابت"])


# -- the real pipeline -------------------------------------------------------

def latin_only_fonts() -> dict:
    """Two Arabic-free TTFs to stand in for Proxima Nova.

    Subsetted from the Noto "UI" cuts so that merging the Arabic back in
    exercises a real merge, and so the two styles are genuinely different files
    the way Proxima Regular and Proxima Bold are.
    """
    from fontTools import subset

    options = subset.Options()
    options.layout_features = ["*"]
    options.layout_scripts = ["*"]
    out = {}
    for style, path in NOTO.items():
        font = subset.load_font(path, options)
        try:
            sub = subset.Subsetter(options=options)
            sub.populate(unicodes=set(range(0x20, 0x7F)))
            sub.subset(font)
            buf = io.BytesIO()
            font.save(buf)
            out[style] = buf.getvalue()
        finally:
            font.close()
    return out


@needs_fonts
class RealShaperTests(unittest.TestCase):
    """The full pipeline against real fonts.

    Skipped unless uharfbuzz, fontTools and the Noto "UI" cuts are all present.
    """

    @classmethod
    def setUpClass(cls):
        cls.base = latin_only_fonts()
        cls.shaper = PreShaper(cls.base, NOTO).prepare(ALL)

    def test_verify_passes(self):
        # This is the whole argument: resolve the encoded strings the way the
        # engine will, one glyph per codepoint, and get the HarfBuzz glyph
        # sequence back.  If this holds the page the game draws is the page a
        # shaper would have drawn.
        self.shaper.verify(ALL)

    def test_every_run_survives_shaping(self):
        # encode() re-derives each run's own source text from the shaping
        # clusters and refuses if the pieces do not add back up, so simply
        # getting here means every run round-tripped.
        runs = collect_runs(ALL)
        self.assertGreater(len(runs), 5)
        for run in runs:
            self.assertTrue(run.strip(), run)

    def test_encoded_codepoints_all_resolve(self):
        for style in ("regular", "bold"):
            cmap = font_cmap(self.shaper.font_bytes(style))
            for text in ALL:
                for ch in self.shaper.encode(text, style):
                    if is_arabic(ch):
                        self.assertIn(ord(ch), cmap,
                                      "U+%04X in %s" % (ord(ch), style))

    def test_letterforms_use_presentation_forms_where_they_exist(self):
        # If everything landed in the private use area the strings would be
        # unreadable in a text editor and in the game's own tools.
        forms = self.shaper.presentation_form_codepoints
        self.assertGreater(len(forms), 0)
        for cp in forms:
            self.assertTrue(FE_LO <= cp < FE_HI, "U+%04X" % cp)
        self.assertGreater(len(self.shaper.private_use_codepoints), 0)

    def test_private_use_codepoints_are_sequential_and_in_range(self):
        private = sorted(self.shaper.private_use_codepoints)
        self.assertEqual(private, list(range(SPUA_LO, SPUA_LO + len(private))))
        self.assertLessEqual(max(private), SPUA_HI)

    def test_one_codepoint_means_the_same_thing_in_both_weights(self):
        # A string is not told which weight it will be drawn in, so the two
        # fonts have to agree on what each codepoint looks like.
        for text in ALL:
            self.assertEqual(self.shaper.encode(text, "regular"),
                             self.shaper.encode(text, "bold"), text)

    def test_the_font_grew_and_kept_its_identity(self):
        from fontTools.ttLib import TTFont
        for style in ("regular", "bold"):
            old = TTFont(io.BytesIO(self.base[style]), lazy=True)
            new = TTFont(io.BytesIO(self.shaper.font_bytes(style)), lazy=True)
            self.assertGreater(new["maxp"].numGlyphs,
                               old["maxp"].numGlyphs, style)
            # A rescaled font would render at the wrong size, which is why the
            # Noto "UI" cut is the one that works.
            self.assertEqual(new["head"].unitsPerEm, old["head"].unitsPerEm,
                             "%s was rescaled" % style)
            self.assertEqual(new["name"].getDebugName(4),
                             old["name"].getDebugName(4), style)

    def test_the_retail_font_had_no_arabic_and_the_new_one_does(self):
        # The premise, and the whole reason the font is part of the mod.
        for style in ("regular", "bold"):
            self.assertEqual(font_arabic_codepoints(self.base[style]), set(),
                             "the stand-in font is meant to have no Arabic")
            self.assertTrue(font_arabic_codepoints(self.shaper.font_bytes(style)),
                            "%s gained no Arabic" % style)

    def test_the_rebuilt_font_still_shapes(self):
        # The point of the *UI* cut: a complete letterform per character, so
        # shaping produces real glyphs rather than .notdef boxes.
        import uharfbuzz as hb
        for style in ("regular", "bold"):
            face = hb.Face(hb.Blob(self.shaper.font_bytes(style)))
            font = hb.Font(face)
            for text in WORDS:
                buf = hb.Buffer()
                buf.add_str(text)
                buf.guess_segment_properties()
                hb.shape(font, buf)
                self.assertTrue(buf.glyph_infos, "%s produced nothing" % style)
                self.assertNotIn(0, [i.codepoint for i in buf.glyph_infos],
                                 "%s produced a .notdef for %r" % (style, text))

    def test_encode_all_keeps_the_keys(self):
        table = {"A": WORDS[0], "B": WORDS[1]}
        out = self.shaper.encode_all(table)
        self.assertEqual(set(out), {"A", "B"})
        for key, value in table.items():
            self.assertEqual(out[key], self.shaper.encode(value))

    def test_prepare_is_idempotent(self):
        again = self.shaper.prepare(["نص مختلف"])
        self.assertIs(again, self.shaper)

    def test_an_unprepared_run_is_reported(self):
        shaper = PreShaper(self.base, NOTO).prepare(["ابت"])
        with self.assertRaisesRegex(ShapeError, "never shaped"):
            shaper.encode("جد مختلف")

    def test_an_unknown_style_is_reported(self):
        with self.assertRaisesRegex(ShapeError, "no such style"):
            self.shaper.font_bytes("italic")

    def test_add_cmap_does_not_hide_the_original_codepoints(self):
        # A partial format 12 makes HarfBuzz and FreeType use *only* that
        # subtable, so every original codepoint would become .notdef and the
        # whole UI would be boxes.
        for style in ("regular", "bold"):
            before = set(font_cmap(self.base[style]))
            after = set(font_cmap(self.shaper.font_bytes(style)))
            self.assertTrue(before <= after, "%s lost codepoints" % style)
            self.assertTrue(self.shaper.private_use_codepoints <= after, style)
            self.assertGreater(len(after), len(before), style)


if __name__ == "__main__":
    unittest.main()
