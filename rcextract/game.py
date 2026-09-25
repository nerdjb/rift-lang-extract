"""Locate and load the pieces of an installed Rift Apart (PC) copy.

Nothing here hardcodes a path: give it the install root (or the archive
itself) and it works out the rest.
"""

from __future__ import annotations

import os

from .dat import Dat, DatError, scan_containers
from .dsar import DsarArchive, DsarError
from .lang import StringTable, load_all
from .toc import Toc, TocError

#: The archive that holds every language's string table, relative to the
#: install root.
LOCALIZATION_REL = os.path.join("d", "localization")

#: The main table of contents, relative to the install root.
TOC_REL = "toc"

#: Directory names the Steam/Epic builds use, in the order we try them.
CANDIDATE_ROOTS = (
    "Ratchet & Clank - Rift Apart",
    "Ratchet & Clank Rift Apart",
    "Ratchet & Clank - Rift Apart (PC)",
)

#: Paths relative to a Steam library root that contain the game.
STEAM_SUFFIXES = (
    os.path.join("steamapps", "common", "Ratchet & Clank - Rift Apart"),
    os.path.join("steamapps", "common", "Ratchet & Clank Rift Apart"),
)


class GameNotFound(Exception):
    """Raised when no install root could be located."""


def find_game_root(start: str | None = None) -> str:
    """Find the game install root.

    Tries, in order: `start` itself, each known directory name inside `start`,
    then a few Steam suffixes.  Returns the directory that contains ``toc``
    and ``d/localization``.
    """
    start = start or os.getcwd()
    cands = [start]
    for name in CANDIDATE_ROOTS:
        cands.append(os.path.join(start, name))
    for suf in STEAM_SUFFIXES:
        cands.append(os.path.join(start, suf))
        cands.append(os.path.join(start, os.path.basename(suf), suf))

    for c in cands:
        if os.path.isfile(os.path.join(c, LOCALIZATION_REL)):
            return os.path.abspath(c)
    raise GameNotFound(
        "could not find a Rift Apart install under %r.\n"
        "Pass the game directory explicitly, e.g.:\n"
        "  rcextract list --game '/path/to/Ratchet & Clank - Rift Apart'\n"
        "It must contain a 'toc' file and a 'd/localization' file." % start)


def localization_path(root: str) -> str:
    return os.path.join(root, LOCALIZATION_REL)


def read_localization_blob(root: str, gdeflate=None) -> bytes:
    """Return the decompressed ``d/localization`` archive as bytes.

    Handles both layouts: a DSAR-wrapped archive, and a bare ``1TAD`` blob
    (possibly with a few bytes in front of the magic).
    """
    path = localization_path(root)
    if not os.path.isfile(path):
        raise GameNotFound("no such file: %s" % path)

    with open(path, "rb") as fh:
        head = fh.read(4)

    if head == b"DSAR":
        with DsarArchive(path) as ar:
            return ar.read(gdeflate)

    with open(path, "rb") as fh:
        buf = fh.read()
    for prefix in range(0, 16):
        if buf[prefix:prefix + 4] == b"1TAD":
            return buf[prefix:]
    return buf


def language_ids_from_toc(root: str, blob: bytes) -> list[int] | None:
    """Language id for each localisation container, in blob order.

    The table of contents is the only thing that says which container holds
    which language: all 32 localisation assets share one path hash, and are
    told apart by TOC *group*, where ``group // 8`` is the language id.  Each
    asset also records the byte offset and size of its container inside the
    decompressed archive, which is what this joins on.

    Returns None if the TOC is missing or does not line up with the archive,
    in which case callers should not guess.
    """
    try:
        assets = Toc.open(root).localization_assets()
    except (TocError, DatError, DsarError, GameNotFound, OSError):
        return None
    if not assets:
        return None

    mapping = {(a.offset, a.size): a.language_id for a in assets}
    ids: list[int] = []
    for c in scan_containers(blob):
        lid = mapping.get((c.base, c.file_size))
        if lid is None:
            return None
        ids.append(lid)
    return ids


def load_localization(root: str, gdeflate=None) -> list[StringTable]:
    """Read every language's string table from an installed game.

    Language ids are taken from the table of contents.  The containers are
    stored in a fixed order that is *not* the language order, so relying on
    blob position would mislabel every table.  Without a usable TOC the
    tables are still returned, but flagged so callers can say so.
    """
    blob = read_localization_blob(root, gdeflate)
    ids = language_ids_from_toc(root, blob)
    tables = load_all(blob, ids)
    if ids is None:
        for t in tables:
            t.language_verified = False
    # Return in language order.  The archive stores these in an unrelated
    # order, and callers almost always want "give me language N".
    tables.sort(key=lambda t: t.language_id)
    return tables


def open_toc(root: str) -> Dat:
    """Open the main table of contents as a ``1TAD`` container.

    The ``toc`` file is *not* DSAR-wrapped: it is a bare ``1TAD`` behind an
    8-byte header (a build hash) that sits in front of the magic.
    """
    path = os.path.join(root, TOC_REL)
    if not os.path.isfile(path):
        raise GameNotFound("no such file: %s" % path)
    with open(path, "rb") as fh:
        buf = fh.read()
    for prefix in range(0, 16):
        if buf[prefix:prefix + 4] == b"1TAD":
            return Dat(buf, prefix)
    raise DatError("%s does not contain a 1TAD container" % path)
