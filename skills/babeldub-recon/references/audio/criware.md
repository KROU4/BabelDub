# CRIWARE (ADX2, Sofdec): ACB, AWB, HCA, CPK, USM

Read this if the game folder has `*.acb`, `*.awb`, `*.cpk`, `*.usm` or `cri_*.dll`.

## Level in BabelDub v0.1

**C**: CLI v0.1 does not parse CRIWARE formats; instructions only. Planned later at level B (encrypted HCA needs the key).

## Formats

- **ACB**: a `@UTF` table: cue, waveform, sequence.
- **AWB**: `AFS2` holding HCA or ADX; streaming AWBs are stored separately.
- **CPK**: archives; **USM**: video (see `audio/video.md`).
- **HCA encryption:** a 56-bit key; since mid-2018 a subkey from the AWB is added. The vgmstream file `hca_keys.h` has about 1200 lines of known keys.

## Extraction tools

| Tool | Purpose | Status as of 2026-10 |
|---|---|---|
| vgmstream | decodes HCA/ADX/ACB/AWB | active |
| CriCodecs / CriStudio | CPK, USM, ACB/AWB, HCA, ADX, UTF: building, muxing, keys, `--recover-key` | v1.2.0 |
| VGAudio | encodes HCA/ADX, `--hcaquality` | last release 2018 |
| SonicAudioTools (AcbEditor) | edits ACB/AWB | "no longer maintained" |

## Native rebuild path (not verified)

1. Encode HCA with **the same key**, the same channels and sample rate (VGAudio or CriCodecs).
2. Repack the AWB and update the ACB: the waveform table stores lengths.
3. Keep the order and IDs of the entries.

## Pitfalls

- The HCA encryption key (and the subkey from the AWB).
- Entry lengths in the ACB.
- USM video with mixed-down audio is a separate task (`audio/video.md`).

## Links

- vgmstream — https://github.com/vgmstream/vgmstream (`src/meta/hca_keys.h`)
- CriCodecs — https://github.com/Youjose/CriCodecs
- VGAudio — https://github.com/Thealexbarney/VGAudio
- SonicAudioTools — https://github.com/blueskythlikesclouds/SonicAudioTools
