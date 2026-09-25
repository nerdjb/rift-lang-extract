"""Synthetic round-trip tests.

These build a localisation container in memory from a known key/value list and
check that the readers recover it exactly.  They need no game install and no
third-party packages, so they run anywhere.

Run with::

    python -m pytest tests/
    python tests/test_formats.py      # no pytest needed
"""

from __future__ import annotations

import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rcextract.dat import Dat, DatError, scan_containers
from rcextract.lang import LocalizationError, load_all, parse_container
from rcextract.languages import LANGUAGES, resolve
from rcextract.lump_types import (
    LUMP_LANG_COUNT,
    LUMP_LANG_KEYS,
    LUMP_LANG_KEY_OFF,
    LUMP_LANG_VALUES,
    LUMP_LANG_VALUE_OFF,
)
from rcextract.toc import LOCALIZATION_PATH_HASH, Toc, TocError

MAGIC = b"1TAD"


# --------------------------------------------------------------------- builder
def build_container(pairs, untranslated=()):
    """Build a synthetic localisation ``1TAD`` container.

    `pairs` is an ordered list of (key, value).  Keys listed in
    `untranslated` (or whose value is None) get a zero value offset, exactly
    as an unshipped language slot does.

    The value blob gets the leading NUL sentinel the real containers have, so
    offset 0 can mean "untranslated" rather than "the first string".
    """
    keys = [k.encode() for k, _ in pairs]
    key_blob = b"".join(k + b"\x00" for k in keys)

    key_off, val_off = [], []
    val_blob = bytearray(b"\x00")  # sentinel at offset 0
    ko = 0
    for kb in keys:
        key_off.append(ko)
        ko += len(kb) + 1
    for k, v in pairs:
        if k in untranslated or v is None:
            val_off.append(0)
            continue
        vb = v.encode()
        val_off.append(len(val_blob))
        val_blob += vb + b"\x00"

    n = len(pairs)
    lumps = [
        (LUMP_LANG_COUNT, struct.pack("<I", n)),
        (LUMP_LANG_KEYS, key_blob),
        (LUMP_LANG_VALUES, bytes(val_blob)),
        (LUMP_LANG_KEY_OFF, struct.pack("<%dI" % n, *key_off)),
        (LUMP_LANG_VALUE_OFF, struct.pack("<%dI" % n, *val_off)),
    ]

    header_size = 0x10 + 12 * len(lumps)
    # 4-byte align each payload, as the real containers do.
    offsets, cur = [], header_size
    for _, payload in lumps:
        cur = (cur + 3) & ~3
        offsets.append(cur)
        cur += len(payload)
    total = (cur + 3) & ~3

    out = bytearray(total)
    struct.pack_into("<4sIIHH", out, 0, MAGIC, 0x4D73CEBD, total, len(lumps), 0)
    for i, (crc, payload) in enumerate(lumps):
        struct.pack_into("<III", out, 0x10 + 12 * i, crc, offsets[i], len(payload))
        out[offsets[i]:offsets[i] + len(payload)] = payload
    return bytes(out)


SAMPLE = [
    ("UI_WEAPONS", "WEAPONS"),
    ("MENU_QUIT", "Quit"),
    ("UI_EMPTY_LABEL", "TBW"),
    ("UI_PERCENT", "%d%%"),
    ("UI_GREETING", "Hello, <span class='emphasis'>Ratchet</span>!"),
    ("UI_MULTILINE", "line one<br>line two"),
    ("UI_QUOTED", "He said &quot;hi&quot;"),
    ("UI_UNICODE", "日本語 / Ελληνικά / العربية"),
]

SAMPLE_KEYS_UI = [k for k, _ in SAMPLE if k.startswith("UI_")]


# ------------------------------------------------------------------ dat tests
class TestDat(unittest.TestCase):
    def test_parses_header_and_lumps(self):
        buf = build_container(SAMPLE)
        d = Dat(buf, 0)
        self.assertEqual(d.lump_count, 5)
        self.assertEqual(len(d), 5)
        self.assertEqual(d.u32(LUMP_LANG_COUNT), len(SAMPLE))

    def test_rejects_bad_magic(self):
        with self.assertRaises(DatError):
            Dat(b"NOPE" + b"\x00" * 64, 0)

    def test_rejects_truncated(self):
        buf = build_container(SAMPLE)
        with self.assertRaises(DatError):
            Dat(buf[:20], 0)

    def test_lump_access(self):
        d = Dat(build_container(SAMPLE), 0)
        self.assertIn(LUMP_LANG_KEYS, d)
        self.assertIsNone(d.get(0xDEADBEEF))
        self.assertTrue(d.get(LUMP_LANG_KEYS, b""))
        with self.assertRaises(DatError):
            d.lump(0xDEADBEEF)

    def test_offset_prefix(self):
        """A container behind a header must parse at a non-zero base."""
        buf = b"\x99" * 8 + build_container(SAMPLE)
        d = Dat(buf, 8)
        self.assertEqual(d.base, 8)
        self.assertEqual(d.u32(LUMP_LANG_COUNT), len(SAMPLE))


class TestScanContainers(unittest.TestCase):
    def test_walks_back_to_back_containers(self):
        a = build_container(SAMPLE)
        b = build_container([("X", "Y")], untranslated=["X"])
        blob = a + b
        found = scan_containers(blob)
        self.assertEqual(len(found), 2)
        self.assertEqual(found[0].base, 0)
        self.assertEqual(found[1].base, len(a))

    def test_stops_at_non_container(self):
        blob = build_container(SAMPLE) + b"garbage garbage garbage"
        self.assertEqual(len(scan_containers(blob)), 1)


# ------------------------------------------------------------------ lang tests
class TestStringTable(unittest.TestCase):
    def setUp(self):
        self.t = parse_container(Dat(build_container(SAMPLE), 0))
        self.t.language_id = 0

    def test_keys_and_values_round_trip(self):
        self.assertEqual(self.t.keys, [k for k, _ in SAMPLE])
        self.assertEqual([v for _, v in self.t.items()], [v for _, v in SAMPLE])

    def test_special_characters_survive(self):
        self.assertEqual(self.t["UI_PERCENT"], "%d%%")
        self.assertEqual(self.t["UI_GREETING"],
                         "Hello, <span class='emphasis'>Ratchet</span>!")
        self.assertEqual(self.t["UI_MULTILINE"], "line one<br>line two")
        self.assertEqual(self.t["UI_QUOTED"], "He said &quot;hi&quot;")
        self.assertEqual(self.t["UI_UNICODE"], "日本語 / Ελληνικά / العربية")

    def test_nul_terminator_not_included(self):
        for v in self.t.values:
            self.assertNotIn("\x00", v)

    def test_lookup(self):
        self.assertEqual(self.t.get("MENU_QUIT"), "Quit")
        self.assertIsNone(self.t.get("NOPE"))
        self.assertIn("MENU_QUIT", self.t)
        self.assertNotIn("NOPE", self.t)
        with self.assertRaises(KeyError):
            self.t["NOPE"]

    def test_untranslated_is_none_not_empty(self):
        """An unshipped slot must read as None, not as an empty string."""
        t = parse_container(Dat(build_container(SAMPLE, untranslated=["MENU_QUIT"]), 0))
        self.assertIsNone(t.get("MENU_QUIT"))
        self.assertEqual(t.translated_count, len(SAMPLE) - 1)
        # ...and the key must still be listed by template().
        self.assertIn("MENU_QUIT", dict(t.template()))

    def test_empty_language_has_all_keys(self):
        t = parse_container(Dat(build_container([(k, None) for k, _ in SAMPLE]), 0))
        self.assertEqual(t.translated_count, 0)
        self.assertEqual(len(list(t.template())), len(SAMPLE))
        self.assertEqual(t.key_count, len(SAMPLE))

    def test_keys_with_prefix(self):
        self.assertEqual(self.t.keys_with_prefix("MENU_"), ["MENU_QUIT"])
        self.assertEqual(self.t.keys_with_prefix("UI_"), SAMPLE_KEYS_UI)
        self.assertEqual(self.t.keys_with_prefix("NOPE"), [])

    def test_duplicate_keys_are_preserved(self):
        pairs = [("A", "1"), ("A", "2")]
        t = parse_container(Dat(build_container(pairs), 0))
        self.assertEqual(t.keys, ["A", "A"])
        self.assertEqual([v for _, v in t.items()], ["1", "2"])

    def test_rejects_non_language_container(self):
        with self.assertRaises(LocalizationError):
            parse_container(Dat(build_container([("A", "1")])[:0x10] + b"\x00" * 0x60, 0))


class TestLoadAll(unittest.TestCase):
    def test_language_ids_are_applied(self):
        blob = build_container(SAMPLE) + build_container(SAMPLE)
        tables = load_all(blob, [7, 9])
        self.assertEqual([t.language_id for t in tables], [7, 9])
        self.assertEqual([t.container_index for t in tables], [0, 1])

    def test_no_containers_raises(self):
        with self.assertRaises(LocalizationError):
            load_all(b"not a localization archive at all" * 4)


# -------------------------------------------------------------- languages tests
class TestLanguages(unittest.TestCase):
    def test_table_is_complete(self):
        self.assertEqual(len(LANGUAGES), 32)
        self.assertEqual([l.id for l in LANGUAGES], list(range(32)))

    def test_four_empty_slots(self):
        self.assertEqual([l.id for l in LANGUAGES if not l.shipped],
                         [23, 28, 29, 30])

    def test_resolve_by_id_code_and_name(self):
        self.assertEqual(resolve("7").code, "de")
        self.assertEqual(resolve("de").id, 7)
        self.assertEqual(resolve("DE").id, 7)
        self.assertEqual(resolve("German").id, 7)
        self.assertEqual(resolve("pt-br").id, 13)
        self.assertEqual(resolve("es-419").id, 15)

    def test_resolve_empty_slot(self):
        self.assertFalse(resolve("23").shipped)

    def test_resolve_unknown(self):
        with self.assertRaises(KeyError):
            resolve("klingon")


# ------------------------------------------------------------------ toc tests
def _build_toc(groups, assets, archive=b"d\\localization"):
    """Build a synthetic TOC container.

    `groups` is the (first_index, count) table; the *position* of a span in
    that list is the group id it assigns.  `assets` is [(path_hash, size, offset)].
    """
    import struct as _s
    group_blob = b"".join(_s.pack("<II", f, c) for f, c in groups)
    id_blob = b"".join(_s.pack("<Q", a[0]) for a in assets)
    meta_blob = b"".join(_s.pack("<IIII", a[1], 0, a[2], 0) for a in assets)
    arch_blob = archive.ljust(0x42, b"\x00")

    from rcextract.lump_types import (
        LUMP_TOC_ARCHIVE_ASSET_IDS, LUMP_TOC_ARCHIVE_ASSET_METADATA,
        LUMP_TOC_FILE_METADATA, LUMP_TOC_HEADER)
    lumps = [
        (LUMP_TOC_HEADER, group_blob),
        (LUMP_TOC_ARCHIVE_ASSET_IDS, id_blob),
        (LUMP_TOC_ARCHIVE_ASSET_METADATA, meta_blob),
        (LUMP_TOC_FILE_METADATA, arch_blob),
    ]
    header_size = 0x10 + 12 * len(lumps)
    offsets, cur = [], header_size
    for _, p in lumps:
        cur = (cur + 3) & ~3
        offsets.append(cur)
        cur += len(p)
    total = (cur + 3) & ~3
    out = bytearray(total)
    _s.pack_into("<4sIIHH", out, 0, MAGIC, 0x4D7CF320, total, len(lumps), 0)
    for i, (crc, p) in enumerate(lumps):
        _s.pack_into("<III", out, 0x10 + 12 * i, crc, offsets[i], len(p))
        out[offsets[i]:offsets[i] + len(p)] = p
    return bytes(out)


def _s_groups(group_ids):
    """Group table placing asset *i* in group ``group_ids[i]``.

    Filler spans are empty, so the group a real asset lands in is exactly the
    value asked for.
    """
    spans = [(0, 0)] * (max(group_ids) + 1)
    for i, g in enumerate(group_ids):
        spans[g] = (i, 1)
    return spans


def _lang_groups(n=32, stride=8):
    """Group table for n localisation assets that step by `stride` in group id.

    Asset *i* must land in group ``stride * i``, so each asset is covered by
    `stride` consecutive spans, only the first of which actually claims it.
    """
    return [(i, 1 if k == 0 else 0) for i in range(n) for k in range(stride)]


def _lang_assets(n=32):
    return [(LOCALIZATION_PATH_HASH, 100 + i, 1000 * i) for i in range(n)]


class TestToc(unittest.TestCase):
    def test_group_assignment(self):
        # one group of 2 assets, then a group of 1
        buf = _build_toc([(0, 2), (2, 1)],
                         [(0xAAAA, 10, 0), (0xBBBB, 20, 10), (0xCCCC, 30, 30)])
        t = Toc(Dat(buf, 0))
        self.assertEqual([a.group for a in t.assets], [0, 0, 1])

    def test_localization_language_id_is_group_over_8(self):
        """Each language is a group of its own, stepping by 8; the asset's byte
        offset is what ties it to a position in the archive blob."""
        t = Toc(Dat(_build_toc(_lang_groups(), _lang_assets()), 0))
        loc = t.localization_assets()
        self.assertEqual(len(loc), 32)
        self.assertEqual([a.language_id for a in loc], list(range(32)))
        # sorted by language id, so offsets come back in language order
        self.assertEqual([a.offset for a in loc], [1000 * i for i in range(32)])

    def test_language_id_is_derived_not_positional(self):
        """Blob order and language order are independent; the TOC joins them."""
        assets = [(LOCALIZATION_PATH_HASH, 10, 0),    # blob slot 0 -> group 24
                  (LOCALIZATION_PATH_HASH, 10, 10),   # blob slot 1 -> group 8
                  (LOCALIZATION_PATH_HASH, 10, 20),   # blob slot 2 -> group 0
                  (LOCALIZATION_PATH_HASH, 10, 30)]   # blob slot 3 -> group 16
        t = Toc(Dat(_build_toc(_s_groups([24, 8, 0, 16]), assets), 0))
        self.assertEqual([a.group for a in t.assets], [24, 8, 0, 16])
        # in blob order the language ids come out shuffled ...
        self.assertEqual([a.language_id for a in t.assets_with_hash(LOCALIZATION_PATH_HASH)],
                         [3, 1, 0, 2])
        # ... and localization_assets() puts them back in language order
        self.assertEqual([a.language_id for a in t.localization_assets()],
                         [0, 1, 2, 3])
        self.assertEqual([a.offset for a in t.localization_assets()], [20, 10, 30, 0])

    def test_archive_name_is_nul_terminated(self):
        buf = _build_toc([(0, 1)], [(1, 1, 0)], archive=b"d\\wem.ar")
        self.assertEqual(Toc(Dat(buf, 0)).archive_name(0), "d\\wem.ar")

    def test_archive_name_ignores_garbage_tail(self):
        """Records are 0x42 bytes but only NUL-terminated -- the tail is junk,
        so the name must be read up to the first NUL, not by stripping NULs."""
        junk = b"d\\wem.ar" + b"\x00" + b"\x40\xb8'\x02" + b"\x11" * (0x42 - 13)
        t = Toc(Dat(_build_toc([(0, 1)], [(1, 1, 0)], archive=junk), 0))
        self.assertEqual(t.archive_name(0), "d\\wem.ar")

    def test_missing_lump_raises(self):
        with self.assertRaises(TocError):
            Toc(Dat(build_container(SAMPLE), 0))

    def test_non_localization_hash_has_no_language_id(self):
        t = Toc(Dat(_build_toc([(0, 1)], [(0x1234, 1, 0)]), 0))
        self.assertIsNone(t.assets[0].language_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)
