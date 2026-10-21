# CRIWARE (ADX2, Sofdec): ACB, AWB, HCA, CPK, USM

Читай, если в папке игры есть `*.acb`, `*.awb`, `*.cpk`, `*.usm` или `cri_*.dll`.

## Уровень в BabelDub v0.1

**C** — CLI v0.1 форматы CRIWARE не разбирает; только инструкции. План — позже, уровень B (для зашифрованных HCA нужен ключ).

## Форматы

- **ACB** — таблица `@UTF`: cue, waveform, sequence.
- **AWB** — `AFS2` с HCA или ADX; стриминговые AWB лежат отдельно.
- **CPK** — архивы; **USM** — видео (см. `audio/video.md`).
- **Шифрование HCA:** 56-битный ключ; с середины 2018 добавляется subkey из AWB. Файл vgmstream `hca_keys.h` содержит около 1200 строк с известными ключами.

## Чем извлечь

| Инструмент | Назначение | Состояние на 2026-10 |
|---|---|---|
| vgmstream | декодирование HCA/ADX/ACB/AWB | активен |
| CriCodecs / CriStudio | CPK, USM, ACB/AWB, HCA, ADX, UTF: сборка, мультиплексирование, ключи, `--recover-key` | v1.2.0 |
| VGAudio | кодирование HCA/ADX, `--hcaquality` | последний релиз 2018 |
| SonicAudioTools (AcbEditor) | правка ACB/AWB | «no longer maintained» |

## Нативный путь пересборки (не проверено)

1. Закодировать HCA **тем же ключом**, с теми же каналами и частотой (VGAudio или CriCodecs).
2. Переупаковать AWB и обновить ACB: в таблице waveform хранятся длины.
3. Сохранить порядок и ID записей.

## Подводные камни

- Ключ шифрования HCA (и subkey из AWB).
- Длины записей в ACB.
- Видео USM со сведённым звуком — отдельная задача (`audio/video.md`).

## Ссылки

- vgmstream — https://github.com/vgmstream/vgmstream (`src/meta/hca_keys.h`)
- CriCodecs — https://github.com/Youjose/CriCodecs
- VGAudio — https://github.com/Thealexbarney/VGAudio
- SonicAudioTools — https://github.com/blueskythlikesclouds/SonicAudioTools
