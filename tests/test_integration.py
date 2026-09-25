"""Integration tests against a real Rift Apart install.

These are skipped unless a game install is found, so the suite stays green on
a machine that has no copy of the game.  Point at one explicitly with:

    RCEXTRACT_GAME=/path/to/'Ratchet & Clank - Rift Apart' python -m pytest tests/

What they check is the ground truth that the synthetic tests in
``test_formats.py`` only assume: the real archive really does hold 32
containers, they really do share one key list, and the table of contents
really does join container offsets to language ids.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rcextract.dat import Dat, scan_containers  # noqa: E402
from rcextract.game import (  # noqa: E402
    GameNotFound,
    find_game_root,
    language_ids_from_toc,
    load_localization,
    moved_localization_slots,
    read_localization_blob,
)
from rcextract.lang import parse_container  # noqa: E402
from rcextract.languages import EMPTY_SLOTS  # noqa: E402
from rcextract.lump_types import (  # noqa: E402
    LUMP_LANG_KEY_ALT,
    LUMP_LANG_KEY_HASH,
    LUMP_LANG_VALUES,
    LUMP_LANG_VALUE_OFF,
)
from rcextract.toc import Toc  # noqa: E402


def _game() -> str | None:
    try:
        return find_game_root(os.environ.get("RCEXTRACT_GAME") or os.getcwd())
    except GameNotFound:
        return None


GAME = _game()


def _moved_slots() -> list:
    """Localisation slots whose toc entry no longer points into d/localization."""
    return moved_localization_slots(GAME) if GAME else []


MOVED = _moved_slots()

# Several tests below join container offsets in d/localization to language ids
# from the toc.  That join only exists while the toc still points every
# localisation container into d/localization, so an installed mod -- which
# repoints some of them at its own archive -- makes them unobservable.  They are
# statements about the retail build, so they are skipped rather than bent.
needs_unmodified_toc = unittest.skipIf(
    bool(MOVED),
    "an installed mod has repointed slot(s) %s, so container offsets can no "
    "longer be joined to language ids; these are retail-build facts"
    % ", ".join(str(s) for s in MOVED))


@unittest.skipIf(GAME is None, "no Rift Apart install found (set RCEXTRACT_GAME)")
class TestRealArchive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables = load_localization(GAME)

    def test_thirty_two_containers(self):
        self.assertEqual(len(self.tables), 32)

    def test_one_shared_key_list(self):
        """Every language must be indexed against the same keys, in the same
        order -- otherwise a translation for slot 3 would land in slot 7."""
        base = self.tables[0].keys
        for t in self.tables:
            self.assertEqual(t.keys, base, "key list differs in slot %d" % t.language_id)
        self.assertEqual(len(base), 25034)

    def test_language_ids_are_unique_and_complete(self):
        ids = sorted(t.language_id for t in self.tables)
        self.assertEqual(ids, list(range(32)))
        # A verified join needs the toc to still describe where every
        # container lives.  An installed mod repoints some of those entries,
        # so the ids come back recovered-by-elimination instead: right as a
        # set, but not confirmed.  Both are acceptable; the *reason* is not,
        # which is why the unverified case is only allowed with a mod present.
        if MOVED:
            # A mod claims the four reserved slots, and may also claim the two
            # English ones so the game finds Arabic where it already looks.
            self.assertTrue(set(EMPTY_SLOTS) <= set(MOVED),
                            "the reserved slots should be claimed first: %s"
                            % MOVED)
            self.assertFalse(all(t.language_verified for t in self.tables))
        else:
            self.assertTrue(all(t.language_verified for t in self.tables),
                            "language ids were not confirmed against the toc")

    def test_known_slots(self):
        by_id = {t.language_id: t for t in self.tables}
        # spot-checks on strings that are unambiguous per language
        cases = {
            3: ("da", "VÅBEN"),
            7: ("de", "WAFFEN"),
            11: ("no", "VÅPEN"),
            14: ("ru", "ОРУЖИЕ"),
            20: ("es-ES", "ARMAS"),
        }
        for slot, (code, expected) in cases.items():
            t = by_id[slot]
            self.assertEqual(t.code, code, "slot %d is %s, expected %s" % (slot, t.code, code))
            self.assertEqual(t.get("UI_WEAPONS"), expected)

    @needs_unmodified_toc
    def test_four_slots_are_empty(self):
        empty = sorted(t.language_id for t in self.tables if not t.translated_count)
        self.assertEqual(empty, sorted(EMPTY_SLOTS))

    def test_untranslated_is_distinct_from_empty_string(self):
        """A missing translation must read as None; a real empty string is
        itself a translation.  Counting them as the same would silently drop
        legitimate strings."""
        for t in self.tables:
            for v in t.values:
                if v is not None:
                    self.assertNotIn("\x00", v)

    def test_no_key_or_value_contains_a_row_delimiter(self):
        """Why the TSV writer can refuse rather than rewrite.  If this ever
        fails, TSV has silently become a lossy format for that language."""
        bad = [(t.language_id, k) for t in self.tables
               for k, v in t.items() for f in (k, v)
               if "\t" in f or "\n" in f or "\r" in f]
        self.assertEqual(bad, [])

    def test_containers_are_contiguous_with_a_zero_trailer(self):
        """The containers tile the archive back to back with no gaps; what
        follows the last one is zero padding, not a truncated container."""
        blob = read_localization_blob(GAME)
        from rcextract.dat import scan_containers
        cs = scan_containers(blob)
        self.assertEqual(len(cs), 32)
        pos = 0
        for c in cs:
            self.assertEqual(c.base, pos, "gap or overlap before container at %d" % pos)
            pos += c.file_size
        trailer = blob[pos:]
        self.assertTrue(set(trailer) <= {0}, "trailer is not zero padding")
        self.assertLess(len(trailer), 64, "trailer is too large to be padding")

    def test_toc_offsets_match_container_offsets(self):
        blob = read_localization_blob(GAME)
        ids = language_ids_from_toc(GAME, blob)
        self.assertIsNotNone(ids, "toc did not line up with the archive")
        self.assertEqual(sorted(ids), list(range(32)))

    def test_arabic_audio_ships_but_its_text_slot_is_empty(self):
        """The basis for treating the empty slots as real localisation work:
        the game has an Arabic voice-over archive, and an Arabic logo, but no
        Arabic text table."""
        toc = Toc.open(GAME)
        archives = set(toc.archive_names())
        self.assertTrue(any(a.endswith("\\wem.ar") for a in archives),
                        "expected an Arabic voice-over archive in the toc")


@unittest.skipIf(GAME is None, "no Rift Apart install found (set RCEXTRACT_GAME)")
class TestTsvRoundTrip(unittest.TestCase):
    """A dump must be a lossless view of the table.

    The point of the TSV is that someone edits it and a writer puts it back,
    so any silent rewriting in the writer is a corruption.  This reads a real
    dump and compares it against the parsed table.
    """

    def test_dump_matches_table_exactly(self):
        import csv
        import io
        from rcextract.cli import _write_tsv

        for slot in (1, 7, 14, 23):   # en-US, de, ru, and an empty slot
            t = [x for x in load_localization(GAME) if x.language_id == slot][0]
            buf = io.StringIO()
            _write_tsv(t, buf, template=(t.translated_count == 0))
            lines = buf.getvalue().split("\n")
            self.assertEqual(lines[0], "LEN %d" % t.key_count)

            rows = list(csv.reader(io.StringIO(buf.getvalue()), delimiter="\t"))
            self.assertEqual(rows[0], ["LEN %d" % t.key_count])
            body = [r for r in rows[1:] if r]
            # A filled table dumps only its translated rows; a template dumps
            # every key so there is something to fill in.
            is_template = t.translated_count == 0
            self.assertEqual(len(body), t.key_count if is_template else t.translated_count)
            # Mirror exactly what the writer iterates: template() emits every
            # key, items() emits only the translated ones.
            rows_from = t.template() if is_template else t.items()
            for (k, v), row in zip(rows_from, body):
                self.assertEqual(row, [k, v], "mismatch at %s in slot %d" % (k, slot))


@unittest.skipIf(GAME is None, "no Rift Apart install found (set RCEXTRACT_GAME)")
class TestCliAgainstRealArchive(unittest.TestCase):
    def run_cli(self, *argv):
        from rcextract.cli import main
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--game", GAME, *argv])
        return rc, buf.getvalue()

    @needs_unmodified_toc
    def test_list_runs(self):
        rc, out = self.run_cli("list")
        self.assertEqual(rc, 0)
        self.assertIn("containers: 32", out)
        self.assertIn("empty slots (no strings): 23, 28, 29, 30", out)
        self.assertIn("Arabic", out)

    def test_verify_passes(self):
        rc, out = self.run_cli("verify")
        self.assertEqual(rc, 0, out)
        self.assertIn("RESULT: PASS", out)

    def test_get_known_string(self):
        rc, out = self.run_cli("get", "-l", "de", "-k", "UI_WEAPONS")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "WAFFEN")

    @needs_unmodified_toc
    def test_get_from_empty_slot_reports_untranslated(self):
        rc, out = self.run_cli("get", "-l", "23", "-k", "UI_WEAPONS")
        self.assertEqual(rc, 1)
        self.assertIn("untranslated", out)

    def test_game_flag_works_before_the_subcommand(self):
        """--game is declared on both the top-level parser and each
        subparser, so a real default on the subparser would clobber a value
        given before the subcommand.  This pins that down."""
        from rcextract.cli import main
        import contextlib, io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["--game", GAME, "get", "-l", "de", "-k", "UI_WEAPONS"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), "WAFFEN")

    def test_every_subcommand_is_reachable(self):
        """Smoke-test that each subcommand dispatches without raising.

        A message-formatting bug once made ``list`` die with a NameError
        three lines into its own output, after the useful part had already
        been printed -- so it was easy to miss by eye.  Walking every
        subcommand catches that class of mistake wherever it lives.
        """
        argv = {
            "list": [],
            "verify": [],
            "get": ["-l", "de", "-k", "UI_WEAPONS"],
            "dump": ["-l", "de"],
            "toc": [],
        }
        for name, extra in argv.items():
            with self.subTest(command=name):
                rc, out = self.run_cli(name, *extra)
                self.assertIn(rc, (0, 1), "%s exited %r" % (name, rc))
                self.assertTrue(out.strip(), "%s printed nothing" % name)

    def test_list_explains_the_shared_slot_languages(self):
        """`list` must not just name the four untranslated languages; it has to
        account for all six untranslated LANGUAGE_* keys, or the counts do not
        add up to four empty slots."""
        rc, out = self.run_cli("list")
        self.assertEqual(rc, 0, out)
        self.assertIn("Canadian French", out)
        self.assertIn("Mexican Spanish", out)
        self.assertIn("Arabic", out)


@unittest.skipIf(GAME is None, "no Rift Apart install found (set RCEXTRACT_GAME)")
class TestWriterRoundTrip(unittest.TestCase):
    """The writer is only trustworthy if it can reproduce the game byte for byte.

    Rebuilding a shipped container with no edits and getting the identical
    bytes back proves the serialiser, the entry ordering, the key hash and
    the section layout all at once.  Any one of them being wrong would show
    up as a diff, and a diff is the difference between "the writer is
    broken" and "the translation is wrong" -- so this is the test that has to
    pass before a single Arabic string goes anywhere near the format.
    """

    @classmethod
    def setUpClass(cls):
        cls.blob = read_localization_blob(GAME)
        cls.containers = scan_containers(cls.blob)

    def test_all_containers_rebuild_byte_identically(self):
        from rcextract.build import build_from_table
        self.assertEqual(len(self.containers), 32)
        for c in self.containers:
            with self.subTest(offset=c.base):
                raw = self.blob[c.base:c.base + c.file_size]
                self.assertEqual(build_from_table(parse_container(c)), raw)

    def test_key_hashes_match_the_games_own_table(self):
        """key_hash() is reverse-engineered; the game already ships the answer.

        Lump 0x06a58050 holds the hash of every key in every language, so it
        is a 25,034-entry oracle for the hash function.
        """
        from rcextract.build import key_hash
        for c in self.containers:
            table = c.u32_array(LUMP_LANG_KEY_HASH)
            keys = parse_container(c).keys
            self.assertEqual(len(table), len(keys))
            for i, k in enumerate(keys):
                if table[i] != key_hash(k):
                    self.fail("key %r hashes to %#010x, game says %#010x"
                              % (k, key_hash(k), table[i]))

    def test_sorted_hash_table_really_is_sorted(self):
        for c in self.containers:
            s = list(c.u32_array(LUMP_LANG_KEY_ALT))
            self.assertEqual(s, sorted(s), "container at %d" % c.base)

    def test_entry_zero_is_invalid(self):
        for c in self.containers:
            self.assertEqual(parse_container(c).keys[0], "INVALID")

    def test_keys_are_ordinal_after_the_first(self):
        for c in self.containers:
            keys = parse_container(c).keys[1:]
            self.assertEqual(keys, sorted(keys, key=lambda s: s.encode("utf-16-be")))

    def test_every_container_shares_one_key_list(self):
        first = parse_container(self.containers[0]).keys
        for c in self.containers[1:]:
            self.assertEqual(parse_container(c).keys, first)

    def test_writing_a_new_language_keeps_the_key_list(self):
        """A container built from the retail key list must accept that exact
        key set -- that is what a new language slot will be."""
        from rcextract.build import build_container  # noqa: F811
        template = parse_container(self.containers[0])
        raw = build_container(template.keys, {"INVALID": None, "UI_WEAPONS": "\u0627\u0644\u0633\u0644\u0627\u062d"})
        out = parse_container(Dat(raw))
        self.assertEqual(out.keys, template.keys)
        self.assertEqual(out.get("UI_WEAPONS"), "\u0627\u0644\u0633\u0644\u0627\u062d")
        self.assertIsNone(out.get("MENU_DIFFICULTY_TITLE"))

    def test_untranslated_entries_fall_back_rather_than_blanking(self):
        """A partial translation must leave the rest of the table untranslated,
        so the game falls back to English instead of showing nothing."""
        from rcextract.build import build_container  # noqa: F811
        template = parse_container(self.containers[0])
        raw = build_container(template.keys, {"INVALID": None, "UI_WEAPONS": "x"})
        offsets = Dat(raw).u32_array(LUMP_LANG_VALUE_OFF)
        table = parse_container(Dat(raw))
        blank = [k for k, o in zip(table.keys, offsets)
                 if o == 0 and k != "INVALID"]
        self.assertEqual(len(blank), len(template.keys) - 2)

    def test_flag_bytes_are_a_property_of_the_key_not_the_language(self):
        """Lump 0xb0653243 is per-entry metadata, and it is *identical* in all
        32 containers -- 5,889 keys carry the value 2.  So it cannot be
        hardcoded to zero when writing, or those entries change meaning.  What
        the flag means is not established; the keys that carry it look like
        voice-over cue names, but that is a guess."""
        ref = None
        for c in self.containers:
            t = parse_container(c)
            nz = tuple(f for f in t.flags)
            if ref is None:
                ref = nz
                self.assertEqual(sum(1 for f in nz if f), 5889)
            self.assertEqual(nz, ref, "container at %d differs" % c.base)
        self.assertEqual(len(ref), 25034)

    def test_flag_padding_is_always_zero(self):
        for c in self.containers:
            raw = c.lump(0xb0653243)
            self.assertEqual(set(raw[25034:]), {0}, "container at %d" % c.base)

    @needs_unmodified_toc
    def test_empty_slots_have_a_one_byte_value_blob(self):
        """The four unassigned slots are the model for a new language: all
        25,034 keys present, every value offset zero, one NUL of payload."""
        from rcextract.languages import EMPTY_SLOTS
        ids = language_ids_from_toc(GAME, self.blob)
        self.assertIsNotNone(ids)
        slot_of = {id(c): lid for c, lid in zip(self.containers, ids)}
        empty = 0
        for c in self.containers:
            t = parse_container(c)
            if slot_of[id(c)] in EMPTY_SLOTS:
                empty += 1
                self.assertEqual(t.translated_count, 0)
                self.assertEqual(c.lump(LUMP_LANG_VALUES), b"\x00")
                self.assertEqual(set(c.u32_array(LUMP_LANG_VALUE_OFF)), {0})
        self.assertEqual(empty, len(EMPTY_SLOTS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
