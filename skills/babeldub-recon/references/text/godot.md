# Godot: переводы `.translation`, `.po`, CSV

Читай, если игра на Godot (см. `engines/godot.md`).

## Уровень в BabelDub v0.1

**C** — CLI v0.1 тексты Godot не читает; только инструкции.

## Как устроено

- CSV импортируется в `.translation`. **`OptimizedTranslation` хранит хэши вместо ключей**, поэтому GDRE восстанавливает ключи эвристиками и подсказкой `--translation-hint`.
- `.po`/`.mo` загружаются как Translation.
- Список переводов — в настройках проекта (`project.binary`).

## Чем извлечь и собрать

- GDRE Tools (gdsdecomp) v2.7.4: `--recover`, `--extract`; запись переводов — `--patch-translations=<CSV>=<res path>`, затем `--pck-patch`.
- Шифрованный PCK: ключ (`--key`) извлекают утилитой gdke.

## Связь с озвучкой

Ключ перевода часто совпадает с именем аудиофайла или используется в диалоговом плагине игры; проверяй выборкой. Если ключи восстановлены эвристикой, связь может быть неточной.

## Подводные камни

- Восстановленные ключи `OptimizedTranslation` неточны.
- MD5 в PCK, шифрование.
- Шрифты без кириллицы.

## Ссылки

- gdsdecomp — https://github.com/GDRETools/gdsdecomp
- gdke — https://github.com/char-ptr/gdke
