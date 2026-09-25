# rcextract

Get localisation strings out of an installed copy of **Ratchet & Clank: Rift
Apart** (PC) as plain text you can diff, review and hand to a translator — and
put a translation back as a mod file, without editing the game.

Pure Python. No game content, no anti-tamper, no network, no mod loader. Point
it at your own install.

## What it does

The game's text is locked in a 30 MB LZ4 archive that no normal tool can read.
`rcextract` reads it and gives you the 25,034 localisation keys of all 32
language slots as TSV or JSON, correctly labelled, in one command:

```bash
rcextract dump --game "$GAME" -l de -o de.tsv
```

Specifically, it:

* **decompresses** `d/localization` (281 LZ4 blocks → 70 MB) and finds the 32
  string-table containers inside it;
* **decodes** each container into `key → value` pairs;
* **identifies each container's language** by joining against the game's asset
  index — the one non-obvious step, and the one that is easy to get wrong
  (see below);
* **flags the four slots that ship empty** but can be filled, one of which is
  almost certainly Arabic;
* **verifies itself** against the game files, so you know the output is
  complete and correctly ordered.

It also **writes back**, as a mod:

```bash
rcextract mod install --game "$GAME" -t my_translation.json
```

That writes a `d\mods\mod1` archive and repoints four entries in the game's
asset index. It does **not** edit `d/localization` or any other game file in
place, and `rcextract mod uninstall` puts the index back byte-for-byte. See
[Installing a mod](#installing-a-mod).

The interesting part: **the game ships four language slots it never fills in**,
and one of them is almost certainly Arabic — the game has an Arabic voice
track and an Arabic logo, but no Arabic text. See [docs/FORMAT.md](docs/FORMAT.md).

```
$ rcextract list
game      : /games/Ratchet & Clank - Rift Apart
containers: 32

id   code     offset      name               keys   strings  notes
0    en-US    67999030    English (US)       25034  19144    voice-over variant
1    en-US    45073104    English (US)       25034  19144    base text variant; default for en_US installs
...
7    de       29985388    German             25034  18463
...
23   slot23   47353220    (unassigned)       25034  0        empty in retail builds
...
28   slot28   26520707    (unassigned)       25034  0        empty in retail builds
29   slot29   34575361    (unassigned)       25034  0        empty in retail builds
30   slot30   55340523    (unassigned)       25034  0        empty in retail builds

empty slots (no strings): 23, 28, 29, 30
  Reserved language slots. Six of the 33 language names the game carries have
  no text table, but only four slots are empty, so two of the six reuse a
  shipped slot (Canadian French -> fr, Mexican Spanish -> es-419).
  The four that need a slot of their own: Arabic, Indonesian, Thai, Vietnamese.
  Only Arabic has any real support in the build -- it ships a logo texture and
  a 560 MB voice bank. Filling an empty slot is how a new language gets added.
```

---

## Getting it

The source is the whole tool — one small Python package, no compiled
components, no game content. Clone it:

```bash
git clone https://github.com/nerdjb/rift-lang-extract.git
cd rift-lang-extract
pip install .
```

That gives you the `rcextract` command. To install without cloning first:

```bash
pip install git+https://github.com/nerdjb/rift-lang-extract.git
```

Without installing, run it straight from a checkout:

```bash
python -m rcextract list --game "$GAME"
```

**Requirements:** Python 3.9+ and one dependency, `lz4` (installed
automatically). `d/localization` is 281 blocks of LZ4, so it is required to
read any localisation data at all. If it is somehow absent the error says so:

```
DsarError: block 0 needs LZ4. Install it with: pip install lz4
```

`rcextract` needs no native decoder. 62 of the archives in `d/` use NVIDIA
GDeflate, but **the three an Arabic mod would have to touch are all pure LZ4**:
`d/localization` (281/281), `d/config` (48/48), `d/userinterface` (176/176).
GDeflate only appears in the texture and impostor archives, which a text mod
never opens.

To run the tests, or to regenerate the lump-type table from its upstream
source, see [docs/SOURCES.md](docs/SOURCES.md).

## Use

```bash
GAME='/path/to/Ratchet & Clank - Rift Apart'

rcextract list    --game "$GAME"          # every language slot
rcextract dump    --game "$GAME" -l de    # German -> TSV
rcextract dump    --game "$GAME" --all    # all 32 -> one JSON
rcextract get     --game "$GAME" -l de -k UI_WEAPONS
rcextract dump    --game "$GAME" -l 23    # empty slot -> blank template
rcextract toc     --game "$GAME"          # the asset index
rcextract verify  --game "$GAME"          # self-check every table

rcextract mod install   --game "$GAME" -t ar.json   # install a translation
rcextract mod uninstall --game "$GAME"             # remove it again
```

`--game` can be dropped if you are already in the install directory. Languages
resolve by id (`23`), BCP-47 code (`de`, `pt-BR`, `zh-Hant`, `es-419`) or
English name (`German`, `Brazilian`).

Without installing, `python -m rcextract <command>` works from a checkout.

### Output

`dump` writes TSV, one `KEY<TAB>value` per line, with a `LEN <n>` header —
a format CAT tools and spreadsheets handle, and which round-trips through
`diff` and `git`:

```bash
rcextract dump --game "$GAME" -l de -o de.tsv
rcextract dump --game "$GAME" -l 23 -o ar_template.tsv   # 25,034 blank rows
```

`dump --all` writes JSON instead, one object per language with the key list,
so it is easy to load into a script or a translation-memory pipeline.

### Verify your install

```bash
$ rcextract verify
ok   all 32 containers share one key list (25034 keys)
ok   28/32 slots carry strings
     counts range 18463..19144
ok   slot 23 is empty, as expected
ok   slot 28 is empty, as expected
ok   slot 29 is empty, as expected
ok   slot 30 is empty, as expected

RESULT: PASS
```

This is a real check, not a formality: it confirms the containers line up
with the table of contents, that no language is misidentified, and that no
table has duplicate or missing keys.

---

## How the extraction works

Three layers, described in full in [docs/FORMAT.md](docs/FORMAT.md). In brief:

1. **`d/localization` is a DSAR archive** — a block-compressed container whose
   281 blocks are all LZ4. Inflate it to one 70 MB buffer.
2. **That buffer is 32 `1TAD` containers back to back**, one per language slot.
   Each holds a 25,034-entry key/value string table. All 32 share a
   byte-identical key list; only the values differ.
3. **The `toc` file says which container is which language.** All 32 are
   registered under the same path hash, so the only thing separating them is
   the TOC *group*, and `group // 8` is the language id. Each asset also
   records the byte offset and size of its container, which is the join.

That third point is the one that catches people out: **the containers are not
stored in language order.** The first container in the blob is Czech
(language 24) and the last is Korean (language 10). Reading them off position
labels all 32 tables wrong. The tool always joins through the TOC and says so
if it cannot.

## What is not in this repo

**No game files, and no extracted game text.** `d/localization`, the `toc`, and
every TSV/JSON this tool produces are verbatim Sony/Insomniac content, and all
are excluded by `.gitignore`. You regenerate them from your own install with
the commands above.

The repository is source, format notes and tests — about 190 KB in total. The
occurrences of game words that remain are: the product name used nominatively,
and five single common words (`VÅBEN`, `VÅPEN`, `WAFFEN`, `ОРУЖИЕ`, `ARMAS`)
in the tests and the language table. Those last five earn their place — Danish
and Norwegian both translate the *name* of that string to the same word, so
"weapons" in each language is the only thing that tells the two slots apart,
and the test pins that so the table cannot be silently misidentified. See
[docs/SOURCES.md](docs/SOURCES.md#5-game-content) for the full note.

This matters beyond licensing: the archive alone is 30 MB and a full dump is
~50 MB per language, so committing it would make the repo unusable anyway.

## Installing a mod

`rcextract mod` writes a **mod file** — a `d\mods\mod1` archive plus a
`toc` with four entries repointed at it. It is what Overstrike does at install
time, reimplemented in Python because Overstrike is Windows-only.

**No game file is edited in place.** `d/localization` is never opened, written
or created; the `toc` is the only retail file touched, and it is backed up
beforehand and restored byte-for-byte on uninstall.

```bash
rcextract mod install --game "$GAME" -t my_translation.json
rcextract mod uninstall --game "$GAME"          # back to byte-identical
```

`-t` takes a JSON object or a `KEY<TAB>value` TSV. Untranslated keys are left
at offset 0, which is the format's own "fall back to English" sentinel, so a
partial translation is safe and shows English for the rest.

```bash
rcextract mod install --game "$GAME" -t ar.json --dry-run   # change nothing
rcextract mod install --game "$GAME" -t ar.json -s 23       # one slot, not four
rcextract mod install --game "$GAME" -t ar.json --keep-english
```

By default it fills **all four empty slots** — ids 23, 28, 29 and 30. They are
byte-identical and nothing in the shipped data records which one the game means
by Arabic, so writing the same text into each removes the need to guess. The
cost is a larger archive, nothing else. `-s` narrows it if you would rather
test one.

What it changes, asserted in `tests/test_mod.py`:

| | |
|---|---|
| files written | `d/mods/mod1`, `toc`, `toc.orig` |
| asset rows changed | exactly 4 (the localisation slots) |
| TOC lumps changed | exactly 2 of 8 |
| `d/localization` | untouched |
| `uninstall` | restores the `toc` to the identical 12,574,728 bytes |

**Unverified:** nobody has run the game with this. The five archive-descriptor
constants in `rcextract/mod.py` are copied from Overstrike rather than derived
from the game's own archives, and that is the one thing a run on real hardware
would confirm. Everything else — the TOC rewrite, the four repointed rows, the
containers, the byte-identical restore — is measured and tested. See
[docs/SOURCES.md](docs/SOURCES.md#mod-archives).

## Sources

**Where did the format knowledge come from?** Short version: the container
formats come from someone else; the localisation format does not exist upstream
and was recovered by inspecting the game files.

* The **DSAR archive**, **`1TAD` container** and **table of contents** layouts
  are taken from the MIT-licensed
  [ripped_apart](https://github.com/chaoticgd/ripped_apart) project, which
  documents these formats. No code is vendored or linked — the Python is an
  independent implementation written against its headers.
* `rcextract/lump_types.py` is **generated** by `tools/gen_lump_types.py` from
  `libra/lump_types.h` of that project, so the provenance is reproducible. The
  nine localisation lump CRCs are **not** in that table; they were recovered by
  inspection, and the generated file says so in its own docstring.
* Everything about the **string tables themselves** — which lumps hold keys
  versus values, the leading-NUL sentinel, the key hash, the `group // 8`
  language join, the 32-slot language table, the four empty slots — was found by
  reading the bytes, and is pinned by tests.
* The **mod-archive install layout** and the five archive-descriptor constants
  `rcextract mod` uses come from **Overstrike**, the Rift Apart mod loader. No
  code is taken; its behaviour is reimplemented. This is the one dependency that
  is trusted rather than measured, and
  [docs/SOURCES.md](docs/SOURCES.md#mod-archives) says exactly which numbers
  and why.

[docs/SOURCES.md](docs/SOURCES.md) has the full breakdown: which struct came
from which upstream header, how each localisation finding was established and
checked, and — importantly — **what is still inferred rather than proven**.

Ratchet & Clank: Rift Apart is a trademark of Sony Interactive Entertainment
Inc. This project is not affiliated with or endorsed by Sony Interactive
Entertainment or Insomniac Games. You must own a copy of the game to use it.
`rcextract` itself is MIT.

## Tests

```bash
python -m unittest discover -s tests -p 'test_*.py'                       # synthetic, no game
RCEXTRACT_GAME="$GAME" python -m unittest discover -s tests -p 'test_*.py'  # + 39 installer tests
```

The synthetic tests build localisation containers in memory from known
key/value lists and check the readers recover them exactly, including the
place round-trips usually break: `%d%%`, `&quot;`, `<span class=…>`, `<br>`,
`[BTN_A]` and CJK. They also check the *writer* rebuilds all 32 shipped
containers and the whole `toc` byte-for-byte, which is the precondition for
everything in `rcextract mod` — if editing the `toc` changed a byte we did not
mean to, no amount of care afterwards helps. The installer tests run against a
sandbox copy of the real `toc` and are skipped when no install is found.

Writing these found four real bugs: the `struct` layout for the DSAR block
descriptor is 32 bytes, not 40 (the `<` prefix suppresses padding), a shared
argparse parent made `--game` silently vanish when it appeared before the
subcommand, `open_toc` leaked a file handle, and a mod built from a translated
template silently leaked the template language's English into every key that
had not been translated. All four are now pinned by tests.

The provenance claim in [docs/SOURCES.md](docs/SOURCES.md) is itself tested:
`TestLumpTypeProvenance` asserts the nine localisation CRCs are disjoint from
the 78-entry upstream table, so the documented split cannot quietly become
false after a regeneration.

## Documentation

| Document | What it covers |
|---|---|
| This file | What the tool does, how to get it, how to use it |
| [docs/FORMAT.md](docs/FORMAT.md) | The on-disk format: DSAR, `1TAD`, the TOC, the 9 localisation lumps, the 32-slot language table, string shapes, and how to write a container or a `toc` back |
| [docs/SOURCES.md](docs/SOURCES.md) | Provenance of every claim: what came from ripped_apart, what from Overstrike, what was recovered by inspection, how each was checked, and what is still unproven |

## Licence

MIT — see [LICENSE](LICENSE).
