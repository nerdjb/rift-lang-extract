"""rcextract -- read Ratchet & Clank: Rift Apart (PC) localisation data.

Pure-Python readers for the three container formats the game uses:

* :mod:`rcextract.dsar` -- the DSAR archive that wraps a whole game directory.
* :mod:`rcextract.dat`  -- the ``1TAD`` asset container ("DAT") of lumps.
* :mod:`rcextract.toc`  -- the main table of contents that indexes every asset.

and :mod:`rcextract.lang` turns those into per-language key/value tables.

Nothing in this package contains game content.  Point it at your own
installed copy of the game.
"""

__version__ = "1.0.0"

__all__ = ["dsar", "dat", "toc", "lang", "lump_types", "languages", "cli"]
