"""Command line interface for :mod:`rcextract`.

Subcommands::

    rcextract list      --game DIR            what is in the archive
    rcextract dump      --game DIR --lang de  one language to TSV
    rcextract dump      --game DIR --all      every language to one JSON
    rcextract get       --game DIR --lang de --key UI_WEAPONS
    rcextract toc       --game DIR            inspect the table of contents
    rcextract verify    --game DIR            self-check the string tables
    rcextract mod install  --game DIR -t FILE install a localisation mod
    rcextract mod uninstall --game DIR        remove it again
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
from .game import (GameNotFound, find_game_root, load_localization,
                    moved_localization_slots)
from .lang import LocalizationError, StringTable
from .languages import (
    EMPTY_SLOTS,
    UNSHIPPED_LANGUAGES,
    resolve,
    shared_slot_names,
    unshipped_names,
)
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


def _warn(msg: str):
    print("%s: warning: %s" % (PROG, msg), file=sys.stderr)


def _tables(args) -> list[StringTable]:
    try:
        return load_localization(_root(args))
    except (DsarError, LocalizationError, DatError) as e:
        _die(str(e))


def _toc_root(args) -> str:
    """An install root for work that only needs the table of contents.

    Undoing a mod touches ``toc`` and the mod archive, and nothing else, so it
    should not insist on a full game install being present.
    """
    cand = getattr(args, "game", None) or os.getcwd()
    cand = os.path.abspath(cand)
    if os.path.isfile(os.path.join(cand, "toc")):
        return cand
    return _root(args)


# ---------------------------------------------------------------- subcommands
def cmd_list(args) -> int:
    root = _root(args)
    tables = _tables(args)
    print("game      : %s" % root)
    print("containers: %d" % len(tables))
    if not all(t.language_verified for t in tables):
        moved = moved_localization_slots(root)
        if moved:
            print("WARNING   : a mod has repointed slot(s) %s out of "
                  "d/localization." % ", ".join(str(i) for i in moved))
            print("            The toc can no longer say which container is "
                  "which, so those ids were")
            print("            recovered by elimination: right as a set, not a "
                  "verified join.")
        else:
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
        print("  Reserved language slots. Six of the 33 language names the game")
        print("  carries have no text table, but only %d slots are empty, so two of" % len(empty))
        print("  the six reuse a shipped slot (%s)." % shared_slot_names())
        print("  The %d that need a slot of their own: %s."
              % (len(UNSHIPPED_LANGUAGES), unshipped_names()))
        print("  Only Arabic has real support in the build: it ships a logo texture")
        print("  and a 560 MB voice bank. Filling a slot is how a language is added.")
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


def _refuse_if_guessed_slot(args, tables, lang, t) -> None:
    """Refuse to read a slot whose language id is a guess and has no text.

    An installed mod repoints localisation slots at its own archive, so the
    table of contents stops saying which container is which language and the
    ids come back recovered by elimination -- right as a set, but not a
    confirmed pairing.  That is survivable while every container still has
    text.  It is not survivable when the table you asked for comes back
    *empty*, because "empty" is exactly what the reader would report if the
    guess picked the wrong container, and the natural reading of that is
    "this language is untranslated".

    So this refuses instead of writing a 25,034-row file of blanks, which is
    the failure mode this guard exists to prevent: `dump -l en-US` after
    installing an Arabic mod handed back a blank template and said "fill it
    in".  Filling it in would have replaced English with an identical copy of
    English.
    """
    if t.translated_count or t.language_verified:
        return
    fullest = max(tables, key=lambda x: x.translated_count)
    _die(
        "slot %d (%s) has no strings, and its language id is a guess rather "
        "than a confirmed join -- a mod has repointed some slots out of "
        "d/localization, so 'no strings' here means 'wrong container', not "
        "'untranslated'.\n"
        "  This build does have text: slot %d holds %d strings, and is the "
        "one to read.\n"
        "  rcextract dump --game DIR -l %d -o out.json\n"
        "  Or uninstall the mod, which restores the confirmed join."
        % (lang.id, lang.code, fullest.language_id, fullest.translated_count,
           fullest.language_id))


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
                "verified": t.language_verified,
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
    _refuse_if_guessed_slot(args, tables, lang, t)
    # An empty slot is a translation job waiting to be done, so hand back a
    # fillable template rather than a file with no rows in it.
    template = args.template or not t.translated_count
    out = args.out or os.path.join(root, "rcextract_%s.tsv" % t.code)
    _ensure_parent(out)

    # The output extension picks the format, so a file called .json does not
    # quietly end up holding tab-separated text.  The JSON shape is the one
    # `rcextract mod install -t` reads, which makes dump -> edit -> install a
    # closed loop with no conversion step.
    if out.lower().endswith(".json"):
        payload = ({k: "" for k in t.keys} if template
                   else dict(t.items()))
        with open(out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        what = ("template" if template
                else "%d/%d strings" % (t.translated_count, t.key_count))
        print("wrote %s  (%s, %d keys, %s)" % (out, what, t.key_count, t.code))
        return 0

    with open(out, "w", encoding="utf-8", newline="") as fh:
        _write_tsv(t, fh, template=template)
    if template:
        print("wrote %s  (template: %d keys, 0 filled in -- %s)"
              % (out, t.key_count, t.code))
        if not args.template and not t.translated_count:
            print("  this slot is empty in this build, so every value is blank.")
            print("  Fill it in, then: rcextract mod install -t %s" % out)
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
    _refuse_if_guessed_slot(args, tables, lang, t)
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


# ------------------------------------------------------------------- mod
def _read_translations(path: str) -> dict[str, str]:
    """Load a translation file: TSV with a header, or a JSON object."""
    if path == "-":
        text = sys.stdin.read()
    else:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()

    if path.endswith(".json") or text.lstrip()[:1] in "{[":
        data = json.loads(text)
        if not isinstance(data, dict):
            _die("%s: expected a JSON object of key -> string" % path)
        # Blank is untranslated, exactly as it is in the TSV path below.  It
        # matters: `dump --template -o x.json` produces 25,034 empty values,
        # and installing that file untouched has to mean "fall back to
        # English", not "replace every string with nothing".
        return {k: v for k, v in data.items() if v}

    out: dict[str, str] = {}
    reader = csv.reader(text.splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE)
    try:
        header = next(reader)
    except StopIteration:
        return out
    if [h.strip().lower() for h in header[:2]] != ["key", "value"]:
        _die("%s: expected a TSV whose first two columns are 'key' and 'value'" % path)
    for lineno, row in enumerate(reader, start=2):
        if not row:
            continue
        if len(row) < 2:
            _die("%s:%d: expected two columns, got %d" % (path, lineno, len(row)))
        key, value = row[0], row[1]
        if "\t" in value or any(c in value for c in "\r\n"):
            _die("%s:%d: value for %r contains a tab or newline, which the "
                 "format cannot represent" % (path, lineno, key))
        if value != "":
            out[key] = value
    return out


def cmd_mod(args) -> int:
    from .build import BuildError, build_container
    from .mod import ModError, TocImage, build_mod, default_targets, install, uninstall

    if args.action == "uninstall":
        try:
            res = uninstall(_toc_root(args))
        except ModError as e:
            _die(str(e))
        if res["archive_removed"]:
            print("removed %s" % res["archive"])
        else:
            print("no mod archive was present at %s" % res["archive"])
        print("restored %s" % res["toc_restored"])
        return 0

    root = _root(args)

    # Everything below builds a mod.  The key set and the per-key flag bytes
    # come from a shipped container, so only the values are ours.
    try:
        tables = load_localization(root)
    except (DsarError, LocalizationError, DatError) as e:
        _die(str(e))
    template = tables[1] if len(tables) > 1 else tables[0]

    translations: dict[str, str] = {}
    if args.translations:
        try:
            translations = _read_translations(args.translations)
        except (OSError, ValueError) as e:
            _die("could not read %s: %s" % (args.translations, e))

    unknown = [k for k in translations if k not in template]
    if unknown:
        _die("%s: %d key(s) are not in the game's key list, starting with %r"
             % (args.translations, len(unknown), unknown[0]))

    # What the table looks like before our edits.  Only the key list and the
    # per-key flag bytes come from the template -- the values are what we are
    # replacing, and keeping them would make the mod "English with a few
    # Arabic strings in it".
    values = dict(zip(template.keys, template.values))
    if args.keep_english:
        # Off by default.  Useful for a first test where you want to confirm
        # the mod loads at all and only a handful of strings are Arabic.
        _warn("keeping %d English value(s) from the template; untranslated "
              "keys will read English rather than falling back to it"
              % sum(1 for v in values.values() if v is not None))
    else:
        values = dict.fromkeys(template.keys)
    values.update(translations)

    try:
        payload = build_container(template.keys, values, dict(zip(template.keys, template.flags)))
    except BuildError as e:
        _die(str(e))

    slots = tuple(int(s) for s in args.slot) if args.slot else default_targets()
    try:
        image = TocImage.load(os.path.join(root, "toc"))
        mod = build_mod(image, {s: payload for s in slots},
                        name=args.name, archive_rel=args.archive)
    except ModError as e:
        _die(str(e))

    if args.dry_run:
        print("would write %s (%d bytes) and repoint %d toc entries"
              % (os.path.join(root, *args.archive.split("\\")),
                 sum(len(p) for _, p in mod.assets), len(mod.assets)))
        print("slots: %s" % ", ".join(str(s) for s in slots))
        print("translated %d of %d keys" % (len(translations), len(template)))
        return 0

    try:
        info = install(root, mod, backup=not args.no_backup)
    except ModError as e:
        _die(str(e))

    print("wrote %s (%d bytes)" % (info["archive"], info["archive_bytes"]))
    print("repointed %d toc entries at archive index %d"
          % (len(info["assets"]), info["archive_index"]))
    print("slots: %s" % ", ".join(str(s) for s in slots))
    print("translated %d of %d keys; the rest fall back to English"
          % (len(translations), len(template)))
    if info["backup"]:
        print("toc backed up to %s" % info["backup"])
        print("remove the mod with:  rcextract mod uninstall --game %r" % root)
    return 0


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

    s = sub.add_parser("mod", parents=[common],
                       help="build and install a localisation mod")
    s.add_argument("action", choices=("install", "uninstall"))
    s.add_argument("-t", "--translations", metavar="FILE",
                   help="TSV (header 'key<TAB>value') or JSON object of translations; "
                        "'-' reads stdin.  Keys left out stay untranslated and fall "
                        "back to English")
    s.add_argument("-s", "--slot", action="append", metavar="ID",
                   help="language slot to claim; repeatable.  Defaults to all four "
                        "empty retail slots (23, 28, 29, 30)")
    s.add_argument("-n", "--name", default="rcextract localisation mod")
    s.add_argument("--keep-english", action="store_true",
                   help="keep the template language's existing values for keys you "
                        "did not translate, instead of leaving them untranslated")
    s.add_argument("--archive", default="d\\mods\\mod1",
                   help="path of the mod archive, relative to the install root")
    s.add_argument("--dry-run", action="store_true",
                   help="report what would change without writing anything")
    s.add_argument("--no-backup", action="store_true",
                   help="do not keep toc.orig (then uninstall cannot restore it)")
    s.set_defaults(fn=cmd_mod)

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
