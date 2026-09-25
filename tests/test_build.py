"""Tests for :mod:`rcextract.build`, the localisation writer.

The synthetic tests here pin down the serialiser's rules.  The one that
really matters -- rebuilding every shipped container and getting the same
bytes back -- needs a game install, so it lives in
``test_integration.py``; this file proves the rules it relies on.
"""

from __future__ import annotations

import contextlib
import io
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rcextract.build import (  # noqa: E402
    DATA_ORDER,
    FIRST_KEY,
    LOCALIZATION_TYPE_CRC,
    STRINGS_BLOCK,
    BuildError,
    build_container,
    key_hash,
    order_entries,
)
from rcextract.dat import Dat  # noqa: E402
from rcextract.lang import parse_container  # noqa: E402
from rcextract.lump_types import (  # noqa: E402
    LUMP_LANG_COUNT,
    LUMP_LANG_HASH_OVF,
    LUMP_LANG_KEYS,
    LUMP_LANG_KEY_ALT,
    LUMP_LANG_KEY_HASH,
    LUMP_LANG_KEY_OFF,
    LUMP_LANG_VALUES,
    LUMP_LANG_VALUE_OFF,
    LUMP_LANG_ZERO,
)


def parse(raw: bytes):
    """Read back a built container as a Dat plus a StringTable."""
    d = Dat(raw)
    return d, parse_container(d)


class TestKeyHash(unittest.TestCase):
    def test_known_value(self):
        # Pinned against the game's own key-hash table (0x06a58050), which
        # holds the hash of every key in every shipped language.
        self.assertEqual(key_hash("INVALID"), 0x0A8BDE3E)

    def test_is_deterministic(self):
        self.assertEqual(key_hash("UI_WEAPONS"), key_hash("UI_WEAPONS"))

    def test_differs_from_zlib(self):
        # The seed is the polynomial, not all-ones, and there is no final
        # inversion, so this must not agree with the usual CRC-32.
        import zlib
        self.assertNotEqual(key_hash("INVALID"), zlib.crc32(b"INVALID"))

    def test_is_case_sensitive(self):
        self.assertNotEqual(key_hash("INVALID"), key_hash("invalid"))

    def test_fits_in_u32(self):
        for k in ("INVALID", "UI_WEAPONS", "MENU_DIFFICULTY_TITLE", ""):
            self.assertLess(key_hash(k), 1 << 32)

    def test_empty_key(self):
        self.assertIsInstance(key_hash(""), int)


class TestEntryOrder(unittest.TestCase):
    def test_invalid_comes_first(self):
        self.assertEqual(order_entries(["B", "A"]), ["A", "B"])
        self.assertEqual(order_entries(["B", "INVALID", "A"])[0], "INVALID")

    def test_rest_is_ordinal(self):
        keys = ["MENU_Z", "MENU_A", "menu_b", "MENU_a"]
        self.assertEqual(order_entries(keys),
                         ["MENU_A", "MENU_Z", "MENU_a", "menu_b"])

    def test_sorts_by_utf16_code_unit(self):
        # U+1D400 encodes as the surrogate pair D8 35 DC 00.  Its lead byte
        # (D8) sorts below U+FF21's FF, so UTF-16 order is the reverse of
        # code point order here -- and the game uses the former.
        keys = ["\uff21", "\U0001d400"]
        self.assertEqual(sorted(keys), keys)                  # code point order
        self.assertEqual(order_entries(keys), ["\U0001d400", "\uff21"])

    def test_is_idempotent(self):
        keys = ["C", "INVALID", "A", "B"]
        once = order_entries(keys)
        self.assertEqual(once, order_entries(once))

    def test_rejects_duplicates(self):
        with self.assertRaises(BuildError):
            order_entries(["A", "A"])
        with self.assertRaises(BuildError):
            order_entries(["INVALID", "INVALID"])

    def test_empty_rejected(self):
        self.assertEqual(order_entries([]), [])


class TestContainerLayout(unittest.TestCase):
    def setUp(self):
        self.raw = build_container(["B", "INVALID", "A"], {"A": "alpha", "B": "beta"})

    def test_magic_and_type(self):
        self.assertEqual(self.raw[:4], b"1TAD")
        self.assertEqual(struct.unpack_from("<I", self.raw, 4)[0], LOCALIZATION_TYPE_CRC)

    def test_nine_lumps(self):
        d = Dat(self.raw)
        self.assertEqual(d.lump_count, 9)
        self.assertEqual(set(l.type_crc for l in d.lumps), set(DATA_ORDER))

    def test_declared_size_is_exact(self):
        d = Dat(self.raw)
        self.assertEqual(d.file_size, len(self.raw))

    def test_directory_is_sorted_by_crc(self):
        tags = [struct.unpack_from("<I", self.raw, 16 + 12 * i)[0] for i in range(9)]
        self.assertEqual(tags, sorted(tags))

    def test_strings_block(self):
        self.assertTrue(self.raw[16 + 108:16 + 108 + 36] == STRINGS_BLOCK)
        self.assertTrue(STRINGS_BLOCK.startswith(b"Localization Built File\x00"))

    def test_sections_are_16_byte_aligned(self):
        d = Dat(self.raw)
        for l in d.lumps:
            self.assertEqual(l.offset % 16, 0, "%#010x at %d" % (l.type_crc, l.offset))

    def test_payload_order_is_logical(self):
        d = Dat(self.raw)
        by_offset = [l.type_crc for l in sorted(d.lumps, key=lambda x: x.offset)]
        self.assertEqual(by_offset, list(DATA_ORDER))

    def test_first_lump_starts_after_strings_block(self):
        d = Dat(self.raw)
        first = min(l.offset for l in d.lumps)
        self.assertEqual(first, 16 + 108 + len(STRINGS_BLOCK))

    def test_sections_do_not_overlap(self):
        d = Dat(self.raw)
        ordered = sorted(d.lumps, key=lambda x: x.offset)
        for a, b in zip(ordered, ordered[1:]):
            self.assertLessEqual(a.offset + a.size, b.offset)


class TestEntries(unittest.TestCase):
    def test_count_lump(self):
        raw = build_container(["A", "B", "C"], {"A": "1"})
        self.assertEqual(Dat(raw).u32(LUMP_LANG_COUNT), 3)

    def test_key_blob_is_nul_terminated(self):
        raw = build_container(["A", "B"], {"A": "1", "B": "2"})
        d = Dat(raw)
        self.assertEqual(d.lump(LUMP_LANG_KEYS), b"A\x00B\x00")

    def test_key_offsets_point_at_keys(self):
        keys = ["A", "INVALID", "B"]
        d, t = parse(build_container(keys, {"A": "1", "B": "2"}))
        off = d.u32_array(LUMP_LANG_KEY_OFF)
        blob = d.lump(LUMP_LANG_KEYS)
        for i, k in enumerate(t.keys):
            self.assertEqual(blob[off[i]:off[i] + len(k)], k.encode())

    def test_key_hashes_match_the_keys(self):
        d, t = parse(build_container(["A", "B"], {"A": "1", "B": "2"}))
        self.assertEqual(list(d.u32_array(LUMP_LANG_KEY_HASH)),
                         [key_hash(k) for k in t.keys])

    def test_sorted_hash_table_is_ascending(self):
        d, _ = parse(build_container(["A", "B", "C", "D", "E"],
                                     {k: "v" for k in "ABCDE"}))
        s = d.u32_array(LUMP_LANG_KEY_ALT)
        self.assertEqual(list(s), sorted(s))

    def test_sorted_index_points_back_at_the_right_entry(self):
        keys = ["A", "B", "C", "D", "E"]
        d, t = parse(build_container(keys, {k: "v" for k in keys}))
        hashes = list(d.u32_array(LUMP_LANG_KEY_HASH))
        srt_h = list(d.u32_array(LUMP_LANG_KEY_ALT))
        raw_i = d.lump(LUMP_LANG_HASH_OVF)
        idx = struct.unpack("<%dH" % (len(raw_i) // 2), raw_i)
        self.assertEqual(len(idx), len(keys))
        for h, i in zip(srt_h, idx):
            self.assertEqual(hashes[i], h)

    def test_sorted_index_is_u16_not_u32(self):
        # 0x0cd2cfe9 is half the size of the u32 tables, which is what proves
        # it holds a u16 per entry rather than a u32 per *pair* of entries.
        keys = ["K%d" % i for i in range(50)]
        d = Dat(build_container(keys, {k: "v" for k in keys}))
        self.assertEqual(len(d.lump(LUMP_LANG_HASH_OVF)), 2 * len(keys))

    def test_flags_are_one_byte_then_three_nuls(self):
        d = Dat(build_container(["A", "B"], {"A": "1", "B": "2"}))
        raw = d.lump(LUMP_LANG_ZERO)
        self.assertEqual(len(raw), 4 * 2)
        self.assertEqual(raw[2:], b"\x00\x00\x00\x00\x00\x00")

    def test_flags_are_carried_through(self):
        raw = build_container(["A", "B"], {"A": "1", "B": "2"}, {"B": 0xAB})
        d = Dat(raw)
        blob = d.lump(LUMP_LANG_ZERO)
        _, t = parse(raw)
        self.assertEqual(blob[t.keys.index("B")], 0xAB)
        self.assertEqual(blob[t.keys.index("A")], 0)

    def test_flags_are_masked_to_a_byte(self):
        raw = build_container(["A"], {"A": "1"}, {"A": 0x1FF})
        self.assertEqual(Dat(raw).lump(LUMP_LANG_ZERO)[0], 0xFF)


class TestValues(unittest.TestCase):
    def test_value_blob_starts_with_nul(self):
        d = Dat(build_container(["A"], {"A": "x"}))
        self.assertEqual(d.lump(LUMP_LANG_VALUES)[0], 0)

    def test_untranslated_keeps_offset_zero(self):
        d, t = parse(build_container(["A", "B"], {"A": "x"}))
        self.assertEqual(t.get("B"), None)
        self.assertEqual(t.get("A"), "x")

    def test_offset_zero_is_the_empty_string(self):
        # This is what makes offset 0 a safe "untranslated" marker: the game
        # resolves it to "", never to a real translation.
        d = Dat(build_container(["A"], {"A": "x"}))
        self.assertEqual(d.lump(LUMP_LANG_VALUES)[:1], b"\x00")

    def test_missing_key_is_untranslated(self):
        _, t = parse(build_container(["A", "B"], {"A": "x"}))
        self.assertIsNone(t.get("B"))

    def test_empty_string_is_not_untranslated(self):
        # An explicit "" is a real (blank) translation, kept distinct from
        # None so a caller can blank a string on purpose.
        _, t = parse(build_container(["A", "B"], {"A": "x", "B": ""}))
        self.assertEqual(t.get("B"), "")
        self.assertIsNotNone(t.get("B"))

    def test_values_are_not_deduplicated(self):
        # The game stores each translated string separately, even when two
        # entries hold identical text; a writer that pooled them would still
        # load, but would not match the retail byte layout.
        d = Dat(build_container(["A", "B"], {"A": "same", "B": "same"}))
        self.assertEqual(d.lump(LUMP_LANG_VALUES), b"\x00same\x00same\x00")

    def test_utf8_is_used(self):
        _, t = parse(build_container(["A"], {"A": "\u0627\u0644\u0639\u0631\u0628\u064a\u0629"}))
        self.assertEqual(t.get("A"), "\u0627\u0644\u0639\u0631\u0628\u064a\u0629")

    def test_newline_and_tab_survive(self):
        v = "a\tb\nc\rd"
        _, t = parse(build_container(["A"], {"A": v}))
        self.assertEqual(t.get("A"), v)

    def test_embedded_nul_truncates(self):
        # A NUL cannot be represented; the reader stops there, same as the game.
        _, t = parse(build_container(["A"], {"A": "before\x00after"}))
        self.assertEqual(t.get("A"), "before")

    def test_all_untranslated(self):
        raw = build_container(["A", "B", "C"])
        d = Dat(raw)
        self.assertEqual(d.lump(LUMP_LANG_VALUES), b"\x00")
        self.assertEqual(set(d.u32_array(LUMP_LANG_VALUE_OFF)), {0})
        _, t = parse(raw)
        self.assertEqual(t.translated_count, 0)


class TestInputHandling(unittest.TestCase):
    def test_values_as_sequence(self):
        _, t = parse(build_container(["A", "B"], ["1", "2"]))
        self.assertEqual(t.get("A"), "1")
        self.assertEqual(t.get("B"), "2")

    def test_sequence_length_is_checked(self):
        with self.assertRaises(BuildError):
            build_container(["A", "B"], ["1"])

    def test_no_values_at_all(self):
        _, t = parse(build_container(["A", "B"]))
        self.assertEqual(t.translated_count, 0)

    def test_empty_key_set_rejected(self):
        with self.assertRaises(BuildError):
            build_container([])

    def test_too_many_entries_rejected(self):
        # The sorted-index table stores a u16 per entry.
        with self.assertRaises(BuildError):
            build_container(["K%d" % i for i in range(0x10000)],
                            {"K%d" % i: "v" for i in range(0x10000)})

    def test_duplicate_keys_rejected(self):
        with self.assertRaises(BuildError):
            build_container(["A", "A"])


class TestBuildFromTable(unittest.TestCase):
    def _table(self, keys=("A", "B", "INVALID"), values=None):
        if values is None:
            values = {"A": "1", "B": "2"}
        return parse_container(Dat(build_container(list(keys), values)))

    def test_rebuilds_identically(self):
        from rcextract.build import build_from_table
        t = self._table()
        self.assertEqual(build_from_table(t), build_container(
            t.keys, dict(zip(t.keys, t.values)), dict(zip(t.keys, t.flags))))

    def test_override_one_value(self):
        from rcextract.build import build_from_table
        _, t = parse(build_from_table(self._table(), {"A": "changed"}))
        self.assertEqual(t.get("A"), "changed")
        self.assertEqual(t.get("B"), "2")

    def test_override_preserves_untranslated(self):
        from rcextract.build import build_from_table
        t = self._table(["A", "B"], {})
        out = parse(build_from_table(t, {"A": "x"}))[1]
        self.assertEqual(out.get("A"), "x")
        self.assertIsNone(out.get("B"))

    def test_override_can_untranslate(self):
        from rcextract.build import build_from_table
        _, out = parse(build_from_table(self._table(), {"A": None}))
        self.assertIsNone(out.get("A"))

    def test_unknown_override_key_rejected(self):
        from rcextract.build import build_from_table
        with self.assertRaises(BuildError):
            build_from_table(self._table(), {"NOPE": "x"})


class TestFirstKey(unittest.TestCase):
    def test_invalid_is_not_magically_invented(self):
        # build_container writes exactly the keys it is given; it does not
        # add INVALID behind the caller's back.
        _, t = parse(build_container(["A", "B"]))
        self.assertNotIn(FIRST_KEY, t.keys)
        self.assertEqual(len(t.keys), 2)

    def test_invalid_is_always_first_when_present(self):
        _, t = parse(build_container(["A", "INVALID", "B"]))
        self.assertEqual(t.keys[0], "INVALID")


# ------------------------------------------------------- guessed-slot guard

KEYS = ["UI_WEAPONS", "MENU_QUIT", "UI_GREETING"]
VALUES = ["WEAPONS", "Quit", "Hello!"]


class TestGuessedSlotGuard(unittest.TestCase):
    """`dump -l en-US` must not answer with a blank file.

    With a mod installed the toc stops saying which container is which
    language, so the ids come back recovered by elimination.  That is fine
    while every container has text, and actively dangerous when the one you
    asked for comes back empty: "empty" is exactly what the reader reports if
    the guess picked the wrong container, and the natural reading of that is
    "untranslated".  So `dump` used to hand back 25,034 blank rows and say
    "fill it in" -- and filling it in would have replaced English with a copy
    of English.
    """

    def table(self, values, verified=False, language_id=0):
        t = parse_container(Dat(build_container(KEYS, values=values), 0))
        t.language_id = language_id
        t.language_verified = verified
        return t

    def guard(self, tables, target, token="en-US"):
        from rcextract.cli import _refuse_if_guessed_slot
        from rcextract.languages import resolve
        return _refuse_if_guessed_slot(None, tables, resolve(token), target)

    def test_an_empty_guessed_slot_is_refused(self):
        full = self.table(dict(zip(KEYS, VALUES)))
        empty = self.table({})
        with self.assertRaises(SystemExit) as cm:
            self.guard([full, empty], empty)
        self.assertEqual(cm.exception.code, 1)

    def test_the_message_names_the_slot_that_has_the_text(self):
        full = self.table(dict(zip(KEYS, VALUES)), language_id=23)
        empty = self.table({})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                self.guard([full, empty], empty)
        msg = err.getvalue()
        # The user should not have to go and find it themselves.
        self.assertIn("-l 23", msg)
        self.assertIn(str(len(KEYS)), msg)
        self.assertIn("wrong container", msg)

    def test_an_empty_slot_with_a_confirmed_id_is_fine(self):
        # A retail build has four genuinely empty slots, and handing back a
        # fillable template for one of those is the whole point of `dump`.
        # Nothing about a confirmed id should be refused.
        full = self.table(dict(zip(KEYS, VALUES)), verified=True)
        empty = self.table({}, verified=True, language_id=23)
        self.guard([full, empty], empty, token="23")

    def test_a_guessed_slot_with_text_is_fine(self):
        # Every slot is unverified once a mod is installed, and 26 of them
        # still have text.  Refusing those would break `dump -l de` for
        # everyone who installed a mod, which is most of the point of a mod.
        full = self.table(dict(zip(KEYS, VALUES)), verified=False)
        self.guard([full], full)


if __name__ == "__main__":
    unittest.main()
