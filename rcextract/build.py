"""Build a Rift Apart localisation container from a key/value table.

This is the write half of :mod:`rcextract.lang`.  Reading a language table is
easy because the game hands you a finished container; writing one means
reproducing the serialiser exactly, because the game looks strings up by a
hash of the key and nothing else.

Container layout
----------------

A localisation container is a ``1TAD`` container (see :mod:`rcextract.dat`)
carrying nine lumps.  The directory is sorted by type CRC, but the payloads
are laid out in a fixed *logical* order that the serialiser always uses::

    0x00  4s   magic '1TAD'
    0x04  u32  0x122bb0ab   type magic, identifies a localisation container
    0x08  u32  file_size   end of the last lump
    0x0C  u16  9           lump count
    0x0E  u16  0           shader count
    0x10  9 * 12 bytes     directory, ascending by type CRC
    0x7C  36 bytes         "Localization Built File" and zero padding
    0xA0  payloads, each aligned to 16 bytes, in this order:
              0xd540a903  u32        entry count
              0x06a58050  u32[N]     key hash
              0xc43731b5  u32[N]     key hash, sorted ascending
              0x0cd2cfe9  u16[N]     index into the entry order, hash-sorted
              0xa4ea55b2  u32[N]     offset into the key blob
              0xf80deeb4  u32[N]     offset into the value blob
              0xb0653243  u8[N]      flag byte per entry, then 3N zero bytes
              0x4d73cebd  bytes      key blob, NUL-terminated
              0x70a382b8  bytes      value blob, NUL-terminated

Entry order
-----------

Entry 0 is always the key ``INVALID``.  The rest are the remaining keys in
ordinal (UTF-16 code unit) order.  Retail containers follow this exactly, so
reproducing it keeps a rebuilt container indistinguishable from a shipped one.

Untranslated entries
--------------------

A value of ``None`` means *untranslated*: the entry keeps offset 0, which is
the empty string at the head of the value blob, and the game falls back to
English.  This is not a special case invented here -- the four empty language
slots in retail builds are built entirely out of ``None``, and that is how the
game represents "no translation yet".

Key hashes
----------

The lookup key is a CRC-32 with an unusual seed: the reflected polynomial
0xEDB88320, seeded with 0xEDB88320 instead of the usual 0xFFFFFFFF, and with
no final inversion.  See :func:`key_hash`.
"""

from __future__ import annotations

import struct
from typing import Iterable, Mapping, Sequence

from .dat import serialize
from .lump_types import (
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

MAGIC = b"1TAD"

#: Asset type CRC every localisation container carries.
LOCALIZATION_TYPE_CRC = 0x122BB0AB

#: The 36-byte strings block that follows the lump directory.  Insomniac's
#: serialiser always writes exactly this; all 32 retail containers agree.
STRINGS_BLOCK = b"Localization Built File\x00".ljust(36, b"\x00")

#: Payload order on disk, independent of the (tag-sorted) directory order.
DATA_ORDER = (
    LUMP_LANG_COUNT,
    LUMP_LANG_KEY_HASH,
    LUMP_LANG_KEY_ALT,     # key hashes, sorted ascending
    LUMP_LANG_HASH_OVF,    # matching entry indexes, u16
    LUMP_LANG_KEY_OFF,
    LUMP_LANG_VALUE_OFF,
    LUMP_LANG_ZERO,
    LUMP_LANG_KEYS,
    LUMP_LANG_VALUES,
)

#: Lumps are aligned to this many bytes.
_ALIGN = 16

#: Entry 0 is always this key, and it is always untranslated.
FIRST_KEY = "INVALID"


class BuildError(Exception):
    """Raised when a table cannot be serialised into a valid container."""


# -- key hashing ----------------------------------------------------------

def _crc32_table() -> tuple[int, ...]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (0xEDB88320 if c & 1 else 0)
        table.append(c)
    return tuple(table)


_CRC32_TABLE = _crc32_table()


def key_hash(key: str) -> int:
    """Return the hash the game uses to look `key` up.

    CRC-32, reflected, seeded with the polynomial rather than all-ones, and
    with no final inversion -- so it does *not* agree with ``zlib.crc32``.
    """
    crc = 0xEDB88320
    for b in key.encode("utf-8"):
        crc = (crc >> 8) ^ _CRC32_TABLE[0xFF & (crc ^ b)]
    return crc & 0xFFFFFFFF


def ordinal_key(key: str) -> bytes:
    """Sort key reproducing C#'s ``string.CompareOrdinal``.

    Comparing UTF-16 code units rather than code points only differs for
    characters outside the basic multilingual plane, but the game ships those,
    and getting the order wrong would change the entry order.
    """
    return key.encode("utf-16-be", "surrogatepass")


# -- blob helpers ---------------------------------------------------------

def _cstr(s: str) -> bytes:
    return s.encode("utf-8", "surrogateescape") + b"\x00"


# -- ordering -------------------------------------------------------------

def order_entries(keys: Iterable[str]) -> list[str]:
    """Return `keys` in the order the serialiser stores them.

    ``INVALID`` first, everything else in ordinal order.  Duplicates are
    rejected: the game has no way to address two entries with the same key.
    """
    keys = list(keys)
    if len(set(keys)) != len(keys):
        seen: set[str] = set()
        for k in keys:
            if k in seen:
                raise BuildError("duplicate key %r" % k)
            seen.add(k)

    first = [k for k in keys if k == FIRST_KEY]
    rest = sorted((k for k in keys if k != FIRST_KEY), key=ordinal_key)
    return first + rest


# -- serialisation --------------------------------------------------------

def build_container(
    keys: Sequence[str],
    values: Mapping[str, str | None] | Sequence[str | None] | None = None,
    flags: Mapping[str, int] | None = None,
) -> bytes:
    """Serialise a localisation container.

    `keys` is the complete key set; every one of the game's ~25,000 keys must
    be present, because the lookup tables are sized by the entry count and the
    game does not check for a well-formed file.

    `values` may be a mapping or a sequence aligned with `keys`.  A missing
    key, or a value of ``None``, is left untranslated.

    `flags` is the per-entry flag byte (lump 0xb0653243).  It is zero
    throughout retail, so it can be ignored.
    """
    if isinstance(values, Mapping):
        by_key: dict[str, str | None] = dict(values)
    elif values is None:
        by_key = {}
    else:
        seq = list(values)
        if len(seq) != len(keys):
            raise BuildError("got %d values for %d keys" % (len(seq), len(keys)))
        by_key = dict(zip(keys, seq))

    order = order_entries(keys)
    n = len(order)
    if n == 0:
        raise BuildError("refusing to build a container with no entries")
    if n > 0xFFFF:
        raise BuildError("the sorted-index table holds a u16 per entry, "
                         "so at most 65535 entries are addressable")

    key_blob = bytearray()
    key_off: list[int] = []
    for k in order:
        key_off.append(len(key_blob))
        key_blob += _cstr(k)

    # The value blob opens with an empty string at offset 0.  That is the
    # sentinel for "untranslated", and it is why offset 0 is safe to use.
    val_blob = bytearray(b"\x00")
    val_off: list[int] = []
    for k in order:
        v = by_key.get(k)
        if v is None:
            val_off.append(0)
        else:
            val_off.append(len(val_blob))
            val_blob += _cstr(v)

    hashes = [key_hash(k) for k in order]
    hash_sorted = sorted(range(n), key=lambda i: (hashes[i], i))

    unknown = bytearray()
    for i in range(n):
        f = (flags or {}).get(order[i], 0) & 0xFF
        unknown.append(f)
    unknown += bytes(3 * n)

    payloads: dict[int, bytes] = {
        LUMP_LANG_COUNT: struct.pack("<I", n),
        LUMP_LANG_KEY_HASH: struct.pack("<%dI" % n, *hashes),
        LUMP_LANG_KEY_ALT: struct.pack("<%dI" % n, *(hashes[i] for i in hash_sorted)),
        LUMP_LANG_HASH_OVF: struct.pack("<%dH" % n, *hash_sorted),
        LUMP_LANG_KEY_OFF: struct.pack("<%dI" % n, *key_off),
        LUMP_LANG_VALUE_OFF: struct.pack("<%dI" % n, *val_off),
        LUMP_LANG_ZERO: bytes(unknown),
        LUMP_LANG_KEYS: bytes(key_blob),
        LUMP_LANG_VALUES: bytes(val_blob),
    }

    # Header, then the lump directory, then the strings block, then payloads.
    # DATA_ORDER fixes the on-disk order; the directory is sorted by CRC.
    return serialize(
        ((crc, payloads[crc]) for crc in DATA_ORDER),
        type_crc=LOCALIZATION_TYPE_CRC,
        strings_block=STRINGS_BLOCK,
        align=_ALIGN,
    )


def build_from_table(table, overrides: Mapping[str, str | None] | None = None) -> bytes:
    """Rebuild `table` with `overrides` applied.

    Keys absent from `overrides` keep whatever `table` has, except that
    entries which were untranslated stay untranslated.  Use
    ``overrides={k: None}`` to deliberately un-translate a key.
    """
    values: dict[str, str | None] = dict(zip(table.keys, table.values))
    if overrides:
        unknown = [k for k in overrides if k not in values]
        if unknown:
            raise BuildError("override has %d key(s) not in the table, "
                             "starting with %r" % (len(unknown), unknown[0]))
        values.update(overrides)

    flags = None
    if len(getattr(table, "flags", ())) == len(table.keys):
        flags = {k: f for k, f in zip(table.keys, table.flags)}
    return build_container(table.keys, values, flags)
