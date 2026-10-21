# I2 Localization (Unity)

Read this if a Unity game has an `I2Languages` asset (usually in `resources.assets`).

## Level in BabelDub v0.1

**C**: v0.1 has no I2 adapter; instructions only. Texts from I2 do not get into `inventory/texts.jsonl`, and `babeldub link` does not link them. Planned for v0.3 at level B. The details below come from a community dump (secondary source).

## How it works

- The `I2Languages` asset (`LanguageSourceAsset`):
  - `mSource.mTerms: List<TermData{string Term; int TermType; string[] Languages; byte[] Flags; string[] Languages_Touch}>`;
  - `mSource.mLanguages: List<LanguageData{string Name; string Code; byte Flags}>`.
- The term's string in language N is the element `Languages[N]`; language N is described in `mLanguages[N]`.
- **A new language** = an element in `mLanguages` plus a string in `Languages[]` of every term.
- I2 can pull tables from Google Sheets or a local CSV if the developer enabled it (not verified).

## Extraction tools

- UABEA (asset dump), UnityPy.
- i2loc-manager: CSV/TSV export and import.
- For IL2CPP the typetree generator sometimes gets it wrong; then manual parsing is needed.

## Link to the voice

The term name (`Term`) often equals the audio name or the event key; check with a sample. If the voice goes through FMOD or Wwise, the link is via the audio table key or the event name.

## Pitfalls

- TextMeshPro fonts without Cyrillic.
- The order of languages in `mLanguages` and in `Languages[]` must match.

## Links

- I2 Localization — http://inter-illusion.com/tools/i2-localization/
- i2loc-manager — https://github.com/Veydzher/i2loc-manager
- I2 parsing (community dump) — https://github.com/truix/travellers-rest-planner/blob/main/scripts/dump_i2l.py
