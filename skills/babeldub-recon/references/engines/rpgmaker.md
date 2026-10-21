# RPG Maker (MV, MZ, XP, VX, VX Ace)

Читай, если в папке игры есть `www/js/rpg_core.js`, `js/rmmz_core.js`, `data/System.json`, `Game.rgss3a`/`.rgss2a`/`.rgssad` или `RGSS*.dll`.

## Уровень в BabelDub v0.1

**C** — CLI v0.1 RPG Maker не разбирает; только инструкции. План: позже, текст — уровень A (через rvpacker-txt-rs), голос — B (зависит от плагинов игры). Если незашифрованные аудиофайлы лежат отдельно, `babeldub scan` может показать их через адаптер `loose`.

## Как опознать

| Признак | Версия |
|---|---|
| `www/js/rpg_core.js`, `www/data/*.json` | MV |
| `js/rmmz_core.js` в корне, `data/*.json` | MZ |
| `Game.rgss3a`, `RGSS3*.dll` | VX Ace |
| `Game.rgss2a` | VX |
| `Game.rgssad` | XP |

## Где голос

- MV/MZ: `audio/{bgm,bgs,me,se}/`. Шифрованные варианты: `.rpgmvo`/`.rpgmvm` (MV), `.ogg_`/`.m4a_` (MZ). Ключ — в `data/System.json` (`encryptionKey`, флаги `hasEncryptedAudio`/`hasEncryptedImages`); шифр — 16-байтовый заголовок `RPGMV…` и XOR первых 16 байт (по гайдам сообщества).
- **Встроенной системы голоса нет.** Голос обычно проигрывается как SE перед сообщением или плагином; связь «звук ↔ реплика» ищут по порядку команд события.
- XP/VX/VX Ace: `Audio/*` (голос редко).

## Где текст

MV/MZ — `data/*.json` (команды событий; код 401 — «Show Text»); XP/VX/VX Ace — `Data/*.rvdata2` (Ruby Marshal) в архиве. Подробно — `text/rpgmaker.md`.

## Чем извлечь

| Инструмент | Назначение | Состояние на 2026-10 |
|---|---|---|
| RPGMakerDecrypter | расшифровка XP/VX/VX Ace/MV/MZ, `--recreate-project` | активен |
| rvpacker-txt-rs | текст 2000/2003/XP/VX/VX Ace/MV/MZ ↔ `.txt`, `read`/`write`, кодировки | активен |
| rpgmtranslate | GUI поверх rvpacker | активен |
| rpgm-asset-decrypter-rs | расшифровка ассетов | активен |

## Нативный путь пересборки

- Текст: JSON или rvdata2 через `rvpacker-txt-rs write`.
- Звук: зашифровать новый OGG тем же ключом или положить незашифрованный файл и выключить флаг — зависит от движка и плагинов (не проверено).
- Старые движки: распакованная папка `Data/` и удалённый архив (по гайдам сообщества).

## Подводные камни

- Шифрование ассетов (ключ в `System.json`).
- Голос завязан на плагины конкретной игры.
- Кодировки текста в старых версиях.

## Ссылки

- RPGMakerDecrypter — https://github.com/uuksu/RPGMakerDecrypter
- rvpacker-txt-rs — https://github.com/RPG-Maker-Translation-Tools/rvpacker-txt-rs
- rpgmtranslate — https://github.com/RPG-Maker-Translation-Tools/rpgmtranslate
- rpgm-asset-decrypter-rs — https://github.com/RPG-Maker-Translation-Tools/rpgm-asset-decrypter-rs
