#!/usr/bin/env python3
"""Build and install the Arabic localisation, with an Arabic-capable UI font.

This is the whole pipeline in one script: read the retail English string table,
shape the Arabic with HarfBuzz, rebuild Proxima Nova with the Arabic glyphs
added, and write both the strings and the two fonts into one mod archive.

Usage::

    pip install fonttools uharfbuzz
    python tools/build_arabic_mod.py --game "$GAME" translations/ar.json

Nothing in the game's own files is edited.  ``d/localization`` and
``d/userinterface`` are never opened for writing; the strings and the fonts go
into a new ``d/mods/mod1`` and the ``toc`` entries are repointed at it, which
:mod:`rcextract.mod`'s ``uninstall`` reverses.

Three things have to be true at once for Arabic to come out right, and this
script is the only place that knows all three:

1. the strings must be in a language slot the game will actually load, which
   includes the English one -- see ``--slots``;
2. the font must contain Arabic, and it must be the *UI* cut of Noto, because
   the game has no shaper and so cannot place GPOS-positioned mark components;
3. the strings must be pre-shaped, because the game maps codepoints through
   ``cmap`` and advances left to right with no bidi.

Add ``--dry-run`` to build and check everything without writing to the game.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rcextract.arabic import (PreShaper, ShapeError, UI_FONT_ASSETS,
                              arabic_codepoints, collect_runs,
                              extract_ui_fonts, font_arabic_codepoints,
                              is_arabic, tag_summary)
from rcextract.build import build_container
from rcextract.game import find_game_root
from rcextract.lang import StringTable
from rcextract.mod import Mod, ModError, TocImage, install

#: Where the Noto "UI" cuts live, by style.  Override with --noto.
NOTO_FONTS = {
    "regular": "/usr/share/fonts/noto/NotoSansArabicUI-Regular.ttf",
    "bold": "/usr/share/fonts/noto/NotoSansArabicUI-Bold.ttf",
}

#: Retail ships 32 slots.  These four exist but hold no text.  Slot 1 is en-US,
#: which has to be included: the game's language dropdown is not being patched,
#: so a new language is reached by having its strings sit where the game already
#: looks.
ARABIC_SLOTS = (0, 1, 23, 28, 29, 30)


def parse_slots(text: str):
    """Parse ``0,1,23`` into a tuple of ints."""
    return tuple(int(p) for p in text.replace(",", " ").split())


def parse_font(item: str):
    """Parse one ``STYLE=PATH`` pair, for ``action="append"``."""
    style, sep, path = item.partition("=")
    if not sep or not style or not path:
        raise ValueError("expected STYLE=PATH, not %r" % item)
    return style, path


def retail_fonts(root: str, override: dict) -> dict:
    """The retail Proxima Nova the Arabic is merged onto, refusing to double up.

    The game loads the font the table of contents points at, so if a previous run
    of this script is still installed then the extracted font is the one *this*
    tool wrote.  Merging onto it again is not a no-op -- the saved glyph names
    have been rewritten, so the presentation forms stop being findable and every
    shaped letterform gets a private-use codepoint instead.  Catch that here
    rather than shipping a subtly different font.
    """
    base = dict(override)
    for style, data in extract_ui_fonts(root).items():
        if style in base:
            continue
        already = font_arabic_codepoints(data)
        if already:
            raise SystemExit(
                "error: the installed %s UI font already contains %d Arabic "
                "codepoints, so a mod built by this tool is installed.\n"
                "Uninstall it first so the font is rebuilt from the retail one:\n"
                "  rcextract mod uninstall --game %r\n"
                "Or keep it and supply the retail font explicitly:\n"
                "  --base-font %s=/path/to/ProximaNova.ttf" %
                (style, len(already), root, style))
        base[style] = data
    missing = sorted(set(UI_FONT_ASSETS) - set(base))
    if missing:
        raise SystemExit("error: no font for style(s) %s" % ", ".join(missing))
    return base


def english_template(root: str) -> StringTable:
    """The retail English table, which every other table is built against.

    ``load_localization`` recovers language ids by elimination, so once a mod has
    repointed the slots its labels are not trustworthy.  Take the table the game
    itself verified as en-US, and fall back to the largest one -- the base
    English table is by far the biggest.
    """
    from rcextract.game import load_localization

    tables = load_localization(root)
    return next((t for t in tables if t.language_id == 1 and t.language_verified),
                max(tables, key=lambda t: t.translated_count))


def report_translation(table: StringTable, arabic: dict) -> None:
    keys = set(table.keys)
    unknown = sorted(k for k in arabic if k not in keys)
    print("translation: %d strings, %d not in the template's %d keys"
          % (len(arabic), len(unknown), len(keys)))
    for key in unknown[:5]:
        print("  ignoring %s" % key)
    with_arabic = sum(1 for v in arabic.values() if any(is_arabic(c) for c in v))
    print("  %d carry Arabic, %d are Latin or markup only"
          % (with_arabic, len(arabic) - with_arabic))
    print("  %d distinct Arabic codepoints; tags: %s"
          % (len(arabic_codepoints(arabic.values())), tag_summary(arabic.values())))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(__doc__.split("\n\n")[2:]))
    ap.add_argument("translation", help="JSON file: {key: arabic string}")
    ap.add_argument("--game", default=None,
                    help="install root (default: search from the working directory)")
    ap.add_argument("--noto", default=None, metavar="DIR",
                    help="directory holding NotoSansArabicUI-{Regular,Bold}.ttf "
                         "(default: %s)" % os.path.dirname(
                             NOTO_FONTS["regular"]))
    ap.add_argument("--base-font", type=parse_font, action="append",
                    metavar="STYLE=PATH",
                    help="use this Proxima Nova instead of the installed one "
                         "(repeat per style); needed if a previous build is "
                         "still installed")
    ap.add_argument("--slots", type=parse_slots, default=ARABIC_SLOTS,
                    help="language slots to fill (default: %s)"
                         % ",".join(str(s) for s in ARABIC_SLOTS))
    ap.add_argument("--out", default=None, metavar="DIR",
                    help="also write the fonts and the payload here")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and verify, but do not touch the game")
    args = ap.parse_args(argv)

    root = find_game_root(args.game)
    print("game: %s" % root)

    with open(args.translation, encoding="utf-8") as fh:
        arabic = json.load(fh)
    if not isinstance(arabic, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in arabic.items()):
        ap.error("%s must be a JSON object of {key: string}" % args.translation)
    texts = list(arabic.values())

    table = english_template(root)
    print("template: slot %s, %d keys, %d translated, %d flagged"
          % (table.language_id, table.key_count, table.translated_count,
             sum(1 for f in table.flags if f)))
    report_translation(table, arabic)

    # -- fonts ------------------------------------------------------------
    noto = dict(NOTO_FONTS)
    if args.noto:
        for style in noto:
            noto[style] = os.path.join(args.noto, os.path.basename(noto[style]))
    missing = [p for p in noto.values() if not os.path.isfile(p)]
    if missing:
        ap.error("missing Arabic source font(s):\n  %s\n"
                 "Install fonts-noto-core, or pass --noto DIR." % "\n  ".join(missing))

    print("\nfonts:")
    base = retail_fonts(root, dict(args.base_font or ()))
    shaper = PreShaper(base, noto)
    print("  shaping %d distinct Arabic runs, this takes a minute ..."
          % len(collect_runs(texts)))
    shaper.prepare(texts)
    print("  %d distinct letterforms: %d Arabic Presentation Forms, %d private-use"
          % (len(shaper.codepoints), len(shaper.presentation_form_codepoints),
             len(shaper.private_use_codepoints)))
    print("  every encoded codepoint resolves to a real glyph in every style ...",
          end=" ", flush=True)
    shaper.verify(texts)
    print("ok")

    for style in shaper.styles:
        data = shaper.font_bytes(style)
        original = base[style]
        was = (len(original) if isinstance(original, bytes)
               else os.path.getsize(original))
        print("  %-8s %7d bytes  (retail %7d)" % (style, len(data), was))

    # -- strings ----------------------------------------------------------
    print("\nstrings:")
    shaped = shaper.encode_all(arabic)
    payload = build_container(table.keys, shaped, dict(zip(table.keys, table.flags)))
    print("  payload %d bytes for slots %s"
          % (len(payload), ",".join(str(s) for s in args.slots)))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        for style in shaper.styles:
            path = os.path.join(args.out, "%s-arabic.ttf" % style)
            with open(path, "wb") as fh:
                fh.write(shaper.font_bytes(style))
            print("  wrote %s" % path)
        path = os.path.join(args.out, "localisation.bin")
        with open(path, "wb") as fh:
            fh.write(payload)
        print("  wrote %s" % path)
        with open(os.path.join(args.out, "arabic-shaped.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(shaped, fh, ensure_ascii=False, indent=1, sort_keys=True)
        print("  wrote %s"
              % os.path.join(args.out, "arabic-shaped.json"))

    if args.dry_run:
        print("\ndry run: nothing written to the game.")
        return 0

    # -- install ----------------------------------------------------------
    print("\ninstall:")
    image = TocImage.load(os.path.join(root, "toc"))
    loc = image.find_localization_assets()
    unknown_slots = [s for s in args.slots if s not in loc]
    if unknown_slots:
        ap.error("the toc has no localisation asset for slot(s) %s"
                 % ", ".join(map(str, unknown_slots)))
    try:
        mod = Mod(name="Arabic (pre-shaped) localisation + fonts")
        for slot in args.slots:
            mod.add(loc[slot], payload)
            print("  slot %-3d -> asset %d" % (slot, loc[slot]))
        for style, index in UI_FONT_ASSETS.items():
            mod.add(index, shaper.font_bytes(style))
            print("  font %-8s -> asset %d" % (style, index))
        for key, value in sorted(install(root, mod).items()):
            print("  %-20s %s" % (key, value))
    except ModError as exc:
        ap.error(str(exc))

    print("\nDone.  Launch the game, pick Arabic in the language menu and look at "
          "the front-end.\nUndo with:  rcextract mod uninstall --game %r" % root)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ShapeError as exc:
        sys.exit("error: %s" % exc)
