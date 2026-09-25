"""Tests for :mod:`rcextract.mod`, the mod installer.

These run against a real ``toc`` when one is available, because the whole
point of this module is to rewrite a 12 MB table of contents in place without
the game noticing.  A synthetic table would be testing a fiction: the
interesting failure modes -- the trailing slack, the tag-sorted directory, the
slack after the directory that is a label and not padding -- are all
properties of the shipped file.

    RCEXTRACT_GAME=/path/to/'Ratchet & Clank - Rift Apart' python -m unittest tests.test_mod
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rcextract.build import build_container  # noqa: E402
from rcextract.dat import Dat, DatError  # noqa: E402
from rcextract.game import GameNotFound, find_game_root  # noqa: E402
from rcextract.lang import parse_container  # noqa: E402
from rcextract.languages import EMPTY_SLOTS  # noqa: E402
from rcextract.mod import (  # noqa: E402
    ARCHIVE_RECORD,
    ASSET_META,
    LUMP_TOC_ARCHIVES,
    LUMP_TOC_ASSET_META,
    MOD_ARCHIVE_DESCRIPTOR,
    MOD_ARCHIVE_NAME,
    RETAIL_SHARED_DESCRIPTOR,
    TOC_MAGIC,
    ModError,
    TocImage,
    build_archive_record,
    build_mod,
    default_targets,
    install,
    parse_archive_record,
    uninstall,
)


def _game() -> str | None:
    try:
        return find_game_root(os.environ.get("RCEXTRACT_GAME") or os.getcwd())
    except GameNotFound:
        return None


GAME = _game()
needs_game = unittest.skipIf(GAME is None, "no Rift Apart install found")

ARABIC = {
    "INVALID": None,
    "UI_WEAPONS": "الأسلحة",
    "MENU_DIFFICULTY_TITLE": "الصعوبة",
    "AUDIOLANGUAGE_TITLE": "لغة الصوت",
}
KEYS = ["INVALID", "UI_WEAPONS", "MENU_DIFFICULTY_TITLE", "AUDIOLANGUAGE_TITLE"]


def payload() -> bytes:
    return build_container(KEYS, ARABIC)


class ArchiveRecord(unittest.TestCase):
    def test_record_is_66_bytes(self):
        self.assertEqual(ARCHIVE_RECORD.size, 66)
        self.assertEqual(len(build_archive_record("d\\mods\\mod1")), 66)

    def test_round_trip(self):
        raw = build_archive_record("d\\mods\\mod1")
        rec = parse_archive_record(raw)
        self.assertEqual(rec["name"], "d\\mods\\mod1")
        self.assertEqual((rec["a"], rec["b"], rec["c"], rec["d"], rec["e"]),
                         MOD_ARCHIVE_DESCRIPTOR)

    def test_name_is_nul_padded(self):
        raw = build_archive_record("d\\config")
        self.assertEqual(raw[:8], b"d\\config")
        self.assertEqual(raw[8:40], b"\x00" * 32)
        self.assertEqual(len(raw), 66)

    def test_short_name_leaves_the_rest_nul(self):
        raw = build_archive_record("a")
        self.assertEqual(parse_archive_record(raw)["name"], "a")
        self.assertEqual(raw[1:40], b"\x00" * 39)

    def test_overlong_name_rejected(self):
        with self.assertRaises(ModError):
            build_archive_record("d\\" + "x" * 40)

    def test_descriptor_is_not_retails(self):
        """Worth pinning: the descriptor must differ from the one the 147
        shipped archives carry, or this module would be writing LZ4 archives
        as if they were flat."""
        self.assertNotEqual(MOD_ARCHIVE_DESCRIPTOR[:4], RETAIL_SHARED_DESCRIPTOR)

    def test_load_order_bucket_is_the_default(self):
        self.assertEqual(MOD_ARCHIVE_DESCRIPTOR[4], 0)


class BadToc(unittest.TestCase):
    def _toc(self, body: bytes) -> str:
        d = tempfile.mkdtemp(prefix="rcra-badtoc-")
        self.addCleanup(shutil.rmtree, d)
        p = os.path.join(d, "toc")
        with open(p, "wb") as fh:
            fh.write(body)
        return p

    def test_missing_file(self):
        with self.assertRaises((ModError, OSError)):
            TocImage.load("/nonexistent/toc")

    def test_bad_magic(self):
        p = self._toc(pack_ii(0, 8) + b"1TAD" + b"\x00" * 8)
        with self.assertRaises(ModError):
            TocImage.load(p)

    def test_no_container_after_header(self):
        p = self._toc(pack_ii(TOC_MAGIC, 8) + b"NOPE" + b"\x00" * 8)
        with self.assertRaises(ModError):
            TocImage.load(p)

    def test_too_small(self):
        p = self._toc(pack_ii(TOC_MAGIC, 4))
        with self.assertRaises(ModError):
            TocImage.load(p)

    def test_header_length_disagrees_with_container(self):
        p = self._toc(pack_ii(TOC_MAGIC, 999) + b"1TAD" + b"\x00" * 8)
        with self.assertRaises((ModError, DatError)):
            TocImage.load(p)

    def test_nonzero_data_after_the_container_is_refused(self):
        """The shipped toc has zero slack past its declared length.  Non-zero
        bytes there would mean something is being carried that this module
        would silently drop on rewrite."""
        p = self._toc(pack_ii(TOC_MAGIC, 8) + b"1TAD" + b"\x00" * 8 + b"junk")
        with self.assertRaises((ModError, DatError)):
            TocImage.load(p)


def pack_ii(a, b):
    import struct
    return struct.pack("<II", a, b)


class InstallCycle(unittest.TestCase):
    """The whole point: a real toc, edited, and put back exactly as it was."""

    def setUp(self):
        if GAME is None:
            self.skipTest("no Rift Apart install found")
        self.sandbox = tempfile.mkdtemp(prefix="rcra-mod-")
        self.addCleanup(shutil.rmtree, self.sandbox, True)
        self.toc = os.path.join(self.sandbox, "toc")
        shutil.copy2(os.path.join(GAME, "toc"), self.toc)
        with open(self.toc, "rb") as fh:
            self.orig = fh.read()
        self.image = TocImage.load(self.toc)
        self.loc = self.image.find_localization_assets()

    def _mod(self, **kw):
        mod = build_mod(self.image, {s: payload() for s in default_targets()}, **kw)
        return mod

    def test_toc_round_trips_byte_identically(self):
        """The precondition for everything else.  If editing the toc changed
        a byte we did not mean to, no amount of care afterwards helps."""
        self.assertEqual(TocImage.load(self.toc).to_bytes(), self.orig)

    def test_default_targets_are_the_empty_slots(self):
        self.assertEqual(set(default_targets()), set(EMPTY_SLOTS))
        self.assertTrue(all(s in self.loc for s in default_targets()))

    def test_all_32_localization_assets_are_found(self):
        self.assertEqual(len(self.loc), 32)
        self.assertEqual(sorted(self.loc), list(range(32)))

    def test_install_repoints_only_the_targets(self):
        before = TocImage.load(os.path.join(GAME, "toc"))
        install(self.sandbox, self._mod())
        after = TocImage.load(self.toc)

        changed = [i for i in range(before.asset_count)
                   if before.asset_meta(i) != after.asset_meta(i)]
        self.assertEqual(sorted(changed),
                         sorted(self.loc[s] for s in default_targets()))

        changed_lumps = [c for c in _lump_crcs(before) if before.lump(c) != after.lump(c)]
        self.assertEqual(sorted(changed_lumps),
                         sorted([LUMP_TOC_ARCHIVES, LUMP_TOC_ASSET_META]))

    def test_installed_archive_reads_back_as_the_payload(self):
        info = install(self.sandbox, self._mod())
        with open(info["archive"], "rb") as fh:
            blob = fh.read()
        after = TocImage.load(self.toc)
        for slot in default_targets():
            i = self.loc[slot]
            size, archive, offset, _ = after.asset_meta(i)
            self.assertEqual(archive, info["archive_index"])
            table = parse_container(Dat(blob[offset:offset + size]))
            self.assertEqual(table.get("UI_WEAPONS"), "الأسلحة")
            self.assertEqual(table.get("MENU_DIFFICULTY_TITLE"), "الصعوبة")
            self.assertIsNone(table.get("BOOT_TRAILER"))

    def test_archive_is_a_flat_concatenation(self):
        """No DSAR wrapper, no block directory.  The toc's (offset, size)
        must address the payload directly."""
        info = install(self.sandbox, self._mod())
        with open(info["archive"], "rb") as fh:
            blob = fh.read()
        after = TocImage.load(self.toc)
        n = len(default_targets())
        self.assertEqual(len(blob), n * len(payload()))
        for k, slot in enumerate(default_targets()):
            i = self.loc[slot]
            size, _archive, offset, _ = after.asset_meta(i)
            self.assertEqual(offset, k * len(payload()))
            self.assertEqual(blob[offset:offset + size], payload())

    def test_install_records_the_new_archive(self):
        info = install(self.sandbox, self._mod())
        after = TocImage.load(self.toc)
        self.assertEqual(len(after.archives), len(self.image.archives) + 1)
        self.assertEqual(after.archives[-1]["name"], MOD_ARCHIVE_NAME)
        self.assertEqual(info["archive_index"], len(after.archives) - 1)
        self.assertIsNone(self.image.archive_index(MOD_ARCHIVE_NAME))

    def test_install_leaves_d_localization_alone(self):
        before = os.path.join(GAME, "d", "localization")
        after = os.path.join(self.sandbox, "d", "localization")
        self.assertFalse(os.path.exists(after),
                         "the installer must not create d/localization")
        self.assertTrue(os.path.isfile(before))

    def test_install_backs_up_the_toc(self):
        info = install(self.sandbox, self._mod())
        self.assertTrue(os.path.isfile(info["backup"]))
        with open(info["backup"], "rb") as fh:
            self.assertEqual(fh.read(), self.orig)

    def test_installed_toc_reloads_and_grew_by_exactly_one_record(self):
        install(self.sandbox, self._mod())
        after = TocImage.load(self.toc)
        self.assertEqual(len(after.lump(LUMP_TOC_ARCHIVES)),
                         len(self.image.lump(LUMP_TOC_ARCHIVES)) + 66)
        self.assertEqual(after.asset_count, self.image.asset_count)
        self.assertEqual(after.span_count, self.image.span_count)

    def test_trailing_slack_is_preserved(self):
        """The shipped toc has 284 bytes past the length its header declares.
        Nothing points there, but a rewrite that drops them makes the file
        smaller, and there is no reason for it to."""
        self.assertTrue(self.image.trailer)
        install(self.sandbox, self._mod())
        self.assertEqual(TocImage.load(self.toc).trailer, self.image.trailer)

    def test_uninstall_restores_the_toc_byte_for_byte(self):
        info = install(self.sandbox, self._mod())
        res = uninstall(self.sandbox)
        self.assertTrue(res["archive_removed"])
        self.assertFalse(os.path.exists(info["archive"]))
        with open(self.toc, "rb") as fh:
            self.assertEqual(fh.read(), self.orig)
        self.assertEqual(TocImage.load(self.toc).archives, self.image.archives)

    def test_uninstall_without_a_backup_refuses(self):
        with self.assertRaises(ModError):
            uninstall(self.sandbox)

    def test_installing_twice_refuses(self):
        install(self.sandbox, self._mod())
        with self.assertRaises(ModError):
            install(self.sandbox, self._mod())

    def test_single_slot_is_allowed(self):
        mod = build_mod(self.image, {23: payload()}, slots=[23])
        info = install(self.sandbox, mod)
        after = TocImage.load(self.toc)
        changed = [i for i in range(self.image.asset_count)
                   if self.image.asset_meta(i) != after.asset_meta(i)]
        self.assertEqual(changed, [self.loc[23]])
        self.assertEqual(len(info["assets"]), 1)

    def test_missing_payload_rejected(self):
        with self.assertRaises(ModError):
            build_mod(self.image, {23: payload()}, slots=[23, 28])

    def test_unassigned_slot_is_rejected(self):
        """Slot 31 is a shipped language; there is no asset for slot 32, and
        inventing one would mean the mod silently did nothing."""
        self.assertNotIn(32, self.loc)
        with self.assertRaises(ModError):
            build_mod(self.image, {32: payload()}, slots=[32])


def _lump_crcs(image: TocImage):
    return [l.type_crc for l in image.dat.lumps]


class Structure(unittest.TestCase):
    """Facts about the shipped toc that this module relies on."""

    @classmethod
    def setUpClass(cls):
        if GAME is None:
            raise unittest.SkipTest("no Rift Apart install found")
        cls.img = TocImage.load(os.path.join(GAME, "toc"))

    def test_counts(self):
        self.assertEqual(self.img.asset_count, len(self.img.lump(0x506D7B8A)) // 8)
        self.assertEqual(len(self.img.archives) * 66, len(self.img.lump(LUMP_TOC_ARCHIVES)))

    def test_asset_meta_row_is_16_bytes(self):
        self.assertEqual(ASSET_META.size, 16)

    def test_retail_archives_share_a_descriptor(self):
        """A, B, C and D are identical across all 147 shipped archives, so they
        are not per-archive data.  If they varied, this module's hardcoded
        descriptor would be wrong for a reason worth knowing about."""
        seen = {(r["a"], r["b"], r["c"], r["d"]) for r in self.img.archives}
        self.assertEqual(seen, {RETAIL_SHARED_DESCRIPTOR})

    def test_our_descriptor_is_not_the_retail_one(self):
        self.assertNotIn(MOD_ARCHIVE_DESCRIPTOR[:4],
                         {(r["a"], r["b"], r["c"], r["d"]) for r in self.img.archives})

    def test_load_order_bucket_is_zero_except_for_audio(self):
        """Field E is a load-order bucket in units of 0x01000000.  It is 0 for
        113 of the 147 retail archives, and non-zero only for the
        ``d\\wem.<lang>`` / ``d\\soundbank.<lang>`` pairs -- which is also how
        the shipped Arabic audio turns up in the table of contents."""
        by_name = {r["name"]: r for r in self.img.archives}
        buckets = {n: r["e"] for n, r in by_name.items() if r["e"]}
        self.assertEqual(len(buckets), 34)
        for name, e in buckets.items():
            base = name.split(".")[-1]
            self.assertEqual(by_name["d\\wem.%s" % base]["e"], e, name)
            self.assertEqual(by_name["d\\soundbank.%s" % base]["e"], e, name)
        self.assertEqual(buckets["d\\wem.ar"], by_name["d\\soundbank.ar"]["e"])
        # everything else is the default bucket
        self.assertTrue(all(r["e"] == 0 for r in self.img.archives if not r["e"]))
        self.assertEqual(MOD_ARCHIVE_DESCRIPTOR[4], 0)

    def test_spans_cover_every_asset(self):
        total = sum(struct_unpack_span(self.img.lump(0xEDE8ADA9), g)
                    for g in range(self.img.span_count))
        self.assertEqual(total, self.img.asset_count)

    def test_group_lookup_agrees_with_the_spans_table(self):
        raw = self.img.lump(0xEDE8ADA9)
        # 203 of the 256 groups are empty, so this checks every non-empty one.
        for g in range(self.img.span_count):
            first, count = struct_unpack_from_ii(raw, g * 8)
            if not count:
                continue
            for idx in (first, first + count - 1):
                self.assertEqual(self.img.group_of(idx), g)

    def test_many_groups_are_empty(self):
        """Only 53 of 256 groups hold anything, and the localisation slots are
        singletons one per language -- which is why the language id has to be
        recovered from the group number rather than from an asset list."""
        raw = self.img.lump(0xEDE8ADA9)
        empty = [g for g in range(self.img.span_count)
                 if struct_unpack_from_ii(raw, g * 8)[1] == 0]
        self.assertEqual(len(empty), 203)
        for slot in (23, 28, 29, 30):
            g = slot * 8
            first, count = struct_unpack_from_ii(raw, g * 8)
            self.assertEqual(count, 1, "group %d" % g)
            self.assertEqual(self.img.group_of(first), g)

    def test_assets_are_ordered_by_group(self):
        """Groups are contiguous, which is what makes the span table work."""
        raw = self.img.lump(0xEDE8ADA9)
        for g in range(1, self.img.span_count):
            f0, c0 = struct_unpack_from_ii(raw, (g - 1) * 8)
            f1, _c1 = struct_unpack_from_ii(raw, g * 8)
            self.assertEqual(f0 + c0, f1, "span %d is not contiguous" % g)


def struct_unpack_span(raw, g):
    import struct
    return struct.unpack_from("<II", raw, g * 8)[1]


def struct_unpack_from_ii(raw, off):
    import struct
    return struct.unpack_from("<II", raw, off)


if __name__ == "__main__":
    unittest.main(verbosity=2)
