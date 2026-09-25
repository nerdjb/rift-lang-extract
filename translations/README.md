# Your translations

Put translation files here and they will be versioned. Everything matching
`*.json` or `*.tsv` is ignored *everywhere else* in the repo, because that is
the shape of `rcextract`'s own output and it is verbatim game text — see
`.gitignore`.

Two formats are accepted, and both are plain text so they diff and merge
normally.

**JSON** — an object of key to string:

```json
{
  "MENU_DIFFICULTY_TITLE": "الصعوبة",
  "UI_WEAPONS": "الأسلحة"
}
```

**TSV** — `key<TAB>value`, with a `key<TAB>value` header line:

```
key	value
MENU_DIFFICULTY_TITLE	الصعوبة
UI_WEAPONS	الأسلحة
```

Install one with:

```bash
rcextract mod install --game "$GAME" -t translations/mine.json
```

Keys you leave out stay untranslated and fall back to English, so a partial
translation is safe to install while it is still in progress. A key that is not
in the game's 25,034-key list is rejected with a message naming it, rather than
being silently dropped.

To get a blank starting point:

```bash
rcextract dump --game "$GAME" -l 23 -o translations/mine.json --template
```
