"""``1TAD`` (a.k.a. DAT) asset container.

Every asset in the game -- textures, models, the table of contents, each
language's string table -- is stored in a container that begins with the
magic bytes ``1TAD``.  The format is a flat lump directory followed by lump
payloads::

    0x00  4s   magic '1TAD'
    0x04  u32  asset_type_crc    identifies the *kind* of asset
    0x08  u32  file_size         total container size, header + directory + data
    0x0C  u16  lump_count
    0x0E  u16  shader_count
    0x10  lump_count * 12 bytes
             u32  lump_type_crc   identifies the *kind* of each lump
             u32  offset          relative to the start of the container
             u32  size
    ...   lump payloads, each already aligned to 4 bytes

The ``1TAD`` magic can be preceded by a small number of unrelated bytes (the
main ``toc`` file has 8), so the constructor takes an explicit offset.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Iterable, Iterator

from .lump_types import LUMP_TYPES

MAGIC = b"1TAD"

_HEADER = struct.Struct("<4sIIHH")
_DIRENT = struct.Struct("<III")

assert _HEADER.size == 16
assert _DIRENT.size == 12


class DatError(Exception):
    """Raised when a buffer is not a readable ``1TAD`` container."""


@dataclass(frozen=True)
class Lump:
    """One lump inside a ``1TAD`` container.  ``offset`` is container-relative."""

    type_crc: int
    offset: int
    size: int

    @property
    def type_name(self) -> str:
        return LUMP_TYPES.get(self.type_crc, "unknown(0x%08x)" % self.type_crc)


class Dat:
    """Random-access view over one ``1TAD`` container.

    The container is not copied; ``buffer`` is sliced lazily, so parsing the
    directory of a 2 MB container costs a few hundred bytes of real work.
    """

    def __init__(self, buffer: bytes, base: int = 0, strict: bool = True) -> None:
        self.buffer = buffer
        self.base = base

        if len(buffer) - base < _HEADER.size:
            raise DatError("buffer too small for a 1TAD header")
        magic, self.asset_type_crc, self.file_size, lump_count, self.shader_count = \
            _HEADER.unpack_from(buffer, base)
        if magic != MAGIC:
            raise DatError("bad magic %r at offset %d, expected %r" % (magic, base, MAGIC))

        end = base + 0x10 + lump_count * _DIRENT.size
        if len(buffer) < end:
            raise DatError("lump directory runs past end of buffer")

        self.lump_count = lump_count
        self.lumps: list[Lump] = [
            Lump(*_DIRENT.unpack_from(buffer, base + 0x10 + i * _DIRENT.size))
            for i in range(lump_count)
        ]
        self._by_crc: dict[int, Lump] = {l.type_crc: l for l in self.lumps}

        if strict and self.file_size and base + self.file_size > len(buffer):
            raise DatError(
                "container claims %d bytes but only %d available from offset %d"
                % (self.file_size, len(buffer) - base, base))

    # -- introspection -----------------------------------------------------
    def __repr__(self) -> str:
        return "<Dat base=%#x type=%#010x lumps=%d size=%d>" % (
            self.base, self.asset_type_crc, self.lump_count, self.file_size)

    @property
    def asset_type_name(self) -> str:
        return LUMP_TYPES.get(self.asset_type_crc, "unknown(0x%08x)" % self.asset_type_crc)

    def __len__(self) -> int:
        return self.lump_count

    def __iter__(self) -> Iterator[Lump]:
        return iter(self.lumps)

    def __contains__(self, type_crc: int) -> bool:
        return type_crc in self._by_crc

    def describe(self) -> str:
        rows = ["%-12s %-24s %10s %12s" % ("type_crc", "name", "size", "offset")]
        for l in sorted(self.lumps, key=lambda x: x.type_crc):
            rows.append("%#010x   %-24s %10d %12s"
                        % (l.type_crc, l.type_name, l.size,
                           hex(self.base + l.offset)))
        return "\n".join(rows)

    # -- payload access ----------------------------------------------------
    def has(self, type_crc: int) -> bool:
        return type_crc in self._by_crc

    def lump(self, type_crc: int) -> bytes:
        """Return the payload of the lump with this type CRC, or raise."""
        try:
            l = self._by_crc[type_crc]
        except KeyError:
            raise DatError("no lump with type CRC %#010x in container" % type_crc) from None
        start = self.base + l.offset
        return self.buffer[start:start + l.size]

    def get(self, type_crc: int, default=None):
        """Return the payload of this lump, or `default` if it is absent."""
        l = self._by_crc.get(type_crc)
        if l is None:
            return default
        start = self.base + l.offset
        return self.buffer[start:start + l.size]

    def u32(self, type_crc: int) -> int:
        raw = self.lump(type_crc)
        if len(raw) < 4:
            raise DatError("lump %#010x is too small to hold a u32" % type_crc)
        return struct.unpack_from("<I", raw, 0)[0]

    def u32_array(self, type_crc: int) -> tuple[int, ...]:
        raw = self.lump(type_crc)
        if len(raw) % 4:
            raise DatError("lump %#010x is not a whole number of u32s" % type_crc)
        return struct.unpack("<%dI" % (len(raw) // 4), raw)


def serialize(lumps: Iterable[tuple[int, bytes]], type_crc: int = 0,
              strings_block: bytes = b"", magic: bytes = MAGIC,
              align: int = 4) -> bytes:
    """Serialise a ``1TAD`` container from ``(type_crc, payload)`` pairs.

    `lumps` is given in the order the payloads should appear on disk.  The
    directory itself is always written sorted by type CRC, which is how
    Insomniac's serialiser emits it and how every retail container reads.

    `strings_block` is the run of bytes between the end of the directory and
    the first lump.  Several containers carry a human-readable label there
    ("Localization Built File", "ArchiveTOC") and it is not padding: it is
    copied through unchanged, so a rebuilt container keeps it.

    The last lump is not padded, and ``file_size`` is its end -- padding it
    would make the container claim bytes it does not have.
    """
    lumps = list(lumps)
    if len({crc for crc, _ in lumps}) != len(lumps):
        raise DatError("duplicate lump type CRC")

    dir_end = 0x10 + 12 * len(lumps)
    cursor = dir_end + len(strings_block)
    if align > 1:
        cursor += -cursor % align

    offsets: list[int] = []
    for i, (_, payload) in enumerate(lumps):
        offsets.append(cursor)
        cursor += len(payload)
        if i != len(lumps) - 1 and align > 1:
            cursor += -cursor % align

    out = bytearray()
    out += magic
    out += struct.pack("<II", type_crc, cursor)
    out += struct.pack("<HH", len(lumps), 0)
    for (crc, payload), off in sorted(zip(lumps, offsets)):
        out += struct.pack("<III", crc, off, len(payload))
    assert len(out) == dir_end
    out += strings_block
    if align > 1:
        out += bytes(-len(out) % align)
    for i, (_, payload) in enumerate(lumps):
        assert len(out) == offsets[i]
        out += payload
        if i != len(lumps) - 1 and align > 1:
            out += bytes(-len(out) % align)
    assert len(out) == cursor
    return bytes(out)


def strings_block_of(dat: Dat) -> bytes:
    """The bytes between a container's lump directory and its first lump."""
    dir_end = 0x10 + 12 * dat.lump_count
    first = min(l.offset for l in dat.lumps) if dat.lumps else dir_end
    return dat.buffer[dat.base + dir_end:dat.base + first]


def scan_containers(buffer: bytes, limit: int | None = None) -> list[Dat]:
    """Walk a buffer that holds ``1TAD`` containers back to back.

    The localisation archive is exactly this: 32 language containers, one per
    supported language, concatenated with no padding between them.  Each
    container's ``file_size`` field says where the next one starts.
    """
    out: list[Dat] = []
    off = 0
    n = len(buffer)
    while off + _HEADER.size <= n:
        if buffer[off:off + 4] != MAGIC:
            break
        d = Dat(buffer, off, strict=False)
        if not d.file_size:
            break
        out.append(d)
        if limit is not None and len(out) >= limit:
            break
        off += d.file_size
    return out
