# Rift Apart localisation formats

Working notes on the three container formats involved, and — more usefully —
on the one fact that makes localisation work on this game possible: **the game
has four language slots it never fills in.**

Everything here was derived by inspecting a retail PC install and cross-checking
the results. Where something is inferred rather than proven, it says so.

---

## 1. The three layers

```
d/localization                 DSAR  (block-compressed archive)
  └─ 32 x 1TAD                one container per language slot
       └─ 9 lumps             key table + value table

toc                            1TAD   (bare, 8-byte header in front)
  └─ 340,662 assets           every asset in the game, indexed
```

Nothing is extracted to individual files. The `1TAD` containers sit
back-to-back inside the decompressed `d/localization`, and the TOC indexes
them by *offset into that decompressed blob*.

---

## 2. DSAR — the archive wrapping each game directory

Every file directly under a game directory (`d/localization`, `d/config`,
`d/userinterface`, …) is one DSAR holding that directory's assets
concatenated.

```
header, 32 bytes
    u32   magic          'DSAR'  = 0x52415344
    u32   version        3
    u32   block_count    281 in d/localization
    u32   data_begin     absolute offset of the first payload
    u64   reserved
    8s    'PADDING*'

block_count x 32-byte descriptors
    u64   dec_offset     offset of the block in the decompressed output
    u64   comp_offset    offset of the payload in this file
    u32   dec_size
    u32   comp_size
    u8    mode           3 = LZ4 block, 2 = GDeflate
    7s    reserved
```

then all payloads, back to back.

`d/localization` is 281 blocks, **all LZ4**, and decompresses to 70,279,168
bytes. `d/config` uses GDeflate, which is why a pure-Python reader needs a
native decoder for it and `rcextract` treats it as optional.

Blocks are contiguous and ordered, so the whole archive is one flat buffer and
the `dec_offset` fields are really just a check that assumption holds.

> Note the descriptor is **32** bytes, not 40. `struct` with a `<` prefix does
> not insert padding, so `<QQIIB7s` is 8+8+4+4+1+7.

---

## 3. `1TAD` — the asset container

```
0x00  4s   magic '1TAD'
0x04  u32  asset_type_crc
0x08  u32  file_size          total, header + directory + payloads
0x0C  u16  lump_count
0x0E  u16  shader_count
0x10  lump_count x 12 bytes
          u32  lump_type_crc
          u32  offset           relative to the container start
          u32  size
...    payloads, 4-byte aligned
```

The magic can sit behind a small unrelated header. In `toc` it is at offset 8,
where the preceding 8 bytes are a build hash. The `d/localization` containers
start at offset 0.

`file_size` is what lets containers be chained: the next one starts exactly
where this one ends.

The main `toc` is **not** DSAR-wrapped. It is a bare `1TAD`.

---

## 4. The localisation lumps

These nine CRCs are **not** in `ripped_apart`'s `lump_types.h` — they were found
by inspecting the containers. The names below are descriptive.

| CRC        | Name              | Contents                                        |
|------------|-------------------|-------------------------------------------------|
| `0xd540a903` | Count           | u32 — the number of strings, N = 25,034         |
| `0x4d73cebd` | Keys            | N NUL-terminated keys, in index order           |
| `0xa4ea55b2` | Key offsets     | u32[N] into the key blob                        |
| `0x70a382b8` | Values          | NUL-terminated localised strings                |
| `0xf80deeb4` | Value offsets   | u32[N] into the value blob; **0 = untranslated**|
| `0xb0653243` | Zero            | u32[N], all zero in every shipped language      |
| `0x06a58050` | Key hash        | u32[N]                                          |
| `0x0cd2cfe9` | Hash overflow    | u32[N/2]                                        |
| `0xc43731b5` | Key alt          | u32[N]                                          |

### The offset-0 sentinel

**The value blob begins with a NUL byte.** Verified across the real containers:
`vb[0] == 0`, and the smallest non-zero value offset is 1.

That is what makes `value_offset == 0` a safe test for "not translated".
Without the leading NUL the first string would sit at offset 0 and be
indistinguishable from a missing one. The key blob has no such sentinel — its
first key is at offset 0.

So an untranslated entry is `None`, which is *not* the same as `""`. A real
empty string is a legitimate translation and must survive a round trip.

### Index order is stable

The key blob order equals the string index order, so a string can be addressed
by index or by key without consulting the offset tables. All 32 containers
carry a **byte-identical key list of 25,034 keys**; only the values differ.

That is why the offsets are redundant, and it is also the invariant that makes
a translation safe: a string written against `MENU_QUIT` is the same string in
every slot.

---

## 5. The TOC, and why the language ids are not positional

The `toc` file indexes **340,662** assets across **147** archives in **256**
groups. `d\localization` is archive **67**. Relevant lumps:

| CRC          | Contents                                          |
|--------------|---------------------------------------------------|
| `0xede8ada9` | u32 pairs — asset groups: `(first_index, count)`   |
| `0x506d7b8a` | u64[N] — asset path hashes                        |
| `0x65bcf461` | 16×N — `(size, archive_index, offset, header_offset)` |
| `0x398abff0` | 0x42×A — archive names, NUL-terminated            |

An asset's **group** is assigned by position: group *g* covers assets
`first_index … first_index + count - 1` of the group table, and it is exactly
one group per asset.

### The problem

All 32 localisation assets are registered under the **same path hash**,
`0xbe55d94f171bf8de`, in archive `d\localization`. Nothing in the containers
distinguishes them — same keys, same lumps, same CRC. The *only* thing that
separates them is the TOC group, which steps by 8:

```
group 0, 8, 16, ... 248      ->  language id 0..31  (group // 8)
```

### The trap

The containers are stored in the archive in an order that is **not** the
language order. The first container in the blob is Czech (language id 24);
the last is Korean (language id 10).

So "container *N* in the blob" ≠ "language *N*". Reading them off positionally
labels every table wrong. The join has to go through the TOC:

* each localisation asset records the **byte offset and size** of its
  container inside the decompressed archive;
* the containers in the archive record their own `base` and `file_size`;
* match on `(offset, size)` → that asset's `group // 8` is the language id.

That join is exact and unambiguous, which is what `rcextract` does.

---

## 6. The four empty slots

28 of the 32 slots carry strings (18,463–19,144 each). Four are empty:
**ids 23, 28, 29, 30**.

An empty slot is not a stripped or corrupt container. It is a complete,
well-formed container with the full 25,034-key table and an all-zero value
offset array — the engine's representation of "this language is supported but
has no translation yet."

### Which languages are they?

The game config contains 33 distinct `LANGUAGE_*` dropdown keys. 26 of them
appear in the shipped text-language dropdown, and 28 slots are filled, so the
slot set is those 26 plus a duplicate en-US and a duplicate en-GB (used for
the regional voice-over variants).

The keys that exist with **no** container and are **not** regional variants of
a language that already ships:

| Key                     | Language    |
|-------------------------|-------------|
| `LANGUAGE_ARABIC`       | Arabic      |
| `LANGUAGE_INDONESIAN`   | Indonesian  |
| `LANGUAGE_THAI`         | Thai        |
| `LANGUAGE_VIETNAMESE`   | Vietnamese  |

Two more — `LANGUAGE_CA_FRENCH` and `LANGUAGE_MX_SPANISH` — are regional
variants that reuse the `fr` and `es` slots, so they need no container of
their own.

Four languages, four empty slots. That is a strong fit, and the table above is
the best-supported account of what the slots are for.

**What is not proven:** which of ids 23/28/29/30 is Arabic specifically. The
four slots are byte-identical and carry no identifier, so the mapping is not
recoverable from the data — only by filling one and seeing whether the game
picks it up.

### Arabic is nonetheless a real, supported language

Not a hypothetical slot. The evidence is in the game config and the archive
index:

* `kLanguageArabic` exists, mapped to `ui/loaded/authored/_art/textures/RCRA_AR_logo.texture`;
* the TOC lists **`d\wem.ar`** (13,747 assets) and **`d\soundbank.ar`**
  (290 assets) — the Arabic voice-over archive is shipped, alongside `.br`,
  `.de`, `.es`, `.fr`, `.it`, `.jp` and the rest.

So Rift Apart has an Arabic voice track and an Arabic logo, and no Arabic
text. That is precisely the gap a translation fills.

---

## 7. Language id → language

Recovered by joining three independent facts: the TOC offset join above; the
shared key list, which fixes container ordering; and the fact that each
container names *itself* in its own `LANGUAGE_*` entries.

| id | code     | language          | strings | how it was identified                     |
|----|----------|-------------------|---------|-------------------------------------------|
| 0  | `en-US`  | English (US)      | 19,144  | dup of id 1; voice-over variant            |
| 1  | `en-US`  | English (US)      | 19,144  | base text                                  |
| 2  | `en-GB`  | English (UK)      | 18,867  | dup of id 18; voice-over variant           |
| 3  | `da`     | Danish            | 18,463  | `UI_WEAPONS` = `VÅBEN`                     |
| 4  | `nl`     | Dutch             | 18,463  | `NEDERLANDS`                               |
| 5  | `fi`     | Finnish           | 18,463  | `SUOMI`                                    |
| 6  | `fr`     | French            | 18,463  | `FRANÇAIS`                                 |
| 7  | `de`     | German            | 18,463  | `DEUTSCH`                                  |
| 8  | `it`     | Italian           | 18,463  | `ITALIANO`                                 |
| 9  | `ja`     | Japanese          | 18,579  | `日本語`                                    |
| 10 | `ko`     | Korean            | 18,467  | `한국어`                                    |
| 11 | `no`     | Norwegian         | 18,463  | `UI_WEAPONS` = `VÅPEN` (cf. Danish `VÅBEN`)|
| 12 | `pl`     | Polish            | 18,463  | `POLSKI`                                   |
| 13 | `pt-BR`  | Portuguese (BR)   | 18,463  | `PORTUGUÊS DO BRASIL`                      |
| 14 | `ru`     | Russian           | 18,463  | `РУССКИЙ`                                   |
| 15 | `es-419` | Spanish (LatAm)   | 18,463  | `LANGUAGE_LA_SPANISH` = `ESPAÑOL DE LATINOAMÉRICA` |
| 16 | `sv`     | Swedish           | 18,463  | `SVENSKA`                                  |
| 17 | `pt-PT`  | Portuguese (PT)   | 18,463  | `PORTUGUÊS`                                |
| 18 | `en-GB`  | English (UK)      | 18,867  | base text                                  |
| 19 | `tr`     | Turkish           | 18,463  | `TÜRKÇE`                                   |
| 20 | `es-ES`  | Spanish (Spain)   | 18,463  | `LANGUAGE_SPANISH` = `ESPAÑOL (ES)`        |
| 21 | `zh-Hant`| Chinese (Trad.)   | 18,463  | `繁體中文`                                   |
| 22 | `zh-Hans`| Chinese (Simp.)   | 18,463  | `简体中文`                                   |
| 23 | —        | **empty**         | 0       | reserved                                   |
| 24 | `cs`     | Czech             | 18,463  | `ČEŠTINA`                                  |
| 25 | `hu`     | Hungarian         | 18,463  | `MAGYAR`                                   |
| 26 | `el`     | Greek             | 18,463  | `ΕΛΛΗΝΙΚΑ`                                   |
| 27 | `ro`     | Romanian          | 18,463  | `ROMÂNĂ`                                   |
| 28 | —        | **empty**         | 0       | reserved                                   |
| 29 | —        | **empty**         | 0       | reserved                                   |
| 30 | —        | **empty**         | 0       | reserved                                   |
| 31 | `hr`     | Croatian          | 18,463  | `HRVATSKI`                                 |

**Danish vs Norwegian** is the one pair that needs a body string rather than a
`LANGUAGE_*` name, because both languages call themselves *DANSK*/*NORSK* and
both entries are translated in every table. The discriminator is
`UI_WEAPONS`: `VÅBEN` is Danish, `VÅPEN` is Norwegian.

**es-ES vs es-419** is resolved by which variant the container claims for
itself: id 20 renders `LANGUAGE_SPANISH` as `ESPAÑOL (ES)`, id 15 renders it
as bare `ESPAÑOL` and puts `ESPAÑOL DE LATINOAMÉRICA` on the LatAm key.

---

## 8. String shapes a translation has to preserve

Counted across all 32 slots. A translator or any rewriting tool must keep each
of these intact, in the same position and the same number of times.

Values below are **shape sketches with the game text elided**, so the markup is
visible without reproducing content. Counts are exact, from the live `en-US`
table. A translator or any rewriting tool must keep every shape intact, in the
same position and the same number of times.

| Shape                | Occurrences (en-US) | Shape sketch                                         |
|----------------------|--------------------|------------------------------------------------------|
| `&quot;` entity      | 9,748              | `He said &quot;hi&quot;`                              |
| subtitle timing      | 9,321              | `<ts=&quot;0.049;5.076&quot;>…`                       |
| button glyph         | 218                | `[BTN_AIM] or [BTN_FIRE]`                            |
| explicit line break  | 174                | `Title line<br>Second line`                          |
| `printf` `%d`        | 47                 | `%d BOLTS`                                          |
| `printf` `%s`        | 12                 | `+%s%% Bolts Gained`                                 |
| markup span          | 47                 | `<span class='emphasis'>…</span> …`                  |
| `TBW` marker         | 46                 | `TBW`                                                |
| `&emsp;` / `&ensp;`  | 8 each             | `&emsp;Some option&ensp;`                            |
| `printf` `%.2f`      | 1                  | `… by %.2f seconds.`                                 |
| signed percent       | 2                  | `+%d%%` and `-%d%%`                                  |

Shapes that **do not occur** anywhere in the shipped data, listed here so
nobody hunts for them: `<color=…>`, `<size=…>`, `&amp;`, `&apos;`, `\n`, `\t`,
`%f`. The only HTML entities in use are `&quot;`, `&emsp;` and `&ensp;`.

The exact printf specifiers that appear are `%d`, `%s` and `%.2f` — nothing
else. A scan for `%[a-z]` will return false positives from the CJK tables,
where `%` is followed by a kana or hangul character.

The subtitle timing prefix is the one that bites hardest. It is *prepended* to
the string in the source, so any tooling that rewrites the value must keep it
at the front, byte for byte — including the `&quot;` entities inside it.

### `None` is not `""`

The `en-US` table has **no** empty-string values, and neither does any other
shipped slot; `INVALID` is simply untranslated (`None`). So the
distinction between "no translation" and "translated to the empty string"
never shows up in retail data.

It still has to be respected by a writer. The value offset is what carries the
distinction, and a tool that treats offset 0 and offset *n* as interchangeable
will silently turn a deliberate empty string into a fallback. The leading NUL
sentinel in §4 is what makes the distinction representable at all.

---

## 9. What is not here

This file documents the **read** path only.

Writing strings back means rebuilding a `1TAD` container, re-laying-out every
container after it (the ones that follow shift in the archive), updating the
TOC `offset`/`size` entries, and re-compressing the DSAR. That is what
[ripped_apart](https://github.com/chaoticgd/ripped_apart) and the Overstrike
mod loader already do, and they do it with a dependency graph the TOC provides
for exactly this purpose. `rcextract` does not reimplement it.

The value blob's leading NUL means a writer must keep the sentinel, and the
offset tables must be regenerated from the new blob rather than patched.
