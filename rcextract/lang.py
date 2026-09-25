"""Read a Rift Apart localisation string table.

Each language container is a ``1TAD`` asset holding a flat key/value string
table.  All 25,034 keys are always present; a key is *translated* only if its
entry in the value-offset table is non-zero.  That is why a retail build has
"empty" language containers: the key table is shared, only the value offsets
are all zero.

Lumps used::

    0xd540a903  u32        N, the number of strings
    0x4d73cebd  bytes      key blob:   N NUL-terminated keys, in index order
    0xa4ea55b2  u32[N]     key offsets into the key blob
    0x70a382b8  bytes      value blob: NUL-terminated localised strings
    0xf80deeb4  u32[N]     value offsets into the value blob; 0 = untranslated
    0xb0653243  u32[N]     all zero in every retail language
    0x06a58050  u32[N]     key hash table
    0x0cd2cfe9  u32[N/2]   key hash overflow table
    0xc43731b5  u32[N]     secondary key offsets

The key blob and the index order line up 1:1, so a string can be addressed
either by index or by key without consulting the offset tables.

**The value blob begins with a NUL byte**, so offset 0 is a reserved sentinel
standing for "no translation" and every real string starts at offset 1 or
later.  That is what makes ``value_offset == 0`` a safe test for "untranslated"
-- without the leading NUL, the first string would be indistinguishable from
a missing one.  The key blob has no such sentinel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Sequence

from .dat import Dat, DatError, scan_containers
from .languages import BY_ID, Language
from .lump_types import (
    LUMP_LANG_COUNT,
    LUMP_LANG_KEYS,
    LUMP_LANG_KEY_OFF,
    LUMP_LANG_VALUES,
    LUMP_LANG_VALUE_OFF,
    LUMP_LANG_ZERO,
)


class LocalizationError(Exception):
    """Raised when a container is not a usable localisation string table."""


@dataclass
class StringTable:
    """One language's key/value pairs plus the metadata around them."""

    language_id: int
    offset: int
    file_size: int
    key_count: int
    keys: list[str]
    values: list[str | None]
    container_index: int = -1
    """Position of this container in the archive blob (not the language id)."""
    language_verified: bool = True
    """False when the language id is a guess (no usable table of contents)."""
    flags: list[int] = field(default_factory=list, repr=False)
    """Per-entry flag byte (lump 0xb0653243).

    Zero throughout every retail language except the two Portuguese ones, so
    a writer must carry it through rather than assume zero.
    """
    _index: dict[str, int] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if not self._index:
            self._index = {k: i for i, k in enumerate(self.keys)}

    # -- identity ----------------------------------------------------------
    @property
    def language(self) -> Language:
        return BY_ID[self.language_id]

    @property
    def code(self) -> str:
        return self.language.code or f"slot{self.language_id}"

    @property
    def translated_count(self) -> int:
        return sum(1 for v in self.values if v is not None)

    def __len__(self) -> int:
        return self.key_count

    def __repr__(self) -> str:
        return "<StringTable %s id=%d %d/%d translated>" % (
            self.code, self.language_id, self.translated_count, self.key_count)

    # -- access ------------------------------------------------------------
    def get(self, key: str) -> str | None:
        """Return the translated string for `key`, or None if untranslated."""
        i = self._index.get(key)
        return None if i is None else self.values[i]

    def __getitem__(self, key: str) -> str:
        v = self.get(key)
        if v is None:
            raise KeyError("key %r is untranslated in %s" % (key, self.code))
        return v

    def __contains__(self, key: str) -> bool:
        return key in self._index

    def items(self, only_translated: bool = True) -> Iterator[tuple[str, str]]:
        for k, v in zip(self.keys, self.values):
            if v is not None or not only_translated:
                yield k, v if v is not None else ""

    def template(self) -> Iterator[tuple[str, str]]:
        """Every key, with the value blanked out.

        This is what you want as a starting point for a new or partial
        language: all 25,034 keys in archive order, ready to fill in.
        """
        return self.items(only_translated=False)

    def keys_with_prefix(self, prefix: str) -> list[str]:
        return [k for k in self.keys if k.startswith(prefix)]

    def sample(self, n: int = 3) -> list[tuple[str, str]]:
        """A few translated pairs, for eyeballing which language this is."""
        out = []
        for k, v in self.items():
            out.append((k, v))
            if len(out) >= n:
                break
        return out


def parse_container(container: Dat) -> StringTable:
    """Turn one ``1TAD`` container into a :class:`StringTable`."""
    for crc in (LUMP_LANG_COUNT, LUMP_LANG_KEYS, LUMP_LANG_VALUES, LUMP_LANG_VALUE_OFF):
        if not container.has(crc):
            raise LocalizationError(
                "container at %#x is missing localisation lump %#010x "
                "(is it really a language table?)" % (container.base, crc))

    n = container.u32(LUMP_LANG_COUNT)

    key_blob = container.lump(LUMP_LANG_KEYS)
    val_blob = container.lump(LUMP_LANG_VALUES)
    key_off = container.u32_array(LUMP_LANG_KEY_OFF)
    val_off = container.u32_array(LUMP_LANG_VALUE_OFF)

    if len(key_off) < n:
        raise LocalizationError("key offset table has %d entries, expected %d"
                                % (len(key_off), n))
    if len(val_off) < n:
        raise LocalizationError("value offset table has %d entries, expected %d"
                                % (len(val_off), n))

    # Strings are NUL-terminated.  Decoding with surrogateescape keeps the
    # table lossless even if a build ships a string in an unexpected encoding.
    dec = lambda b: b.decode("utf-8", "surrogateescape")

    keys: list[str] = []
    for i in range(n):
        o = key_off[i]
        if o == 0:
            # Fall back to positional order when the offset table says 0.
            keys.append(dec(_cstr(key_blob, _nth_offset(key_blob, i))))
        else:
            keys.append(dec(_cstr(key_blob, o)))

    values: list[str | None] = []
    for i in range(n):
        o = val_off[i]
        values.append(None if o == 0 else dec(_cstr(val_blob, o)))

    # Lump 0xb0653243 is one flag byte per entry followed by 3N zero bytes.
    raw_flags = container.get(LUMP_LANG_ZERO, b"")
    flags = list(raw_flags[:n]) if len(raw_flags) >= n else [0] * n

    return StringTable(
        language_id=0,  # caller assigns once it knows the slot order
        offset=container.base,
        file_size=container.file_size,
        key_count=n,
        keys=keys,
        values=values,
        flags=flags,
    )


def _cstr(buf: bytes, off: int) -> bytes:
    """Read a NUL-terminated string from `buf` at `off`."""
    if off < 0 or off > len(buf):
        return b""
    end = buf.find(b"\x00", off)
    if end < 0:
        end = len(buf)
    return buf[off:end]


def _nth_offset(buf: bytes, n: int) -> int:
    """Offset of the n-th NUL-terminated string, scanning from the start."""
    off = 0
    for _ in range(n):
        nxt = buf.find(b"\x00", off)
        if nxt < 0:
            return len(buf)
        off = nxt + 1
    return off


def load_all(buffer: bytes, language_ids: Sequence[int] | None = None) -> list[StringTable]:
    """Parse every language container in a decompressed ``d/localization``.

    The containers are stored in a fixed order that is **not** the language
    order, so `language_ids` should be supplied from the table of contents:
    pass the language id for each container *in blob order*.  With no mapping
    the container index is used, which labels the tables wrongly -- the
    contents are still correct.
    """
    containers = scan_containers(buffer)
    tables = []
    for i, c in enumerate(containers):
        try:
            t = parse_container(c)
        except (LocalizationError, DatError):
            continue
        t.container_index = i
        t.language_id = language_ids[i] if language_ids and i < len(language_ids) else i
        tables.append(t)
    if not tables:
        raise LocalizationError(
            "no localisation containers found; is this a decompressed "
            "d/localization?")
    return tables


def write_json(tables: Sequence[StringTable], indent: int | None = 2) -> str:
    """Serialise tables to JSON as a list of {language, entries} objects."""
    import json
    out = []
    for t in tables:
        out.append({
            "language_id": t.language_id,
            "code": t.code,
            "language": t.language.name,
            "key_count": t.key_count,
            "translated": t.translated_count,
            "entries": [[k, v] for k, v in t.items()],
        })
    return json.dumps(out, ensure_ascii=False, indent=indent)
