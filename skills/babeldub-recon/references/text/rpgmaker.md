# RPG Maker: тексты `data/*.json` и `*.rvdata2`

Читай, если игра на RPG Maker (см. `engines/rpgmaker.md`).

## Уровень в BabelDub v0.1

**C** — CLI v0.1 тексты RPG Maker не читает; только инструкции. План — позже, уровень A через rvpacker-txt-rs.

## Где лежит

- **MV/MZ:** `data/*.json` — карты (`Map*.json`), общие события (`CommonEvents.json`), отряды (`Troops.json`) и база данных. Реплики — команды событий с кодом **401 «Show Text»** (по гайдам сообщества).
- **XP/VX/VX Ace:** `Data/*.rvdata2` (Ruby Marshal), обычно внутри архива `Game.rgss3a`/`.rgss2a`/`.rgssad`.

## Чем извлечь и собрать

| Инструмент | Назначение |
|---|---|
| rvpacker-txt-rs | текст RPG Maker 2000/2003/XP/VX/VX Ace/MV/MZ ↔ `.txt`: `read` и `write`, кодировки |
| rpgmtranslate | GUI поверх rvpacker |
| RPGMakerDecrypter | распаковка архивов старых версий, `--recreate-project` |

Нативный путь: `rvpacker-txt-rs write`; для старых движков — распакованная `Data/` и удалённый архив (по гайдам сообщества).

## Связь с озвучкой

Встроенной системы голоса нет: звук реплики обычно проигрывается командой SE перед сообщением или плагином. Связь ищут по порядку команд в событии (SE → Show Text).

## Подводные камни

- Кодировки текста в старых версиях.
- Плагины игры меняют вывод текста и звука.
- Шрифты без кириллицы.

## Ссылки

- rvpacker-txt-rs — https://github.com/RPG-Maker-Translation-Tools/rvpacker-txt-rs
- rpgmtranslate — https://github.com/RPG-Maker-Translation-Tools/rpgmtranslate
- RPGMakerDecrypter — https://github.com/uuksu/RPGMakerDecrypter
