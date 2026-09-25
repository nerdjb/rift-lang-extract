"""Command line interface for :mod:`rcextract`.

Subcommands::

    rcextract list      --game DIR            what is in the archive
    rcextract dump      --game DIR --lang de  one language to TSV
    rcextract dump      --game DIR --all      every language to one JSON
    rcextract get       --game DIR --lang de --key UI_WEAPONS
    rcextract toc       --game DIR            inspect the table of contents
    rcextract verify    --game DIR            self-check the string tables
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from typing import Sequence

from . import __version__
from .dat import DatError
from .dsar import DsarError
from .game import GameNotFound, find_game_root, load_localization
from .lang import LocalizationError, StringTable
from .languages import EMPTY_SLOTS, resolve, unshipped_names
from .toc import Toc

PROG = "rcextract"


def _root(args) -> str:
    try:
        return find_game_root(args.game)
    except GameNotFound as e:
        _die(str(e))


def _die(msg: str, code: int = 1):
    print("%s: error: %s" % (PROG, msg), file=sys.stderr)
    raise SystemExit(code)


def _tables(args) -> list[StringTable]:
    try:
        return load_localization(_root(args))
    except (DsarError, LocalizationError, DatError) as e:
        _die(str(e))


# ---------------------------------------------------------------- subcommands
def cmd_list(args) -> int:
    root = _root(args)
    tables = _tables(args)
    print("game      : %s" % root)
    print("containers: %d" % len(tables))
    if not all(t.language_verified for t in tables):
        print("WARNING   : no usable 'toc'; language ids are blob positions, "
              "not real language ids")
    print()
    print("%-4s %-8s %-11s %-18s %-6s %-8s %s"
          % ("id", "code", "offset", "name", "keys", "strings", "notes"))
    for t in sorted(tables, key=lambda t: t.language_id):
        lang = t.language
        note = lang.note
        if not t.language_verified:
            note = (note + "; " if note else "") + "id unverified"
        print("%-4d %-8s %-11d %-18s %-6d %-8d %s"
              % (t.language_id, t.code, t.offset, lang.name[:18],
                 t.key_count, t.translated_count, note))
    print()
    empty = sorted(t.language_id for t in tables if not t.translated_count)
    if empty:
        print("empty slots (no strings): %s" % ", ".join(map(str, empty)))
        print("  Reserved language slots. The four languages with LANGUAGE_* string")
        print("  keys and a voice-over logo in the game config, but no text table")
        print("  in retail builds, are: %s." % unshipped_names())
        print("  Filling an empty slot is how a new language gets added.")
    return 0


class TsvUnsafeError(Exception):
    """A value contains a character that would break the TSV row format."""


def _write_tsv(t: StringTable, fh, template: bool = False) -> None:
    """Write a TSV.

    `template=True` writes every key with a blank value, which is the useful
    form for a language that has not been translated yet.

    A tab or newline inside a key or value would be indistinguishable from a
    row break, so this raises rather than rewriting the text.  Silently
    substituting such a character would corrupt a translation in a way that
    only shows up in game, and no shipped table contains one.
    """
    w = csv.writer(fh, delimiter="\t", lineterminator="\n",
                   quoting=csv.QUOTE_NONE, escapechar=None)
    fh.write("LEN %d\n" % t.key_count)
    rows = t.template() if template else t.items()
    for k, v in rows:
        for field, text in (("key", k), ("value", v)):
            if "\t" in text or "\n" in text or "\r" in text:
                raise TsvUnsafeError(
                    "%s %r contains a tab or newline, which TSV cannot represent. "
                    "Use `dump --all` (JSON) for this language." % (field, k))
        w.writerow([k, v])


def cmd_dump(args) -> int:
    tables = _tables(args)
    root = _root(args)

    if args.all:
        out = args.out or os.path.join(root, "rcextract_all.json")
        payload = []
        for t in tables:
            payload.append({
                "language_id": t.language_id,
                "code": t.code,
                "language": t.language.name,
                "key_count": t.key_count,
                "translated": t.translated_count,
                "entries": [[k, v] for k, v in t.items()],
            })
        _ensure_parent(out)
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        print("wrote %s (%d languages)" % (out, len(tables)))
        return 0

    try:
        lang = resolve(args.lang)
    except KeyError as e:
        _die(str(e))
    if lang.id >= len(tables):
        _die("language id %d is out of range (archive has %d containers)"
             % (lang.id, len(tables)))

    t = tables[lang.id]
    # An empty slot is a translation job waiting to be done, so hand back a
    # fillable template rather than a file with no rows in it.
    template = args.template or not t.translated_count
    out = args.out or os.path.join(root, "rcextract_%s.tsv" % t.code)
    _ensure_parent(out)
    with open(out, "w", encoding="utf-8", newline="") as fh:
        _write_tsv(t, fh, template=template)
    if template:
        print("wrote %s  (template: %d keys, 0 filled in -- %s)"
              % (out, t.key_count, t.code))
        if not args.template and not t.translated_count:
            print("  this slot is empty in this build, so every value is blank.")
            print("  This tool only reads; see the README for writing strings back.")
    else:
        print("wrote %s  (%d/%d strings, %s)"
              % (out, t.translated_count, t.key_count, t.code))
    return 0


def cmd_get(args) -> int:
    tables = _tables(args)
    try:
        lang = resolve(args.lang)
    except KeyError as e:
        _die(str(e))
    t = tables[lang.id]
    if args.key not in t:
        _die("no such key %r (table has %d keys)" % (args.key, t.key_count))
    v = t.get(args.key)
    if v is None:
        print("(untranslated in %s)" % t.code)
        return 1
    print(v)
    return 0


def cmd_toc(args) -> int:
    root = _root(args)
    try:
        toc = Toc.open(root)
    except (DsarError, DatError) as e:
        _die(str(e))
    if args.archives:
        for name in toc.archive_names():
            print(name)
        return 0
    for row in toc.archive_summary():
        print("%8d  %s" % (row[0], row[1]))
    print()
    print("assets: %d   groups: %d   archives: %d"
          % (len(toc.assets), len(toc.groups), len(toc.archives)))
    loc = toc.assets_with_hash(0xbe55d94f171bf8de)
    if loc:
        print("localisation assets: %d  (groups %d..%d, language id = group // 8)"
              % (len(loc), loc[0].group, loc[-1].group))
    return 0


def cmd_verify(args) -> int:
    """Self-check every table and report anything structurally suspicious."""
    tables = _tables(args)
    bad = 0

    keysets = {tuple(t.keys) for t in tables}
    if len(keysets) == 1:
        print("ok   all %d containers share one key list (%d keys)"
              % (len(tables), len(tables[0].keys)))
    else:
        print("FAIL key lists differ between containers")
        base = tables[0].keys
        for t in tables[1:]:
            if t.keys != list(base):
                print("     slot %d differs" % t.language_id)
                bad += 1

    for t in tables:
        problems = []
        if t.key_count != len(t.keys):
            problems.append("key_count %d != %d keys" % (t.key_count, len(t.keys)))
        if len(t.values) != t.key_count:
            problems.append("%d values for %d keys" % (len(t.values), t.key_count))
        if len(set(t.keys)) != len(t.keys):
            problems.append("duplicate keys")
        blanks = sum(1 for v in t.values if v == "")
        if blanks:
            problems.append("%d empty-string values" % blanks)
        if problems:
            print("FAIL slot %d (%s): %s" % (t.language_id, t.code, "; ".join(problems)))
            bad += 1

    filled = [t for t in tables if t.translated_count]
    print("ok   %d/%d slots carry strings" % (len(filled), len(tables)))
    if filled:
        print("     counts range %d..%d" % (min(t.translated_count for t in filled),
                                             max(t.translated_count for t in filled)))
    for sid in EMPTY_SLOTS:
        if sid < len(tables) and not tables[sid].translated_count:
            print("ok   slot %d is empty, as expected" % sid)

    print()
    print("RESULT: %s" % ("PASS" if not bad else "FAIL (%d problems)" % bad))
    return 1 if bad else 0


def _ensure_parent(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)


# ---------------------------------------------------------------- arg parsing
def build_parser() -> argparse.ArgumentParser:
    # Shared so that --game works both before and after the subcommand.
    # SUPPRESS matters: if the subparser had a real default it would overwrite
    # whatever the top-level parser already parsed, so `rcextract --game X
    # list` would silently lose X.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-g", "--game", metavar="DIR",
                        default=argparse.SUPPRESS,
                        help="game install directory (default: auto-detect from cwd)")

    p = argparse.ArgumentParser(
        prog=PROG,
        parents=[common],
        description="Read localisation strings out of an installed copy of "
                    "Ratchet & Clank: Rift Apart (PC).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Point --game at your install directory (the one containing "
               "'toc' and 'd/localization'), or run from inside it.")
    p.add_argument("--version", action="version", version="%s %s" % (PROG, __version__))

    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", parents=[common], help="show every language slot")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("dump", parents=[common], help="write one or all languages to disk")
    s.add_argument("-l", "--lang", default="en-US",
                   help="language id (0-31), code (de, pt-BR, ...) or name")
    s.add_argument("-a", "--all", action="store_true",
                   help="write every language into a single JSON file")
    s.add_argument("-t", "--template", action="store_true",
                   help="write every key with a blank value, as a fill-in template "
                        "(implied for slots that are empty in this build)")
    s.add_argument("-o", "--out", help="output path (default: inside the game dir)")
    s.set_defaults(fn=cmd_dump)

    s = sub.add_parser("get", parents=[common], help="print one string")
    s.add_argument("-l", "--lang", default="en-US")
    s.add_argument("-k", "--key", required=True)
    s.set_defaults(fn=cmd_get)

    s = sub.add_parser("toc", parents=[common], help="inspect the table of contents")
    s.add_argument("--archives", action="store_true",
                   help="list archive names instead of a summary")
    s.set_defaults(fn=cmd_toc)

    s = sub.add_parser("verify", parents=[common], help="self-check every string table")
    s.set_defaults(fn=cmd_verify)

    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not hasattr(args, "game"):
        args.game = None
    try:
        return args.fn(args)
    except (DsarError, DatError, LocalizationError, GameNotFound, TsvUnsafeError) as e:
        _die(str(e))
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
