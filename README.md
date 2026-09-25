# rcextract

Read localisation strings out of an installed copy of **Ratchet & Clank: Rift
Apart** (PC), as plain text you can diff, review and hand to a translator.

Pure Python, no game content, no anti-tamper, no network. Point it at your own
install; it reads 32 language tables out of `d/localization` and tells you
which is which.

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

## Install

```bash
git clone <this repo>
cd rift-lang-extract
pip install .
```

Python 3.9+. `pip install .` pulls in the one dependency, `lz4` — `d/localization`
is 281 blocks of LZ4, so it is required to read any localisation data. If it
is somehow absent the error says so:

```
DsarError: block 0 needs LZ4. Install it with: pip install lz4
```

Some *other* game archives (`d/config`, `d/userinterface`) use NVIDIA
GDeflate, which needs a native decoder. `rcextract` does not implement one and
does not need it — it only reads localisation data.

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

The repository is source, format notes, and tests — 436 KB in total. The
occurrences of game words that remain are: the product name used nominatively,
and five single common words (`VÅBEN`, `VÅPEN`, `WAFFEN`, `ОРУЖИЕ`, `ARMAS`)
in the tests and the language table. Those last five earn their place — Danish
and Norwegian both translate the *name* of that string to the same word, so
"weapons" in each language is the only thing that tells the two slots apart,
and the test pins that so the table cannot be silently misidentified.

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

## Attribution

* `rcextract/lump_types.py` is generated by `tools/gen_lump_types.py` from
  `libra/lump_types.h` of the MIT-licensed
  [ripped_apart](https://github.com/chaoticgd/ripped_apart) project, which
  documents the `1TAD` and DSAR layouts this tool reads. The nine
  localisation lump CRCs are **not** in that table; they were recovered by
  inspection, and the generator says so in the file.
* Ratchet & Clank: Rift Apart is a trademark of Sony Interactive
  Entertainment Inc. This project is not affiliated with or endorsed by Sony
  Interactive Entertainment or Insomniac Games. You must own a copy of the
  game to use it.

## Tests

```bash
python tests/test_formats.py       # 30 synthetic tests, no game needed
RCEXTRACT_GAME="$GAME" python tests/test_integration.py   # 14 against a real install
```

The synthetic tests build localisation containers in memory from known
key/value lists and check the readers recover them exactly, including the
place round-trips usually break: `%d%%`, `&quot;`, `<span class=…>`, `<br>`,
`[BTN_A]` and CJK. The integration tests are skipped when no install is found.

Writing these found two real bugs: the `struct` layout for the DSAR block
descriptor is 32 bytes, not 40 (the `<` prefix suppresses padding), and a
shared argparse parent made `--game` silently vanish when it appeared before
the subcommand. Both are now pinned by tests.

## Licence

MIT — see [LICENSE](LICENSE).
