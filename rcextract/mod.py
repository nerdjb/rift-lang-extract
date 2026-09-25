"""Install a localisation mod without editing the game's own files.

Rift Apart reads every asset through a table of contents, and the table can
point any asset at any archive.  A mod therefore does not have to touch
``d/localization`` at all: it can add one new archive and repoint a handful of
table-of-contents entries at it.  The original localisation archive is left
exactly as shipped, so uninstalling is "delete one file, put the toc back".

This mirrors what Overstrike's ``.stage`` installer does, reimplemented in
Python so it runs on Linux.  Overstrike is a Windows .NET 7 desktop
application; the archive layout it produces is simple enough to redo here,
and doing so means no dependency on a Windows machine.

What gets written
-----------------

``d/mods/mod1``
    A flat, *uncompressed* concatenation of the replacement assets.  No DSAR
    wrapper and no block directory -- the table of contents addresses each
    asset by (archive, offset, size), and the archive descriptor says not to
    decompress.  This is why no LZ4 encoder is needed anywhere in this
    package.

``toc``
    The same file with one archive record appended and the repointed assets'
    ``(archive_index, offset, size)`` rewritten.  The 8-byte file header
    carries the container length, so that is updated too.

The game is not shipped with a mod loader, so the toc must be modified; the
mod archive is added alongside it rather than merged into ``d/localization``.
That is the same trade Overstrike makes.  :func:`uninstall` puts it back.

Where Arabic goes
-----------------

The retail game has four language slots that are present but empty: ids 23,
28, 29 and 30.  Which of them the game *means* by Arabic is not recorded
anywhere -- the four containers are byte-identical -- so
:func:`default_targets` claims all four.  The cost is four copies of the same
container; the benefit is not having to guess.
"""

from __future__ import annotations

import os
import shutil
import struct
from dataclasses import dataclass, field

from .dat import Dat, serialize, strings_block_of
from .toc import LOCALIZATION_GROUP_STRIDE, LOCALIZATION_PATH_HASH

#: The ``toc`` file is an 8-byte header in front of the ``1TAD`` container.
TOC_MAGIC = 0x34E89035
TOC_HEADER = struct.Struct("<II")

#: The asset type CRC of the table of contents itself.
TOC_TYPE_CRC = 0x4D7CF320

#: Lumps in the table of contents that this module reads or rewrites.
LUMP_TOC_SPANS = 0xEDE8ADA9          # u32 pairs: (first asset index, count)
LUMP_TOC_ASSET_IDS = 0x506D7B8A      # u64[N] asset path hashes
LUMP_TOC_ASSET_META = 0x65BCF461     # 16 * N: size, archive, offset, header
LUMP_TOC_ARCHIVES = 0x398ABFF0       # 66 * A: archive descriptors

#: One archive descriptor in the table of contents.
ARCHIVE_RECORD = struct.Struct("<40sQQIHI")
assert ARCHIVE_RECORD.size == 66

#: One asset's (size, archive_index, offset, header_offset) row.
ASSET_META = struct.Struct("<IIII")
assert ASSET_META.size == 16

#: Load descriptor for an archive this module creates.
#:
#: Six fields per archive: A, B, C, D and the load-order bucket E.
#:
#: A, B, C and D are byte-identical across all 147 retail archives, so they
#: are not per-archive data -- they do not encode size or offset.  They
#: describe how to read the archive's contents.  The retail value goes with
#: DSAR archives whose blocks are LZ4-compressed; this one goes with a flat
#: uncompressed file, which is what a mod archive is.
#:
#: E is a bucket, in units of 0x01000000.  It is 0 for 113 retail archives and
#: non-zero only for the audio pairs: ``d\wem.<lang>`` and
#: ``d\soundbank.<lang>`` share a bucket, and the buckets ascend with archive
#: index.  So it is a load-order hint, and 0 -- the default, and what this
#: module uses -- is right for a mod archive.
#:
#: Taken from Overstrike's ``TOC_I29.AddNewArchive``, whose mods are known to
#: work.  Not derived from the game's own archives, and not understood well
#: enough to be sure the only difference from the retail value is compression.
MOD_ARCHIVE_DESCRIPTOR = (0x26FB4987040, 0x26FB4987040, 0xE522446B, 0x7FFB, 0)

#: Fields of a descriptor that every retail archive agrees on, measured from
#: the shipped ``toc`` rather than assumed.
RETAIL_SHARED_DESCRIPTOR = (0x2283B699040, 0x2283B699040, 0xBE26446B, 0x7FFB)

#: Where a mod archive is written, relative to the install root.
MOD_ARCHIVE_REL = os.path.join("d", "mods", "mod1")

#: The archive name as the toc spells it (backslashes, no extension).
MOD_ARCHIVE_NAME = "d\\mods\\mod1"


class ModError(Exception):
    """Raised when a mod cannot be planned or installed."""


# -- archive records ------------------------------------------------------

def parse_archive_record(raw: bytes) -> dict:
    """Split one 66-byte archive descriptor into its fields."""
    name, a, b, c, d, e = ARCHIVE_RECORD.unpack(raw)
    return {
        "name": name.split(b"\x00", 1)[0].decode("ascii", "replace"),
        "a": a, "b": b, "c": c, "d": d, "e": e,
    }


def build_archive_record(name: str, descriptor=MOD_ARCHIVE_DESCRIPTOR) -> bytes:
    """Build one 66-byte archive descriptor.

    The name field is 40 bytes and NUL-terminated; the rest is the load
    descriptor, which this module does not vary.
    """
    raw = name.encode("ascii")
    if len(raw) >= 40:
        raise ModError("archive name %r does not fit in 40 bytes" % name)
    return ARCHIVE_RECORD.pack(raw + b"\x00" * (40 - len(raw)), *descriptor)


# -- the table of contents ------------------------------------------------

@dataclass
class TocImage:
    """A loaded ``toc``, kept in a form that can be edited and written back.

    Every lump is held as raw bytes.  Only the archive table and the asset
    metadata rows are ever interpreted, so any lump this module does not
    understand is copied through untouched.
    """

    dat: Dat
    header: bytes
    """The 8 bytes in front of the ``1TAD`` magic."""
    trailer: bytes = b""
    """Bytes after the declared container.

    The retail ``toc`` has 284 zero bytes past the end its own header
    declares.  Nothing points into them, but they are copied through so a
    rewrite never makes the file smaller than it was.
    """

    _lumps: dict[int, bytes] = field(default_factory=dict, repr=False)
    _order: list[int] = field(default_factory=list, repr=False)

    @classmethod
    def load(cls, path: str) -> "TocImage":
        with open(path, "rb") as fh:
            buf = fh.read()
        if len(buf) < TOC_HEADER.size + 4:
            raise ModError("%s is too small to be a table of contents" % path)
        magic, length = TOC_HEADER.unpack_from(buf, 0)
        if magic != TOC_MAGIC:
            raise ModError("%s: bad toc magic %#010x" % (path, magic))
        if buf[TOC_HEADER.size:TOC_HEADER.size + 4] != b"1TAD":
            raise ModError("%s has no 1TAD container after its header" % path)
        d = Dat(buf, TOC_HEADER.size)
        img = cls(dat=d, header=buf[:TOC_HEADER.size])
        img._order = [l.type_crc for l in sorted(d.lumps, key=lambda x: x.offset)]
        img._lumps = {l.type_crc: d.lump(l.type_crc) for l in d.lumps}
        if length != d.file_size:
            raise ModError("%s: header says %d bytes, container says %d"
                           % (path, length, d.file_size))
        trailer = buf[TOC_HEADER.size + length:]
        if trailer.strip(b"\x00"):
            raise ModError("%s has %d bytes of non-zero data after the toc"
                           % (path, len(trailer)))
        img.trailer = trailer
        return img

    # -- lumps
    def lump(self, crc: int) -> bytes:
        try:
            return self._lumps[crc]
        except KeyError:
            raise ModError("table of contents has no lump %#010x" % crc) from None

    def set_lump(self, crc: int, data: bytes) -> None:
        if crc not in self._lumps:
            raise ModError("table of contents has no lump %#010x" % crc)
        self._lumps[crc] = data

    @property
    def strings_block(self) -> bytes:
        return strings_block_of(self.dat)

    # -- archives
    @property
    def archives(self) -> list[dict]:
        raw = self.lump(LUMP_TOC_ARCHIVES)
        if len(raw) % ARCHIVE_RECORD.size:
            raise ModError("archive table is not a whole number of 66-byte records")
        return [parse_archive_record(raw[i * 66:(i + 1) * 66])
                for i in range(len(raw) // 66)]

    def archive_index(self, name: str) -> int | None:
        for i, rec in enumerate(self.archives):
            if rec["name"].lower() == name.lower():
                return i
        return None

    def add_archive(self, name: str) -> int:
        """Append an archive record and return its index."""
        existing = self.archive_index(name)
        if existing is not None:
            raise ModError("the toc already lists an archive called %r "
                           "(index %d)" % (name, existing))
        raw = self.lump(LUMP_TOC_ARCHIVES)
        self.set_lump(LUMP_TOC_ARCHIVES,
                       raw + build_archive_record(name))
        return len(self.archives) - 1

    def remove_archive(self, name: str) -> None:
        """Drop the last archive record, which must be `name`."""
        raw = self.lump(LUMP_TOC_ARCHIVES)
        recs = self.archives
        if not recs:
            raise ModError("the toc lists no archives")
        if recs[-1]["name"].lower() != name.lower():
            raise ModError("refusing to remove %r: the last archive is %r"
                           % (name, recs[-1]["name"]))
        self.set_lump(LUMP_TOC_ARCHIVES, raw[:-ARCHIVE_RECORD.size])

    # -- assets
    @property
    def asset_count(self) -> int:
        raw = self.lump(LUMP_TOC_ASSET_META)
        if len(raw) % ASSET_META.size:
            raise ModError("asset metadata table is not a whole number of 16-byte rows")
        return len(raw) // ASSET_META.size

    def asset_meta(self, index: int) -> tuple[int, int, int, int]:
        raw = self.lump(LUMP_TOC_ASSET_META)
        return ASSET_META.unpack_from(raw, index * ASSET_META.size)

    def set_asset_meta(self, index: int, size=None, archive=None,
                       offset=None, header=None) -> None:
        raw = bytearray(self.lump(LUMP_TOC_ASSET_META))
        s, a, o, h = ASSET_META.unpack_from(raw, index * ASSET_META.size)
        if size is not None:
            s = size
        if archive is not None:
            a = archive
        if offset is not None:
            o = offset
        if header is not None:
            h = header
        ASSET_META.pack_into(raw, index * ASSET_META.size, s, a, o, h)
        self.set_lump(LUMP_TOC_ASSET_META, bytes(raw))

    def asset_id(self, index: int) -> int:
        raw = self.lump(LUMP_TOC_ASSET_IDS)
        if (index + 1) * 8 > len(raw):
            raise ModError("asset id table is too short for index %d" % index)
        return struct.unpack_from("<Q", raw, index * 8)[0]

    @property
    def span_count(self) -> int:
        return len(self.lump(LUMP_TOC_SPANS)) // 8

    def group_of(self, index: int) -> int:
        """Which TOC group (span) an asset index belongs to."""
        raw = self.lump(LUMP_TOC_SPANS)
        first, count = struct.unpack_from("<II", raw, 0)
        if index < first:
            raise ModError("asset %d precedes the first span" % index)
        for g in range(len(raw) // 8):
            f, c = struct.unpack_from("<II", raw, g * 8)
            if f <= index < f + c:
                return g
        raise ModError("asset %d belongs to no span" % index)

    def find_localization_assets(self) -> dict[int, int]:
        """Map language id -> asset index, for every localisation asset.

        All 32 localisation assets share one path hash, so they are told apart
        only by their TOC group, where ``group // 8`` is the language id.
        """
        out: dict[int, int] = {}
        ids = self.lump(LUMP_TOC_ASSET_IDS)
        n = self.asset_count
        for i in range(n):
            if struct.unpack_from("<Q", ids, i * 8)[0] != LOCALIZATION_PATH_HASH:
                continue
            lid = self.group_of(i) // LOCALIZATION_GROUP_STRIDE
            if lid in out:
                raise ModError("language id %d has more than one toc asset "
                               "(%d and %d)" % (lid, out[lid], i))
            out[lid] = i
        return out

    # -- writing
    def to_bytes(self) -> bytes:
        """Serialise back to a complete ``toc`` file."""
        body = serialize(
            ((crc, self._lumps[crc]) for crc in self._order),
            type_crc=self.dat.asset_type_crc,
            strings_block=self.strings_block,
            align=16,
        )
        # The header's second field is the container length, so it has to be
        # recomputed; the magic is carried over rather than assumed.
        magic, _ = TOC_HEADER.unpack_from(self.header, 0)
        return TOC_HEADER.pack(magic, len(body)) + body + self.trailer

    def save(self, path: str) -> None:
        data = self.to_bytes()
        tmp = path + ".rcextract-tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)


# -- the mod itself -------------------------------------------------------

@dataclass
class Mod:
    """A set of replacement assets, and where each one goes."""

    name: str
    archive_name: str = MOD_ARCHIVE_NAME
    archive_rel: str = MOD_ARCHIVE_REL
    #: (toc asset index, payload) pairs, in the order they are laid down.
    assets: list[tuple[int, bytes]] = field(default_factory=list)
    descriptor: tuple = MOD_ARCHIVE_DESCRIPTOR
    _placed: list[tuple[int, int, int]] = field(default_factory=list, repr=False)
    """(asset index, offset in the archive, size), filled in by archive_bytes."""

    def add(self, asset_index: int, payload: bytes) -> None:
        self.assets.append((asset_index, payload))

    def archive_bytes(self) -> bytes:
        """The mod archive: payloads laid end to end, uncompressed."""
        out = bytearray()
        placed = []
        for index, payload in self.assets:
            placed.append((index, len(out), len(payload)))
            out += payload
        self._placed = placed
        return bytes(out)


# -- planning -------------------------------------------------------------

def default_targets() -> tuple[int, ...]:
    """The language slots a new language should claim.

    Retail ships 32 slots; 23, 28, 29 and 30 exist but hold no text.  The
    game does not record which of them it means by Arabic -- all four are
    byte-identical -- so all four are claimed.
    """
    from .languages import EMPTY_SLOTS
    return tuple(EMPTY_SLOTS)


def build_mod(image: TocImage, payloads: dict[int, bytes],
              slots=None, name: str = "rcextract localisation mod",
              archive_name: str = MOD_ARCHIVE_NAME,
              archive_rel: str = MOD_ARCHIVE_REL) -> Mod:
    """Plan a mod that serves `payloads` for the given language `slots`.

    `payloads` maps language id to a built localisation container.  `slots`
    defaults to every empty retail slot, and every slot in it is given the
    same payload, so the result does not depend on knowing which slot the
    game means by Arabic.
    """
    if slots is None:
        slots = default_targets()
    assets = image.find_localization_assets()
    missing = [s for s in slots if s not in assets]
    if missing:
        raise ModError("the toc has no localisation asset for language "
                       "slot(s) %s" % ", ".join(str(s) for s in missing))
    absent = sorted(s for s in slots if s not in payloads)
    if absent:
        raise ModError("no payload for language slot(s) %s"
                       % ", ".join(str(s) for s in absent))

    mod = Mod(name=name, archive_name=archive_name, archive_rel=archive_rel)
    for slot in slots:
        mod.add(assets[slot], payloads[slot])
    return mod


# -- installing -----------------------------------------------------------

def install(root: str, mod: Mod, backup: bool = True) -> dict:
    """Write the mod archive and repoint the toc.  Returns what it did.

    `d/localization` is never opened.  The toc is replaced atomically, and by
    default a copy is kept next to it so :func:`uninstall` can restore it.
    """
    toc_path = os.path.join(root, "toc")
    if not os.path.isfile(toc_path):
        raise ModError("no toc at %s" % toc_path)
    image = TocImage.load(toc_path)

    archive_path = os.path.join(root, *mod.archive_rel.split("\\"))
    if os.path.exists(archive_path):
        raise ModError("%s already exists; run uninstall first" % archive_path)

    blob = mod.archive_bytes()

    index = image.add_archive(mod.archive_name)
    placed = []
    for asset_index, offset, size in mod._placed:
        image.set_asset_meta(asset_index, size=size, archive=index, offset=offset)
        placed.append((asset_index, offset, size))

    data = image.to_bytes()

    os.makedirs(os.path.dirname(archive_path), exist_ok=True)
    if backup and not os.path.exists(toc_path + ".orig"):
        shutil.copy2(toc_path, toc_path + ".orig")
    with open(archive_path, "wb") as fh:
        fh.write(blob)
    try:
        image.save(toc_path)
    except OSError:
        os.unlink(archive_path)
        raise

    return {
        "archive": archive_path,
        "archive_bytes": len(blob),
        "toc_bytes": len(data),
        "archive_index": index,
        "assets": placed,
        "backup": toc_path + ".orig" if backup else None,
    }


def uninstall(root: str, archive_rel: str = MOD_ARCHIVE_REL) -> dict:
    """Delete the mod archive and restore the toc from the backup."""
    toc_path = os.path.join(root, "toc")
    backup = toc_path + ".orig"
    archive_path = os.path.join(root, *archive_rel.split("\\"))

    if not os.path.exists(backup):
        raise ModError("no backup at %s; cannot restore the toc automatically" % backup)
    removed = os.path.exists(archive_path)
    if removed:
        os.unlink(archive_path)
    shutil.copy2(backup, toc_path)
    return {"archive_removed": removed,
            "archive": archive_path,
            "toc_restored": toc_path}
