# Godot

Читай, если в папке игры есть `*.pck` с магией `GDPC` или exe с GDPC в хвосте.

## Уровень в BabelDub v0.1

**C** — CLI v0.1 Godot не разбирает; только инструкции. План поддержки — позже v0.3 (уровень B: нужен Godot той же версии, для шифрованных игр — ключ AES).

## Как опознать

- `.pck` рядом с exe или PCK, вшитый в exe (магия `GDPC`).
- Возможно шифрование AES-256, ключ вшит в бинарь; извлекают его утилитой gdke.

## Где голос

При экспорте исходники импортируются в ресурсы `.godot/imported/*.oggvorbisstr` (`AudioStreamOggVorbis`), `.sample` (WAV), `.mp3str`. Исходный путь ведёт к ним через файлы `.import` (по гайдам сообщества).

## Где текст

См. `text/godot.md`: CSV → `.translation` (`OptimizedTranslation` хранит хэши вместо ключей), `.po`/`.mo`; список переводов — в настройках проекта (`project.binary`).

## Чем извлечь

| Инструмент | Назначение | Состояние на 2026-10 |
|---|---|---|
| gdsdecomp / GDRE Tools | Godot 2–4: `--recover`, `--extract`, `--pck-create`, `--pck-patch`, `--patch-translations=<CSV>=<res path>`, `--key` | v2.7.4 |
| GodotPckTool | extract, list, add | v2.3 |
| gdke | извлечение ключа шифрования PCK | существует (детали не проверялись) |

## Нативный путь пересборки

- **Текст:** GDRE `--patch-translations`.
- **Звук:** импортировать новые OGG в пустом проекте Godot **той же версии** (`godot --headless --import`, движок открыт), взять импортированные ресурсы и заменить их в PCK через GDRE `--pck-patch`. На практике не проверено.

## Подводные камни

- Формат импортированных ресурсов зависит от версии Godot.
- MD5 в PCK (у GDRE есть ключи игнорирования).
- Шифрование PCK.
- Игры на C# (.NET) — отдельные сборки.

## Ссылки

- gdsdecomp — https://github.com/GDRETools/gdsdecomp
- GodotPckTool — https://github.com/hhyyrylainen/GodotPckTool
- gdke — https://github.com/char-ptr/gdke
