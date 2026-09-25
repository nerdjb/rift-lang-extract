# rcextract

Get localisation strings out of an installed copy of **Ratchet & Clank: Rift
Apart** (PC) as plain text you can diff, review and hand to a translator — and
put a translation back as a mod file, without editing the game.

Pure Python. No game content, no anti-tamper, no network, no mod loader. Point
it at your own install.

```
rcextract list    # what languages exist, and which slots are empty
rcextract dump    # the strings, as TSV or JSON
rcextract get     # one string
rcextract toc     # the asset index
rcextract verify  # self-check every table
rcextract mod     # build and install a localisation mod

tools/build_arabic_mod.py   # build the Arabic mod: shaped strings + a new font
```

---

## Contents

- [What it does](#what-it-does)
- [Getting it](#getting-it)
- [Commands](#commands) — one section per tool
  - [`list`](#list) · [`dump`](#dump) · [`get`](#get) · [`toc`](#toc) · [`verify`](#verify) · [`mod`](#mod)
  - [`build_arabic_mod.py`](#build_arabic_modpy) — **start here for Arabic**
- [Installing a mod](#installing-a-mod)
- [End-to-end: German to Arabic](#end-to-end-german-to-arabic)
- [How it works](#how-the-extraction-works)
- [What is not in this repo](#what-is-not-in-this-repo)
- [Sources](#sources)
- [Tests](#tests)
- [Status: what is verified and what is not](#status-what-is-verified-and-what-is-not)
- [Licence](#licence)

---

## What it does

The game's text is locked in a 30 MB LZ4 archive that no normal tool can read.
`rcextract` reads it and gives you the 25,034 localisation keys of all 32
language slots as TSV or JSON, correctly labelled, in one command:

```bash
rcextract dump --game "$GAME" -l de -o de.tsv
```

Specifically, it:

- **decompresses** `d/localization` (281 LZ4 blocks → 70 MB) and finds the 32
  string-table containers inside it;
- **decodes** each container into `key → value` pairs;
- **identifies each container's language** by joining against the game's asset
  index — the one non-obvious step, and the one that is easy to get wrong
  (see [How it works](#how-the-extraction-works));
- **flags the four slots that ship empty** but can be filled, one of which is
  almost certainly Arabic;
- **verifies itself** against the game files, so you know the output is
  complete and correctly ordered;
- **writes back**, as a mod, without editing any game file in place.

It also installs a translation:

```bash
rcextract mod install --game "$GAME" -t my_translation.json
```

That writes a `d\mods\mod1` archive and repoints four entries in the game's
asset index. It does **not** edit `d/localization` or any other game file in
place, and `rcextract mod uninstall` puts the index back byte-for-byte. See
[Installing a mod](#installing-a-mod).

**Arabic needs more than that**, and this is the one place where "put the
translated strings in" is not sufficient. The game has no Arabic shaper and no
Arabic glyphs, so a mod that only swaps the strings renders Arabic as isolated
letters in reverse word order. `tools/build_arabic_mod.py` fixes that by shaping
the text with HarfBuzz at build time and shipping a patched font alongside it —
still entirely inside the mod, still no game file edited. Start at
[`build_arabic_mod.py`](#build_arabic_modpy).

The interesting part: **the game ships four language slots it never fills in**,
and one of them is almost certainly Arabic — the game has an Arabic voice bank
(`d\wem.ar`, 560 MB) and an Arabic logo texture, but no Arabic text. See
[docs/FORMAT.md](docs/FORMAT.md).

```
$ rcextract list
game      : /games/Ratchet & Clank - Rift Apart
containers: 32

id   code     offset      name               keys   strings  notes
0    en-US    67999030    English (US)       25034  19144    voice-over variant
1    en-US    45073104    English (US)       25034  19144    base text variant; default for en_US installs
2    en-GB    63479115    English (UK)       25034  18867    voice-over variant
3    da       27715300    Danish             25034  18463
4    nl       11723712    Dutch              25034  18463
5    fi       58919470    Finnish            25034  18463
6    fr       35769954    French             25034  18463
7    de       29985388    German             25034  18463
...
23   slot23   47353220    (unassigned)       25034  0        empty in retail builds
...
28   slot28   26520707    (unassigned)       25034  0        empty in retail builds
29   slot29   34575361    (unassigned)       25034  0        empty in retail builds
30   slot30   55340523    (unassigned)       25034  0        empty in retail builds
31   hr       48547813    Croatian           25034  18463

empty slots (no strings): 23, 28, 29, 30
  Reserved language slots. Six of the 33 language names the game
  carries have no text table, but only 4 slots are empty, so two of
  the six reuse a shipped slot (Canadian French, Mexican Spanish).
  The 4 that need a slot of their own: Arabic, Indonesian, Thai, Vietnamese.
  Only Arabic has real support in the build: it ships a logo texture
  and a 560 MB voice bank. Filling a slot is how a language is added.
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

**For Arabic, two more:** `fonttools` and `uharfbuzz`, plus the Noto Sans
Arabic UI fonts from your distribution (`fonts-noto-core` on Debian and Ubuntu).
They are an optional extra and nothing else needs them:

```bash
pip install '.[arabic]'
sudo apt install fonts-noto-core
```

### Pointing it at your install

Every command takes `--game DIR` / `-g DIR`, the directory containing `toc`
and `d/localization`. If you leave it off, `rcextract` walks up from the
current directory looking for those two files, so running it from inside the
install works too:

```bash
GAME='/path/to/SteamLibrary/steamapps/common/Ratchet & Clank - Rift Apart'
cd "$GAME" && rcextract list
```

On Linux, Proton and Wine installs work: `rcextract` only reads the files, so
it does not care what is between it and the disk.

### How languages are named

Anywhere a language is accepted, all of these work:

```bash
rcextract get -l 7      -k UI_WEAPONS   # slot number
rcextract get -l de     -k UI_WEAPONS   # BCP-47-ish code
rcextract get -l German -k UI_WEAPONS   # English name
```

`de` → `WAFFEN`, `7` → `WAFFEN`, `German` → `WAFFEN`, `pl` → `BROŃ`. Codes for
regional variants are `pt-BR`, `es-419`, `zh-Hant`, `zh-Hans`.

An unknown name is an error, not a guess:

```
$ rcextract get -l ar -k UI_WEAPONS
rcextract: error: "unknown language 'ar'"
```

`ar` is not a code in this build because Arabic has no container — see
[the four empty slots](#the-four-empty-slots).

---

## Commands

```
rcextract [-g DIR] [--version] {list,dump,get,toc,verify,mod} ...
```

### `list`

Every language slot, what is in it, and what is missing.

```bash
rcextract list --game "$GAME"
```

Columns are `id`, `code`, the byte `offset` of the container inside the
decompressed archive, `name`, how many `keys` it has, how many `strings` are
actually translated, and `notes`.

The `strings` count is the interesting one: it is the number of entries with a
non-zero value offset, i.e. genuinely translated. A retail build has 28 of 32
slots filled, ranging 18,463–19,144 strings out of 25,034 keys.

It also prints the empty-slot analysis described in
[the four empty slots](#the-four-empty-slots).

### `dump`

Write the strings out. This is the command you hand to a translator.

```bash
# one language as TSV
rcextract dump --game "$GAME" -l de -o de.tsv

# one language as JSON
rcextract dump --game "$GAME" -l de -o de.json

# every language into one JSON file
rcextract dump --game "$GAME" --all -o all.json

# a blank fill-in template for an empty slot
rcextract dump --game "$GAME" -l 23 --template -o translations/ar.json
```

| flag | meaning |
|---|---|
| `-l`, `--lang` | language id, code or name |
| `-a`, `--all` | every language into one JSON file |
| `-t`, `--template` | every key with a blank value, ready to fill in |
| `-o`, `--out` | output path (default: inside the game dir) |

**TSV output** has a `LEN <n>` header and one `KEY<TAB>value` per line — a
format CAT tools and spreadsheets handle, and which round-trips through `diff`
and `git`:

```
LEN 25034
ACCESS_LEGAL_PC_GLOBAL	Mit dem Einschalten dieser Einstellungen bist du damit einverstanden, dass …
ACTIVITY_CARD_CAT_ARENA	Arenaplex
ACTIVITY_CARD_CAT_ARENA_BRONZE	Bronzepokal
```

**The output extension picks the format.** `-o x.json` writes real JSON, a
`{key: value}` object — the same shape `rcextract mod install -t` reads, so
dump → edit → install needs no conversion step. `-o x.tsv` writes TSV.

An empty slot gives you a template automatically, because a file with no rows
in it is not a useful thing to hand anyone:

```
$ rcextract dump -l 23 -o ar.json
wrote ar.json  (template, 25034 keys, slot23)
  this slot is empty in this build, so every value is blank.
  Fill it in, then: rcextract mod install -t ar.json
```

Blank values mean *untranslated*, not *empty string* — see
[the offset-0 sentinel](docs/FORMAT.md#the-offset-0-sentinel). Installing a
blank template untouched is therefore a no-op that leaves English in place,
rather than blanking all 25,034 strings.

### `get`

One string, printed. Useful in scripts and for checking a single entry.

```bash
rcextract get --game "$GAME" -l de -k UI_WEAPONS
```

```
WAFFEN
```

An untranslated key says so and exits non-zero, rather than printing an empty
line you would mistake for an empty translation:

```
$ rcextract get --game "$GAME" -l 23 -k UI_WEAPONS
(untranslated in slot23)
$ echo $?
1
```

Any key that is not in the 25,034-key list is an error too.

### `toc`

The game's asset index: 340,662 assets across 147 archives in 256 groups.

```bash
rcextract toc --game "$GAME"              # top archives by asset count
rcextract toc --game "$GAME" --archives   # every archive name, in order
```

```
   23997  d\animsetclips
   14696  d\wem.us
   14515  d\perfsetclips
   13747  d\wem.ar
   13747  d\wem.br
```

All 147 names, in order. This is also how you can see a mod's archive appear
after installing — `d\mods\mod1` becomes the 148th line:

```
$ rcextract toc --game "$GAME" --archives | tail -2
d\wem_00          # before installing
d\mods\mod1      # after
```

### `verify`

A real self-check, not a formality. It confirms the containers line up with the
table of contents, that no language is misidentified, and that no table has
duplicate or missing keys.

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

Run this first if anything looks wrong. It is the fastest way to tell "my
install is unusual" apart from "the tool is wrong".

### `mod`

Builds and installs a localisation mod. Covered in full in
[Installing a mod](#installing-a-mod).

```bash
rcextract mod install   --game "$GAME" -t ar.json
rcextract mod uninstall --game "$GAME"
rcextract mod install   --game "$GAME" -t ar.json --dry-run
```

That installs the *strings*. It is enough for German, French or Japanese, because
the game's own font already has those glyphs. **For Arabic it is not enough**, and
the reason is worth stating plainly: the engine has no Arabic shaper. See below.

### `build_arabic_mod.py`

This is the tool that makes Arabic actually appear as Arabic, and **it is what
you want for Arabic** — not `rcextract mod install`.

```bash
python -m pip install fonttools uharfbuzz lz4     # or: pip install .[arabic]
sudo apt install fonts-noto-core                  # Noto Sans Arabic UI

GAME='/path/to/Ratchet & Clank - Rift Apart'
python tools/build_arabic_mod.py --game "$GAME" ar.json
```

#### How to make the Arabic fonts show

Four things have to be true at once, and missing any one of them is what makes
Arabic render as boxes, as disconnected letters, or backwards.

**1. The strings have to arrive pre-shaped.** Rift Apart maps every codepoint
through the font's `cmap` and advances the pen left to right. There is no
HarfBuzz call, no bidi pass, no `direction` property — Arabic in the stock
pipeline draws as isolated letters in reverse word order, which is what
"disconnected and backwards" means. So the shaping is done **at build time** and
the mod ships the *result*: the tool runs HarfBuzz over every Arabic run itself,
and writes back one codepoint per shaped glyph, already in visual order. The
engine then has nothing left to get wrong.

**2. The font has to contain those shaped glyphs.** Proxima Nova — the two
assets the game loads for UI text — has no Arabic codepoint in it at all, so a
new font has to ship alongside the new strings. The tool merges
[Noto Sans Arabic UI](https://fonts.google.com/noto/specimen/Noto+Sans+Arabic+UI)
into Proxima Nova rather than replacing it, so the game's Latin and symbol glyphs
keep their original shapes and metrics. The "UI" cut specifically, not plain
Noto Naskh or Noto Sans: only the UI cut's Arabic glyphs have self-contained
single-glyph forms, which is what lets each one be addressed by its own
codepoint.

**3. Every shaped glyph needs a codepoint, including the ones Unicode has no
name for.** 138 distinct letterforms turned up across the 716 strings. 86 of
them have a codepoint in the Arabic Presentation Forms block, U+FE70–U+FEFF, and
those are used. The other 52 have none, so they are given new codepoints in the
**Supplementary Private Use Area-A**, U+F0000–U+FFFFD, and appended to the
font's `cmap`. The BMP private-use areas are *not* usable: Proxima Nova already
occupies U+E000–U+E800, U+F000–U+F800 and U+0100–U+0200.

The same codepoint is used for a shaped glyph in both weights, paired by glyph
*name* rather than by glyph id, so Regular and Bold do not have to agree on
glyph order. Both patched fonts are `unitsPerEm=1000` like the originals, and
keep the original name records.

**4. The run reversal has to respect word boundaries.** A space bundled into a
neighbouring Latin run travels to the wrong side of it when the runs are
reversed — `PULSE 3D™` comes back as `3D™ PULSE`, and the gap between a number
and the word it follows ends up on the far side of the number. So a gap is
broken out as a run of its own exactly when Arabic is touching it, and left
inside the run when it is only separating two pieces of one Latin phrase.

#### The font input is not optional

The tool needs the **retail** Proxima Nova, because `extract_ui_fonts` reads
whatever the `toc` points at — and once a mod is installed that is the *patched*
font, so rebuilding on top of it would quietly degrade (all 139 letterforms land
in the PUA instead of 86 presentation forms plus 52 PUA). The tool detects this
and refuses:

```bash
# retail fonts, extracted once from the pristine toc
python tools/build_arabic_mod.py --game "$GAME" ar.json \
    --base-font regular=regular-arabic.ttf --base-font bold=bold-arabic.ttf
```

To get them, read the two assets out of the original archive with the toc's own
`(offset, size)` — `rcextract.toc.Toc.open("$GAME/toc.orig")`, assets **47779**
(regular) and **75673** (bold), both bare TTFs rather than containers. Keep them
somewhere outside the repo; they are game content.

#### Options

| flag | meaning |
| --- | --- |
| `--game DIR` | the install (found by searching upward if omitted) |
| `--noto DIR` | where the Noto Sans Arabic UI cuts are, default `/usr/share/fonts/noto` |
| `--base-font STYLE=PATH` | the retail font to build on, instead of the installed one; repeatable, needs `regular` and `bold` |
| `--slots` | language slots to claim; default `0,1,23,28,29,30` |
| `--out DIR` | *also* write the fonts, the payload and the shaped strings here |
| `--dry-run` | build and verify everything, write nothing to the game |

Builds are reproducible: set `SOURCE_DATE_EPOCH` and two runs differ only in the
font `head` table's timestamp.

#### What pre-shaping costs you

Display is correct. Layout is not, and cannot be:

- **The caret and selection are wrong.** The engine sees one codepoint per
  shaped glyph, not one per character, so a cursor steps over whole ligatures and
  marks. This matters in text-entry fields.
- **Wrapping measures glyph boxes, not characters**, so a line can break in a
  place a reader would not choose, especially inside a word.
- **Kashida and justification cannot be added at render time**, because the
  engine is not doing the rendering.
- **No live re-shaping.** Changing a string means rebuilding the mod.

That is the price of working inside an engine with no shaper, and it is the
right trade for a front-end: menus, subtitles and item descriptions are
read-only, and those are the overwhelming majority of what a player sees.

#### Layout limitations are inherent to the approach

The font carries no Arabic OpenType tables. Glyph *substitution* works, because
that is now just a `cmap` lookup, but anything that needs the shaper at render
time cannot work:

- `liga`, `rlig` and the joining forms are already resolved into distinct
  glyphs, so there is nothing left for them to do;
- mark positioning is baked into the glyph the shaper chose, and is not
  re-adjustable;
- nothing in the font can respond to `size`, `lang` or `script` runs.

---

## Installing a mod

`rcextract mod` writes a **mod file** — a `d\mods\mod1` archive plus a `toc`
with four entries repointed at it. It is what
[Overstrike](https://github.com/Tkachov/Overstrike) does at install time,
reimplemented in Python because Overstrike is Windows-only and .NET 7.

**No game file is edited in place.** `d/localization` is never opened, written
or created; the `toc` is the only retail file touched, and it is backed up to
`toc.orig` beforehand and restored byte-for-byte on uninstall.

The Arabic tool described in
[`build_arabic_mod.py`](#build_arabic_modpy) is built on the same installer and
follows the same rule. It repoints eight entries rather than four — the four
reserved slots *and* the two en-US ones, so the text is reachable — plus the two
UI font assets, which is the same operation applied to a different kind of
asset.

### install

```bash
rcextract mod install --game "$GAME" -t translations/ar.json
```

```
wrote /games/Ratchet & Clank - Rift Apart/d/mods/mod1 (5035180 bytes)
repointed 4 toc entries at archive index 147
slots: 23, 28, 29, 30
translated 852 of 25034 keys; the rest fall back to English
toc backed up to /games/Ratchet & Clank - Rift Apart/toc.orig
remove the mod with:  rcextract mod uninstall --game '/games/Ratchet & Clank - Rift Apart'
```

| flag | meaning |
|---|---|
| `-t`, `--translations` | TSV or JSON of `key → value`; `-` reads stdin |
| `-s`, `--slot` | language slot to claim; repeatable. Default: all four empty slots |
| `-n`, `--name` | mod name recorded in the archive |
| `--archive` | path of the mod archive, relative to the install root |
| `--keep-english` | keep the template language's values for untranslated keys |
| `--dry-run` | report what would change, write nothing |
| `--no-backup` | do not write `toc.orig` (then uninstall cannot restore) |

Check first, then commit:

```bash
rcextract mod install --game "$GAME" -t ar.json --dry-run
```

```
would write /games/.../d/mods/mod1 (5035180 bytes) and repoint 4 toc entries
slots: 23, 28, 29, 30
translated 852 of 25034 keys
```

### The four empty slots

By default it fills **all four** empty slots — ids 23, 28, 29 and 30.

They are byte-identical and nothing in the shipped data records which one the
game means by Arabic, so writing the same text into each removes the need to
guess. The cost is a larger archive, nothing else.

To test just one:

```bash
rcextract mod install --game "$GAME" -t ar.json -s 23
```

To work out which slot is which, write a *different* marker into each and
launch the game — whatever marker appears names the Arabic slot. That is a
one-launch experiment that answers the question for good.

### Translation files

Two formats, both plain text so they diff and merge normally. See
[translations/README.md](translations/README.md).

**JSON** — an object of key to string:

```json
{
  "MENU_DIFFICULTY_TITLE": "الصعوبة",
  "UI_WEAPONS": "الأسلحة"
}
```

**TSV** — `key<TAB>value` with a `key<TAB>value` header:

```
key	value
MENU_DIFFICULTY_TITLE	الصعوبة
UI_WEAPONS	الأسلحة
```

Rules:

- **Keys you leave out stay untranslated** and fall back to English. A partial
  translation is safe to install while it is still in progress.
- **A blank value means untranslated**, not empty string — matching TSV and
  JSON, so a blank template is a no-op rather than 25,034 empty strings.
- **A key that is not in the game's list is rejected**, naming it, rather than
  being silently dropped. A typo cannot quietly cost you a translation.
- **Tabs and newlines in a value are rejected** in TSV, because the format
  cannot represent them.

### What it changes

Asserted in `tests/test_mod.py`, not just claimed:

| | |
|---|---|
| files written | `d/mods/mod1`, `toc`, `toc.orig` |
| asset rows changed | exactly 4 — the localisation slots |
| TOC lumps changed | exactly 2 of 8 |
| `d/localization` | untouched |
| archive layout | flat concatenation, no DSAR wrapper, no block directory |
| `uninstall` | restores the `toc` to the identical 12,574,728 bytes |

That last row matters: it means the change is reversible without a backup of
the whole game, and without Steam re-downloading 42 GB.

### uninstall

```bash
rcextract mod uninstall --game "$GAME"
```

```
removed /games/Ratchet & Clank - Rift Apart/d/mods/mod1
restored /games/Ratchet & Clank - Rift Apart/toc
```

This deletes the mod archive and copies `toc.orig` back over `toc`. It refuses
if there is no `toc.orig`, rather than leaving you with a patched `toc` and no
way back.

### After installing, the reader still works

Installing repoints four assets at a different archive, and the reader
identifies each container's language by joining container offsets against the
`toc`. A naive join stops matching, and `list` starts reporting blob positions
as language ids — which silently mislabels all 32 tables and moves the
"empty slots" line to the wrong four numbers.

`rcextract` handles this: assets that no longer live in `d/localization` are
excluded from the join, and the containers they lost track of are recovered by
elimination. `list`, `dump` and `verify` keep working, and say so:

```
WARNING   : a mod has repointed slot(s) 23, 28, 29, 30 out of d/localization.
            The toc can no longer say which container is which, so those ids were
            recovered by elimination: right as a set, not a verified join.
```

The empty-slot set stays `23, 28, 29, 30`. This was a real bug, found by
running `list` after an install and noticing it contradict the documented
answer; `ReaderSurvivesAMod` now pins it.

---

## End-to-end: German to Arabic

```bash
GAME='/path/to/Ratchet & Clank - Rift Apart'

# 1. check the install is normal
rcextract verify --game "$GAME"

# 2. get German as a starting point, and a reference key list
rcextract dump --game "$GAME" -l de  -o de.tsv
rcextract dump --game "$GAME" -l 23 --template -o translations/ar.json

# 3. translate translations/ar.json  (or de.tsv -> ar.tsv, same format)

# 4. check what will happen
python tools/build_arabic_mod.py --game "$GAME" translations/ar.json --dry-run

# 5. install
python tools/build_arabic_mod.py --game "$GAME" translations/ar.json

# 6. launch the game, look at the front-end

# 7. if it is wrong
rcextract mod uninstall --game "$GAME"
```

Step 4 needs the retail UI fonts the first time; see
[`build_arabic_mod.py`](#build_arabic_modpy). Step 5 refuses to run if a patched
font is already installed, so run step 7 before rebuilding.

**Use `build_arabic_mod.py`, not `rcextract mod install`, for Arabic.**
`rcextract mod install` only writes the strings, and Arabic needs a new font and
pre-shaped text as well. `rcextract mod install` is the right tool for every
other language, and the right tool for Arabic if you have already shaped the
strings and built the fonts yourself.

Step 6 is the one step this project cannot do for you. See
[Status](#status-what-is-verified-and-what-is-not).

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
(language 24) and the last is English (US). Reading them off position labels
all 32 tables wrong. The tool always joins through the TOC and says so if it
cannot.

### Writing, and why there is no compressor

A mod does not rewrite `d/localization`. It writes a new archive,
`d\mods\mod1`, holds the new containers there, and repoints four `toc` entries
at it. So there is nothing to re-lay-out, no DSAR to recompress, and **no LZ4
encoder anywhere in the project** — the one dependency is a decoder.

A mod archive is flat: payloads concatenated with no block directory, so the
`toc`'s `(offset, size)` address the payload directly.

The writer is exact. Rebuilding all 32 shipped containers with no edits returns
the identical bytes, and so does the whole `toc` — 12,574,728 bytes including
284 bytes of slack past the declared length. Those round-trips are pinned by
tests, because if editing the `toc` changed a byte we did not mean to, no
amount of care afterwards would help.

---

## What is not in this repo

**No game files, and no extracted game text.** `d/localization`, the `toc`, and
every TSV/JSON this tool produces are verbatim Sony/Insomniac content, and all
are excluded by `.gitignore`. You regenerate them from your own install with
the commands above.

The repository is source, format notes and tests — about 290 KB in total. The
occurrences of game words that remain are: the product name used nominatively,
and five single common words (`VÅBEN`, `VÅPEN`, `WAFFEN`, `ОРУЖИЕ`, `ARMAS`)
in the tests and the language table. Those last five earn their place — Danish
and Norwegian both translate the *name* of that string to the same word, so
"weapons" in each language is the only thing that tells the two slots apart,
and the test pins that so the table cannot be silently misidentified. See
[docs/SOURCES.md](docs/SOURCES.md#6-game-content) for the full note.

This matters beyond licensing: the archive alone is 30 MB and a full dump is
~50 MB per language, so committing it would make the repo unusable anyway.

Your own translations are the exception — put them in
[translations/](translations/) and they are versioned, because they are yours.
`*.tsv` and `*.json` are ignored everywhere else in the repo precisely so that
a stray `rcextract` dump cannot be committed by accident.

That includes the Arabic. A 716-key Arabic translation and the two retail fonts
it is built on are game-derived and translation content respectively, and
neither is in the repository — `tools/build_arabic_mod.py` takes the translation
as a path argument, so you point it at your own file. The **shaped** output it
produces is not game content and would be safe to commit, but it is a build
artefact derived from a translation, so it is left out too; `--out DIR` writes
it wherever you want it.

**No game fonts either.** The two retail Proxima Nova files are game content and
are not in the repo. If you want to build the Arabic mod you need your own copy,
extracted from your own install as described in
[`build_arabic_mod.py`](#build_arabic_modpy).

---

## Sources

**Where did the format knowledge come from?** Short version: the container
formats come from someone else; the localisation format does not exist upstream
and was recovered by inspecting the game files; the mod-archive layout comes
from Overstrike.

- The **DSAR archive**, **`1TAD` container** and **table of contents** layouts
  are taken from the MIT-licensed
  [ripped_apart](https://github.com/chaoticgd/ripped_apart) project, which
  documents these formats. No code is vendored or linked — the Python is an
  independent implementation written against its headers.
- `rcextract/lump_types.py` is **generated** by `tools/gen_lump_types.py` from
  `libra/lump_types.h` of that project, so the provenance is reproducible. The
  nine localisation lump CRCs are **not** in that table; they were recovered by
  inspection, and the generated file says so in its own docstring.
- Everything about the **string tables themselves** — which lumps hold keys
  versus values, the leading-NUL sentinel, the key hash, the `group // 8`
  language join, the 32-slot language table, the four empty slots — was found
  by reading the bytes, and is pinned by tests.
- The **mod-archive install layout** and the five archive-descriptor constants
  `rcextract mod` uses come from **Overstrike**. No code is taken; its
  behaviour is reimplemented, and the five numbers are checked field by field
  against `TOC_I29.AddNewArchive`. This is the one dependency that is trusted
  rather than measured — see
  [docs/SOURCES.md](docs/SOURCES.md#4-mod-archives).
- The **Arabic glyph shapes** come from
  [Noto Sans Arabic UI](https://fonts.google.com/noto/specimen/Noto+Sans+Arabic+UI)
  (SIL Open Font License 1.1), merged into the game's own Proxima Nova. The
  shaping itself is done by **HarfBuzz** through the `uharfbuzz` binding, and the
  fonts are edited with **fontTools** (both MIT). Both are optional extras:
  reading the game's files needs neither.
- The claim that **the engine does not shape Arabic** is not taken from anyone's
  word. It is a conclusion from the format work: the string tables are a flat
  list of key/value pairs with no run, script or direction information anywhere
  in them, and the font has no Arabic codepoints, so there is nothing for a
  shaper to act on. The last 140 MB of the install does contain HarfBuzz and
  FreeType, inside `RenoirCore.WindowsDesktop.dll` — but the engine never calls
  the shaper for this text path, which is why the mod pre-shapes instead.

[docs/SOURCES.md](docs/SOURCES.md) has the full breakdown: which struct came
from which upstream header, how each finding was established and checked, and —
importantly — **what is still inferred rather than proven**.

Ratchet & Clank: Rift Apart is a trademark of Sony Interactive Entertainment
Inc. This project is not affiliated with or endorsed by Sony Interactive
Entertainment or Insomniac Games. You must own a copy of the game to use it.
`rcextract` itself is MIT.

---

## Tests

```bash
python -m unittest discover -s tests -p 'test_*.py'                        # synthetic, no game
RCEXTRACT_GAME="$GAME" python -m unittest discover -s tests -p 'test_*.py'  # + the real install
```

225 tests, 52 of which need a real install and skip without one. With one
present the suite reports `Ran 225 ... OK (skipped=4)`; those four are
assertions about the *retail* build — which containers are empty, and how
container offsets join to language ids — and they are skipped rather than bent
when a mod has repointed those slots, because the join they rely on no longer
exists. That is deliberate: after following this README you *will* have a mod
installed, and a permanently red suite would be worse than an honest skip.

The synthetic ones build localisation containers in memory from known key/value
lists and check the readers recover them exactly, including the
places round-trips usually break: `%d%%`, `&quot;`, `<span class=…>`, `<br>`,
`[BTN_A]` and CJK. They also check the *writer* rebuilds all 32 shipped
containers and the whole `toc` byte-for-byte, and that the key hash matches all
25,034 values in the game's own table — the game ships its own oracle, and the
test uses it.

The installer tests run against a sandbox copy of the real `toc` and are
skipped when no install is found. They start from a **pristine** `toc`
(`toc.orig` or `toc.BAK` if present) rather than `toc`, so the suite gives the
same answer whether or not you have a mod installed — which is the state you
are in after following this README.

`tests/test_arabic.py` is in two tiers, because the two halves have very
different requirements. The segmentation half — run splitting, markup, reversal —
needs nothing but the standard library and is always tested. The font half needs
`uharfbuzz`, `fontTools` and the Noto UI cut, and skips without them; what it
checks is the whole safety argument, by resolving the encoded strings the way
the game will, one glyph per codepoint, and requiring the HarfBuzz glyph
sequence back.

Writing these found eleven real bugs. The first six are in the container code:

- the `struct` layout for the DSAR block descriptor is 32 bytes, not 40 (`<` in
  a `struct` format suppresses padding);
- a shared argparse parent made `--game` silently vanish when it appeared
  before the subcommand;
- `open_toc` leaked a file handle;
- a mod built from a translated template silently leaked the template
  language's English into every key that had not been translated, so a
  "translation" was English with a few Arabic strings in it;
- installing a mod broke `list`, `dump` and `verify` for all 32 languages;
- `dump -o x.json` wrote tab-separated text into a file called `.json`, and a
  blank JSON template would have installed as 25,034 *blanked* strings rather
  than as "fall back to English", because the JSON reader kept `""` where the
  TSV reader dropped it.

And five in the Arabic path, all of which would have shipped as visibly wrong
text:

- the segmenter had a shared-mutable-class-state bug, so two `PreShaper`
  instances in one process shared one shaper's state;
- the two weights were paired by glyph *id* rather than glyph *name*, which
  silently mispairs any font whose styles do not have aligned glyph orders;
- a module-level `_CLUSTERS` cache leaked between calls;
- a space bundled into a neighbouring Latin run travelled to the wrong side of
  it when the runs were reversed — 144 of 716 strings;
- and the fix for that one over-corrected, splitting *every* gap and so
  reversing the words inside a Latin phrase. The rule that is actually right is
  narrower: a gap is its own run only when Arabic is touching it. Caught by
  asserting that every non-Arabic stretch survives verbatim, which is now a
  test.

Plus two in the suite itself: the writer round-trip class was missing the
`skipIf` its three neighbours had, so on a machine with no game copy it errored
instead of skipping; and the four retail-build assertions above failed once a
mod was installed.

The provenance claim in [docs/SOURCES.md](docs/SOURCES.md) is itself tested:
`TestLumpTypeProvenance` asserts the nine localisation CRCs are disjoint from
the 78-entry upstream table, so the documented split cannot quietly become
false after a regeneration.

---

## Status: what is verified and what is not

Being straight about this, because it is the one thing that matters for
whether you should trust the output.

**Verified, by test, against a real install:**

- all 32 containers decode, share one key list, and join to the right languages
- the writer rebuilds all 32 containers and the whole `toc` byte-for-byte
- the key hash reproduces all 25,034 of the game's own hash values
- install changes exactly 4 asset rows and 2 of 8 lumps, and never touches
  `d/localization`
- uninstall restores the `toc` to the identical bytes
- `list`/`dump`/`verify` keep working with a mod installed
- the Arabic build: 1,424 distinct Arabic runs shaped, 138 distinct letterforms
  (86 presentation forms, 52 private-use), every one of the 34,434 emitted
  codepoints resolves to a real glyph in both weights, and re-resolving the
  encoded strings the way the engine will reproduces the HarfBuzz glyph sequence
  for all 1,424 runs
- the installed Arabic mod, read back out of `d/mods/mod1` at the offsets the
  `toc` gives: 716 of 716 strings byte-exact, all six slots carrying the
  identical 1,290,017-byte payload, `d/localization` still retail

**Not verified:**

- **that the game loads the mod, and that the Arabic reads correctly on screen.**
  Nobody has run Rift Apart with one. The five archive-descriptor constants come
  from Overstrike rather than from the game's own archives; they are checked
  against the source field by field, and they are believed to mean "flat and
  uncompressed" where retail means "LZ4 blocks", but that is not established from
  the game's own data, because the game ships no uncompressed archive to compare
  against. This is the main residual risk, and a single launch settles it.
- **that the pre-shaping workaround survives contact with the renderer.** The
  logic is verified — the strings resolve, in order, to the glyphs HarfBuzz
  chose — but that is a statement about the data, not about the pixels. A caret
  in a text field is the most likely thing to look wrong.
- **which of slots 23/28/29/30 is Arabic.** The four are byte-identical and
  carry no identifier. Writing all four sidesteps it; a per-slot marker settles
  it.
- **the language dropdown.** The dropdown in `d/config` has 23 entries and
  Arabic is not one of them, and that file is not touched — it is game content,
  and editing it in place is exactly what this project does not do. So the mod
  also claims the two **en-US** slots, which means the Arabic shows when the game
  is set to English (US) rather than when it is set to Arabic. That is a
  workaround, not the intended behaviour, and it is the reason the in-game
  language setting and what you see on screen will disagree.

### What the first launch should tell you

| what you see | what it means |
| --- | --- |
| Arabic, connected, right to left | it works; record it and this section can be tightened |
| letters joined but words backwards | the run reversal is off for that string; the shaped data is fine |
| separate letters, left to right | the font did not load, or the shaped codepoints are not in the `cmap` the game read |
| boxes or nothing at all | the font asset was not repointed, or `d/mods/mod1` was not found |
| a crash | the archive layout is wrong after all; the error text will say which field |

---

## Licence

MIT — see [LICENSE](LICENSE).
