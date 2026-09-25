"""Locate and load the pieces of an installed Rift Apart (PC) copy.

Nothing here hardcodes a path: give it the install root (or the archive
itself) and it works out the rest.
"""

from __future__ import annotations

import os

from .dat import Dat, DatError, scan_containers
from .dsar import DsarArchive, DsarError
from .lang import StringTable, load_all
from .toc import LOCALIZATION_ARCHIVE, Toc, TocError

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


def archive_path(root: str, archive_name: str) -> str:
    """Resolve a toc archive name to a path under `root`.

    Archive names are stored Windows-style (``d\\userinterface``) whatever
    platform the game is running on.
    """
    rel = archive_name.replace("\\", os.sep).replace("/", os.sep)
    return os.path.join(root, rel)


def read_asset(root: str, archive_name: str, offset: int, size: int) -> bytes:
    """Read one asset out of an archive, by byte offset and length.

    The table of contents stores ``(archive index, offset, size)`` per asset;
    this is the other half of :meth:`rcextract.toc.TocImage.set_asset_meta`, and
    reads back what a mod wrote.  Handles the two archive layouts the game
    uses: a DSAR-wrapped, block-compressed archive, and a flat uncompressed one
    (which is what a mod archive is).
    """
    path = archive_path(root, archive_name)
    if not os.path.isfile(path):
        raise GameNotFound("no such file: %s" % path)

    with open(path, "rb") as fh:
        magic = fh.read(4)

    if magic == b"DSAR":
        with DsarArchive(path) as ar:
            stream = ar.read_stream()
            stream.seek(offset)
            return stream.read(size)

    with open(path, "rb") as fh:
        fh.seek(offset)
        return fh.read(size)


def language_ids_from_toc(root: str, blob: bytes) -> list[int] | None:
    """Language id for each localisation container, in blob order.

    The table of contents is the only thing that says which container holds
    which language: all 32 localisation assets share one path hash, and are
    told apart by TOC *group*, where ``group // 8`` is the language id.  Each
    asset also records the byte offset and size of its container inside the
    decompressed archive, which is what this joins on.

    Assets that a mod has repointed out of ``d/localization`` no longer record
    where their container lives, so they cannot contribute to the join.  When
    a mod is installed by :mod:`rcextract.mod` that is exactly what happens to
    the four empty slots, and the containers are all still present in the
    archive.  The orphans are recovered by elimination: if the number of
    containers the toc cannot place equals the number of localisation assets
    that have been moved elsewhere, the two sets are the same set.  The
    pairing within them is arbitrary, which is why callers treat the result
    as unverified -- but the *set* of empty slots comes out right, which is
    what ``list``, ``dump`` and ``verify`` report.

    Returns None if the TOC is missing or does not line up with the archive,
    in which case callers should not guess.
    """
    try:
        toc = Toc.open(root)
    except (TocError, DatError, DsarError, GameNotFound, OSError):
        return None

    assets = toc.localization_assets()
    if not assets:
        return None

    resident: list = []
    moved: list = []
    for a in assets:
        if toc.archive_name(a.archive_index) == LOCALIZATION_ARCHIVE:
            resident.append(a)
        else:
            moved.append(a)

    mapping = {(a.offset, a.size): a.language_id for a in resident}
    placed: list[int | None] = []
    orphans = 0
    for c in scan_containers(blob):
        lid = mapping.get((c.base, c.file_size))
        placed.append(lid)
        if lid is None:
            orphans += 1

    if orphans:
        moved_ids = sorted(a.language_id for a in moved if a.language_id is not None)
        if orphans != len(moved_ids):
            # Either the archive does not match the toc, or something moved an
            # asset somewhere the toc still describes.  Do not guess.
            return None
        spare = iter(moved_ids)
        placed = [next(spare) if v is None else v for v in placed]
    elif moved:
        return None

    # Every container must have an id.  Filtering the Nones out instead would
    # quietly return a short list, and a short list mislabels every language
    # after the gap -- so fail loudly instead.
    if any(v is None for v in placed):
        return None
    return [v for v in placed if v is not None]


def moved_localization_slots(root: str) -> list[int]:
    """Language slots whose assets no longer live in ``d/localization``.

    Non-empty exactly when a mod has repointed them, which means the toc can
    no longer place their containers and the ids had to be recovered by
    elimination.  Callers use it to explain why a result is unverified.
    """
    try:
        toc = Toc.open(root)
    except (TocError, DatError, DsarError, GameNotFound, OSError):
        return []
    out = [a.language_id for a in toc.localization_assets()
           if toc.archive_name(a.archive_index) != LOCALIZATION_ARCHIVE]
    return sorted(i for i in out if i is not None)


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
    if ids is None or moved_localization_slots(root):
        # Ids recovered by elimination are right as a set, but the pairing
        # within the moved set is arbitrary, so this is not a verified join.
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
