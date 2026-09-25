# rcextract

Get localisation strings out of an installed copy of **Ratchet & Clank: Rift
Apart** (PC) as plain text you can diff, review and hand to a translator.

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

It does **not** write strings back — see [below](#writing-strings-back).

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
  Reserved language slots. The four languages with LANGUAGE_* string keys
  and a voice-over logo in the game config, but no text table in retail
  builds, are: Arabic, Indonesian, Thai, Vietnamese.
  Filling an empty slot is how a new language gets added.
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

Some *other* game archives (`d/config`, `d/userinterface`) use NVIDIA
GDeflate, which needs a native decoder. `rcextract` does not implement one and
does not need it — it only reads localisation data.

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

## Writing strings back

**Not implemented, deliberately.** Rebuilding a language means re-laying out
every container after it (they all shift), regenerating the offset tables
against a new value blob, patching the TOC's `offset`/`size` entries, and
re-compressing the DSAR. Two existing projects already do this and have the
dependency-graph machinery for it:

* [ripped_apart](https://github.com/chaoticgd/ripped_apart) — C, MIT, the
  reference for these formats
* **Overstrike** — the Rift Apart mod loader, which is what you would actually
  use to load a patched archive

`rcextract` is a reader. The format notes above are written to be enough for
someone to build the writer.

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
  versus values, the leading-NUL sentinel, the `group // 8` language join, the
  32-slot language table, the four empty slots — was found by reading the
  bytes, and is pinned by tests.

[docs/SOURCES.md](docs/SOURCES.md) has the full breakdown: which struct came
from which upstream header, how each localisation finding was established and
checked, and — importantly — **what is still inferred rather than proven**.

Ratchet & Clank: Rift Apart is a trademark of Sony Interactive Entertainment
Inc. This project is not affiliated with or endorsed by Sony Interactive
Entertainment or Insomniac Games. You must own a copy of the game to use it.
`rcextract` itself is MIT.

## Tests

```bash
python tests/test_formats.py       # 34 synthetic tests, no game needed
RCEXTRACT_GAME="$GAME" python tests/test_integration.py   # 16 against a real install
```

The synthetic tests build localisation containers in memory from known
key/value lists and check the readers recover them exactly, including the
place round-trips usually break: `%d%%`, `&quot;`, `<span class=…>`, `<br>`,
`[BTN_A]` and CJK. The integration tests are skipped when no install is found.

Writing these found three real bugs: the `struct` layout for the DSAR block
descriptor is 32 bytes, not 40 (the `<` prefix suppresses padding), a shared
argparse parent made `--game` silently vanish when it appeared before the
subcommand, and `open_toc` leaked a file handle. All three are now pinned by
tests.

The provenance claim in [docs/SOURCES.md](docs/SOURCES.md) is itself tested:
`TestLumpTypeProvenance` asserts the nine localisation CRCs are disjoint from
the 78-entry upstream table, so the documented split cannot quietly become
false after a regeneration.

## Documentation

| Document | What it covers |
|---|---|
| This file | What the tool does, how to get it, how to use it |
| [docs/FORMAT.md](docs/FORMAT.md) | The on-disk format: DSAR, `1TAD`, the TOC, the 9 localisation lumps, the 32-slot language table, string shapes. Written to be enough to build a *writer* |
| [docs/SOURCES.md](docs/SOURCES.md) | Provenance of every claim: what came from ripped_apart, what was recovered by inspection, how each was checked, and what is still unproven |

## Licence

MIT — see [LICENSE](LICENSE).
