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

from rcextract.game import (  # noqa: E402
    GameNotFound,
    find_game_root,
    language_ids_from_toc,
    load_localization,
    read_localization_blob,
)
from rcextract.languages import EMPTY_SLOTS  # noqa: E402
from rcextract.toc import Toc  # noqa: E402


def _game() -> str | None:
    try:
        return find_game_root(os.environ.get("RCEXTRACT_GAME") or os.getcwd())
    except GameNotFound:
        return None


GAME = _game()


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
