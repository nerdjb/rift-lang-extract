"""DSAR archive reader.

A DSAR file is a simple block-compressed archive.  Every file in a game
subdirectory (``d/localization``, ``d/config``, ``d/userinterface``, ...) is
one DSAR containing that directory's assets concatenated after decompression.

Layout::

    header  32 bytes
        u32  magic          'DSAR' (0x52415344 little-endian)
        u32  version
        u32  block_count
        u32  data_begin     absolute file offset of the first block's payload
        u64  reserved
        8s   reserved

    block_count * 32 bytes of block descriptors
        u64  dec_offset     absolute offset of the block in the output
        u64  comp_offset    absolute offset of the block's payload in this file
        u32  dec_size       decompressed size
        u32  comp_size      stored size
        u8   mode           3 = LZ4 block, 2 = GDeflate
        7s   reserved

After the descriptor table, each block's compressed payload is stored back to
back.  Blocks are contiguous and ordered, so the whole archive decompresses to
one flat buffer.

The block payloads are concatenated to form the *decompressed archive*, which
is what :mod:`rcextract.dat` and :mod:`rcextract.toc` then parse.
"""

from __future__ import annotations

import io
import struct
from dataclasses import dataclass
from typing import BinaryIO, Callable, Iterator

MAGIC = b"DSAR"
MAGIC_U32 = 0x52415344  # 'DSAR' read little-endian

_HEADER = struct.Struct("<IIIIQ8s")
_BLOCK = struct.Struct("<QQIIB7s")

assert _HEADER.size == 32
assert _BLOCK.size == 32

MODE_LZ4 = 3
MODE_GDEFLATE = 2


class DsarError(Exception):
    """Raised when a file is not a readable DSAR archive."""


@dataclass(frozen=True)
class Block:
    """One compressed chunk of the archive."""

    index: int
    dec_offset: int
    comp_offset: int
    dec_size: int
    comp_size: int
    mode: int

    @property
    def compression(self) -> str:
        return {MODE_LZ4: "lz4", MODE_GDEFLATE: "gdeflate"}.get(self.mode, "unknown(%d)" % self.mode)


class DsarArchive:
    """Random-access reader for a DSAR file.

    The block descriptor table is read eagerly (it is tiny); payloads are
    decompressed lazily and cached, so listing a directory's assets does not
    pay to inflate 70 MB of localisation data.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._fh: BinaryIO = open(path, "rb")
        try:
            self._read_header()
        except Exception:
            self._fh.close()
            raise

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()

    def __enter__(self) -> "DsarArchive":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- header ------------------------------------------------------------
    def _read_header(self) -> None:
        raw = self._fh.read(_HEADER.size)
        if len(raw) < _HEADER.size:
            raise DsarError("%s: truncated header (%d bytes)" % (self.path, len(raw)))
        magic, self.version, count, self.data_begin, _, _ = _HEADER.unpack(raw)
        if magic != MAGIC_U32:
            raise DsarError(
                "%s: bad magic %#010x, expected %#010x ('DSAR')"
                % (self.path, magic, MAGIC_U32))

        self.blocks: list[Block] = []
        desc = self._fh.read(count * _BLOCK.size)
        if len(desc) < count * _BLOCK.size:
            raise DsarError("%s: truncated block table" % self.path)
        for i in range(count):
            do, co, ds, cs, mode, _ = _BLOCK.unpack_from(desc, i * _BLOCK.size)
            self.blocks.append(Block(i, do, co, ds, cs, mode))

    def __repr__(self) -> str:
        return "<DsarArchive %r v%d blocks=%d decompressed=%d>" % (
            self.path, self.version, len(self.blocks), self.decompressed_size)

    @property
    def decompressed_size(self) -> int:
        if not self.blocks:
            return 0
        return max(b.dec_offset + b.dec_size for b in self.blocks)

    def compression_summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for b in self.blocks:
            out[b.compression] = out.get(b.compression, 0) + 1
        return out

    # -- payloads ----------------------------------------------------------
    def iter_blocks(self, gdeflate: Callable[[bytes, int], bytes] | None = None) -> Iterator[bytes]:
        """Yield each block's decompressed bytes, in order."""
        expect = 0
        for b in self.blocks:
            if b.dec_offset != expect:
                raise DsarError(
                    "block %d: dec_offset %d is not contiguous (expected %d)"
                    % (b.index, b.dec_offset, expect))
            self._fh.seek(b.comp_offset)
            raw = self._fh.read(b.comp_size)
            if len(raw) < b.comp_size:
                raise DsarError("block %d: truncated payload" % b.index)
            data = self._decompress(b, raw, gdeflate)
            if len(data) != b.dec_size:
                raise DsarError(
                    "block %d: got %d bytes, header promised %d"
                    % (b.index, len(data), b.dec_size))
            expect += b.dec_size
            yield data

    def _decompress(self, b: Block, raw: bytes, gdeflate) -> bytes:
        if b.mode == MODE_LZ4:
            try:
                import lz4.block
            except ImportError as e:  # pragma: no cover
                raise DsarError(
                    "block %d needs LZ4. Install it with: pip install lz4" % b.index) from e
            return lz4.block.decompress(raw, uncompressed_size=b.dec_size)
        if b.mode == MODE_GDEFLATE:
            if gdeflate is None:
                raise DsarError(
                    "block %d uses GDeflate, which needs a native decoder "
                    "(libdeflate-ng). Pass one via the `gdeflate=` argument." % b.index)
            return gdeflate(raw, b.dec_size)
        raise DsarError("block %d: unknown compression mode %d" % (b.index, b.mode))

    def read(self, gdeflate=None) -> bytes:
        """Decompress the whole archive into one bytes object.

        Convenient, but it allocates the full decompressed size (the
        localisation archive is ~70 MB) -- prefer :meth:`iter_blocks` for
        anything large.
        """
        out = bytearray()
        for chunk in self.iter_blocks(gdeflate):
            out += chunk
        return bytes(out)

    def read_stream(self, gdeflate=None) -> io.BufferedReader:
        """Return a file-like object over the decompressed archive.

        Uses a sliding window so that seeking (which :mod:`rcextract.toc`
        relies on) does not re-inflate from the start on every call.
        """
        return _BlockStream(self, gdeflate)


class _BlockStream(io.RawIOBase):
    """Seekable read-only stream over a DSAR's decompressed contents."""

    _WINDOW = 1 << 22  # 4 MiB

    def __init__(self, archive: DsarArchive, gdeflate=None) -> None:
        self._a = archive
        self._gdeflate = gdeflate
        self._pos = 0
        self._buf = b""
        self._buf_start = 0
        self._exhausted = False

    # -- helpers -----------------------------------------------------------
    def _block_at(self, pos: int) -> Block:
        for b in self._a.blocks:
            if b.dec_offset <= pos < b.dec_offset + b.dec_size:
                return b
        raise IndexError("offset %d is past end of archive" % pos)

    def _fill(self, pos: int) -> None:
        """Make sure `pos` falls inside the current buffer, else re-fill."""
        if self._buf_start <= pos < self._buf_start + len(self._buf):
            return
        blk = self._block_at(pos)
        self._a._fh.seek(blk.comp_offset)
        raw = self._a._fh.read(blk.comp_size)
        data = self._a._decompress(blk, raw, self._gdeflate)
        # opportunistically pull in following blocks to cut syscall churn
        buf = [data]
        have = len(data)
        nxt = blk.index + 1
        while have < self._WINDOW and nxt < len(self._a.blocks):
            nb = self._a.blocks[nxt]
            if nb.dec_offset != blk.dec_offset + have:
                break
            self._a._fh.seek(nb.comp_offset)
            nraw = self._a._fh.read(nb.comp_size)
            try:
                buf.append(self._a._decompress(nb, nraw, self._gdeflate))
            except Exception:
                break
            have += nb.dec_size
            nxt += 1
        self._buf = b"".join(buf)
        self._buf_start = blk.dec_offset

    # -- RawIOBase ---------------------------------------------------------
    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self._a.decompressed_size + offset
        else:
            raise ValueError("invalid whence %r" % (whence,))
        return self._pos

    def tell(self) -> int:
        return self._pos

    def read(self, size: int = -1) -> bytes:
        if self._pos >= self._a.decompressed_size:
            return b""
        if size is None or size < 0:
            size = self._a.decompressed_size - self._pos
        size = min(size, self._a.decompressed_size - self._pos)
        out = bytearray()
        remaining = size
        while remaining:
            self._fill(self._pos)
            i = self._pos - self._buf_start
            take = min(remaining, len(self._buf) - i)
            if take <= 0:
                break
            out += self._buf[i:i + take]
            self._pos += take
            remaining -= take
        return bytes(out)

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)

    def close(self) -> None:
        self._a.close()
        super().close()
