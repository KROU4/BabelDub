# Unreal Engine 4/5: `.pak`, IoStore, `.locres`

Level in v0.1: **C** — CLI v0.1 has no Unreal adapter. If the game's voice sits in FMOD or Wwise inside a UE game (common), build it per [fmod.md](fmod.md) or [wwise.md](wwise.md). Recon — the `babeldub-recon` skill, `references/engines/unreal.md`.

Markers: [verified] — a primary source; [per guides] — a secondary source (modder guides, forums); [not verified].

## 1. Where things live

- Containers in `Content/Paks/`: `.pak` (versions 1–11) or IoStore (`.utoc` + `.ucas` + an "empty" `.pak`) in UE5 and late UE4; AES-256 encryption (key in the executable) and `.sig` signatures are possible [per guides].
- Audio: `SoundWave` (`.uasset/.uexp`, data in `.ubulk` or inside; OGG, ADPCM, Opus, and in UE5 Bink Audio), `DialogueWave` + `DialogueVoice`; localized assets in `Content/L10N/<culture>/...`. Often FMOD or Wwise (`Content/FMOD/Desktop/*.bank`, `.bnk/.wem`) [per guides].
- Texts: `Content/Localization/<Target>/<culture>/<Target>.locres`; subtitles can also live in `SoundWave.Subtitles` [per guides].

## 2. Tools

| Tool | Purpose | Status |
|---|---|---|
| FModel / CUE4Parse | browsing and export (audio, `.locres` → JSON), working with the AES key | [verified: repositories] |
| repak | `.pak`: unpack and pack, AES; reads and writes versions 2–5, 7, 8A/8B, 9, 11 (UE 4.0–5.3); 1, 6, 10 not tested by the authors | [verified: README] |
| retoc | IoStore: `unpack`, `unpack-raw`, `pack-raw`, `to-zen` (build a mod from legacy), `to-legacy`; there is no separate `pack` command | [verified: README] |
| UnrealLocres, LocRes-Builder, UnrealLocresEditor | `.locres` ↔ CSV | [verified: repositories] |
| RE-UE4SS | runtime mods (Lua/C++) | [verified: repository] |

## 3. Building a mod [per guides]

- **Games with `.pak`:** a folder `ModName_P/<Game>/Content/...` with files at the same paths → `repak pack` → `ModName_P.pak` into `Content/Paks/` or `Content/Paks/~mods/`. **The `_P` suffix is mandatory** (a patch pak with priority). For UE before 4.27 with signatures, copy any `.sig` under the mod's name.
- **Games with IoStore:** the mod is IoStore too (`.pak` + `.utoc` + `.ucas`), built with `retoc to-zen` or UnrealReZen.
- **SoundWave audio:** cook in an **Unreal Editor of the same version** (free) with the same package path — without the editor you cannot build your own `.uasset`. This is manual work for the user.
- **Text:** replace the `.locres` of an existing culture in a `_P` pak; a new culture must be allowed by the game.

## 4. Pitfalls [per guides]

- AES: without the key the paks cannot be read (the key is searched for in the executable — the user does this, not the agent).
- Pak signatures, EasyAntiCheat in many UE games — with anti-cheat BabelDub refuses by default.
- FaceFX and lip-sync curves are tied to the original duration.
- Subtitle timings in `SoundWave`.
- The cook version must match the game.

## 5. What to tell the user

The dub (if the lines could be extracted, for example through FMOD or Wwise) is ready in `post/audio/`. For Unreal assets v0.1 does not build a mod automatically: an Unreal Editor of the same version and packing into a `_P` pak (`repak` or `retoc`) are needed. The alternative is a runtime mod through UE4SS ([runtime-mods.md](runtime-mods.md)).
