"""Main table of contents.

The install root holds one ``toc`` file: a bare ``1TAD`` container (8 bytes
of header, then the magic) that indexes *every* asset in the game.

It matters for localisation because the 32 language tables cannot be told
apart any other way.  All 32 are registered under the same path hash
``0xbe55d94f171bf8de`` in archive ``d\\localization``; the only thing that
distinguishes them is the TOC *group*, and ``group // 8`` is the language id.

Relevant lumps::

    0xede8ada9  u32 pairs  asset groups: (first_index, count)
    0x506d7b8a  u64[N]     asset path hashes
    0x65bcf461  16 * N    per-asset (size, archive_index, offset, header_offset)
    0x398abff0  0x42 * A   archive names, NUL-terminated
    0x654bded9            optional per-asset headers
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from .dat import Dat
from .lump_types import (
    LUMP_TOC_ARCHIVE_ASSET_IDS,
    LUMP_TOC_ARCHIVE_ASSET_METADATA,
    LUMP_TOC_FILE_METADATA,
    LUMP_TOC_HEADER,
)

_HEADER = struct.Struct("<II")
_METADATA = struct.Struct("<IIII")
_ARCHIVE_NAME_LEN = 0x42

#: The path hash shared by all 32 localisation assets.
LOCALIZATION_PATH_HASH = 0xBE55D94F171BF8DE

#: TOC groups for the localisation archive step by this much between languages.
LOCALIZATION_GROUP_STRIDE = 8

#: The archive that holds the 32 retail containers, as the TOC spells it.
#:
#: Not the asset path -- that is
#: ``localization/localization_all.localization``, which is what
#: :data:`LOCALIZATION_PATH_HASH` is the hash of.  This is the *archive* name.
LOCALIZATION_ARCHIVE = "d\\localization"


class TocError(Exception):
    """Raised when a table of contents cannot be parsed."""


@dataclass(frozen=True)
class Asset:
    index: int
    path_hash: int
    size: int
    archive_index: int
    offset: int
    header_offset: int
    group: int

    @property
    def language_id(self) -> int | None:
        """For localisation assets, the language slot this asset holds."""
        if self.path_hash != LOCALIZATION_PATH_HASH:
            return None
        return self.group // LOCALIZATION_GROUP_STRIDE


class Toc:
    """Parsed table of contents."""

    def __init__(self, dat: Dat) -> None:
        for crc in (LUMP_TOC_HEADER, LUMP_TOC_ARCHIVE_ASSET_IDS,
                    LUMP_TOC_ARCHIVE_ASSET_METADATA, LUMP_TOC_FILE_METADATA):
            if not dat.has(crc):
                raise TocError("table of contents is missing lump %#010x" % crc)

        self._dat = dat

        raw_groups = dat.lump(LUMP_TOC_HEADER)
        if len(raw_groups) % 8:
            raise TocError("group table is not a whole number of (first, count) pairs")
        self.groups: list[tuple[int, int]] = list(
            struct.unpack("<%dI" % (len(raw_groups) // 4), raw_groups))
        self.groups = [(self.groups[i], self.groups[i + 1])
                       for i in range(0, len(self.groups), 2)]

        raw_ids = dat.lump(LUMP_TOC_ARCHIVE_ASSET_IDS)
        if len(raw_ids) % 8:
            raise TocError("asset id table is not a whole number of u64s")
        ids = struct.unpack("<%dQ" % (len(raw_ids) // 8), raw_ids)

        meta = dat.lump(LUMP_TOC_ARCHIVE_ASSET_METADATA)
        n = len(meta) // _METADATA.size
        if len(ids) < n:
            raise TocError("asset id table (%d) is shorter than the metadata table (%d)"
                           % (len(ids), n))

        group_of = self._group_lookup(n)

        self.assets: list[Asset] = []
        for i in range(n):
            size, arch, off, hdr = _METADATA.unpack_from(meta, i * _METADATA.size)
            self.assets.append(Asset(i, ids[i], size, arch, off, hdr, group_of[i]))

        raw_arch = dat.lump(LUMP_TOC_FILE_METADATA)
        self.archives: list[str] = []
        for i in range(len(raw_arch) // _ARCHIVE_NAME_LEN):
            e = raw_arch[i * _ARCHIVE_NAME_LEN:(i + 1) * _ARCHIVE_NAME_LEN]
            # Names are NUL-terminated but the tail of the record is not zeroed.
            self.archives.append(e.split(b"\x00", 1)[0].decode("utf-8", "replace"))

    # -- construction ------------------------------------------------------
    @classmethod
    def open(cls, root: str) -> "Toc":
        """Load the ``toc`` file from a game install root."""
        from .game import open_toc as _open
        return cls(_open(root))

    def _group_lookup(self, n: int) -> list[int]:
        """Map asset index -> group index.

        The group table is a list of (first_index, count) spans, exactly one
        group per asset, so this is a single linear fill.
        """
        out = [0] * n
        for gi, (first, count) in enumerate(self.groups):
            hi = min(first + count, n)
            if first >= n:
                break
            for i in range(first, hi):
                out[i] = gi
        return out

    # -- queries -----------------------------------------------------------
    def archive_name(self, index: int) -> str:
        if 0 <= index < len(self.archives):
            return self.archives[index]
        return "<archive %d?>" % index

    def archive_names(self) -> list[str]:
        return list(self.archives)

    def archive_summary(self, top: int = 0) -> list[tuple[int, str]]:
        """(asset count, archive name) sorted by count descending."""
        counts: dict[int, int] = {}
        for a in self.assets:
            counts[a.archive_index] = counts.get(a.archive_index, 0) + 1
        rows = [(c, self.archive_name(i)) for i, c in counts.items()]
        rows.sort(key=lambda r: (-r[0], r[1]))
        return rows[:top] if top else rows

    def assets_with_hash(self, path_hash: int) -> list[Asset]:
        return [a for a in self.assets if a.path_hash == path_hash]

    def localization_assets(self) -> list[Asset]:
        """The 32 localisation assets, ordered by language id."""
        return sorted(self.assets_with_hash(LOCALIZATION_PATH_HASH),
                      key=lambda a: a.language_id or 0)

    def __len__(self) -> int:
        return len(self.assets)

    def __repr__(self) -> str:
        return "<Toc assets=%d groups=%d archives=%d>" % (
            len(self.assets), len(self.groups), len(self.archives))
