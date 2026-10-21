# Установка BabelDub в разных ИИ-хостах

Один репозиторий BabelDub одновременно служит маркетплейсом и плагином Claude Code, плагином Codex, расширением Gemini CLI и пакетом Agent Plugins 1.0. Скиллы (`skills/`) одинаковы для всех хостов. Отличаются только способ установки и то, что хост умеет сверх скиллов.

Пометки в таблицах:

- **проверено** — команда проверена локально (Claude Code 2.1.302, codex-cli 0.158.0, Gemini CLI 0.65.0);
- **по документации** — взято из официальной документации хоста, локально не запускалось;
- **не проверено** — разумное предположение, подтверждения нет.

## Что нужно в любом случае

Скиллы управляют CLI `babeldub`. Полный конвейер (извлечение, клонирование голосов, сборка) работает только у **локального** агента с доступом к файлам игры:

- Windows 10/11 или Linux, Python 3.10+ и [uv](https://docs.astral.sh/uv/), git, ffmpeg;
- для озвучки — видеокарта NVIDIA с 8+ ГБ видеопамяти (на CPU работает, но медленно);
- десятки гигабайт на диске для моделей и проектов в `BABELDUB_HOME` (по умолчанию `%LOCALAPPDATA%\BabelDub` в Windows и `~/.local/share/babeldub` в Linux; переопределяется переменной окружения `BABELDUB_HOME`).

CLI ставит скилл `babeldub-doctor` при первом запуске. Можно поставить и заранее:

```powershell
# Windows, из клона репозитория или распакованного kit
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

```bash
# Linux
bash scripts/install.sh
```

Без локального `runtime/` скрипты ставят CLI из GitHub (`-Ref v0.1.0` / `--ref v0.1.0` — конкретный релиз). Если uv не установлен, скрипт покажет официальную команду установки; флаг `-InstallUv` / `--install-uv` выполнит её сам.

## Сводка

| Хост | Что получает | Установка | Статус |
|---|---|---|---|
| Claude Code | скиллы, субагенты, хуки, команды `/babeldub:*` | маркетплейс `KROU4/BabelDub` | проверено |
| claude.ai, Claude Desktop | скиллы (чат), плюс агенты и хуки в Cowork | загрузка zip | по документации |
| Codex CLI, Codex desktop | скиллы | маркетплейс `KROU4/BabelDub` | проверено |
| ChatGPT (web, desktop) | скиллы в облачной песочнице | каталог плагинов, импорт маркетплейса | не проверено |
| Gemini CLI | скиллы и контекст `GEMINI.md` | `gemini extensions install` | проверено (локальный путь) |
| Antigravity | скиллы | `.agents/skills`, `npx skills` | по документации |
| GitHub Copilot (CLI, VS Code) | скиллы | `copilot plugin install` или `.agents/skills` | по документации |
| Cursor | скиллы | плагин Agent Plugins или `.agents/skills` | по документации |
| OpenCode и другие клиенты Agent Skills | скиллы | `npx skills add` | по документации |

Субагенты BabelDub есть только в Claude Code (и Cowork). На остальных хостах те же роли выполняются в основной сессии по файлам `skills/babeldub-localize/references/roles/*.md` — скилл-оркестратор сам это делает.

## Claude Code

**Проверено.**

В сессии:

```text
/plugin marketplace add KROU4/BabelDub
/plugin install babeldub@babeldub
/reload-plugins
```

Из терминала (одной командой нужна Claude Code 2.1.292+):

```bash
claude plugin install babeldub --marketplace KROU4/BabelDub
# или в два шага
claude plugin marketplace add KROU4/BabelDub
claude plugin install babeldub@babeldub          # --scope project — для всей команды проекта
```

Конкретная версия: `claude plugin marketplace add KROU4/BabelDub#v0.1.0`.

Что появится:

- скиллы `babeldub:babeldub-localize`, `babeldub:babeldub-doctor` и остальные;
- субагенты `babeldub:babeldub-*` для ролей конвейера;
- хуки: защита папки зарегистрированной игры от записи в обход `babeldub install` и сводка активного проекта при старте сессии;
- команды `/babeldub:localize <путь к игре> [язык]` и `/babeldub:rollback`.

Обновление: `claude plugin update babeldub@babeldub`. Автообновление сторонних маркетплейсов по умолчанию выключено: `/plugin` → Marketplaces → Enable auto-update. Удаление: `claude plugin uninstall babeldub@babeldub` (модели в `BABELDUB_HOME` остаются).

Без маркетплейса, на одну сессию:

```bash
claude --plugin-dir ./BabelDub                     # клон репозитория
claude --plugin-dir ./babeldub-plugin-0.1.0.zip    # архив из релиза
```

Подключить плагин всем участникам проекта — добавить в `.claude/settings.json` проекта (по документации, локально не проверено):

```json
{
  "extraKnownMarketplaces": {
    "babeldub": { "source": { "source": "github", "repo": "KROU4/BabelDub" } }
  },
  "enabledPlugins": { "babeldub@babeldub": true }
}
```

## claude.ai, Claude Desktop и Cowork

**По документации.** В облачном чате нет доступа к папке игры и к видеокарте, поэтому там доступен только перевод выгруженных пакетов реплик (`translate/batches/*.in.jsonl`). Полный конвейер — только в Claude Code или Cowork на вашем компьютере.

- **Плагин целиком** (Cowork получит ещё агентов и хуки): Customize → Plugins → Add → Upload plugin → `babeldub-plugin-<версия>.zip` из релиза. Лимиты claude.ai: до 200 МБ и 5000 файлов; архив BabelDub в них укладывается.
- **Отдельные скиллы**: Customize → Skills → загрузить zip. Каждая загрузка — ровно один скилл, поэтому в `babeldub-skills-<версия>.zip` лежит папка `claude-ai/` с отдельным архивом на каждый скилл. Начните с `babeldub-localize.zip` и `babeldub-translate.zip`. Нужно включённое выполнение кода (code execution).

## Codex CLI и Codex desktop

**Проверено** (codex-cli 0.158.0): Codex читает `.claude-plugin/marketplace.json` и ставит плагин; скиллы видны как `babeldub:babeldub-*`.

```bash
codex plugin marketplace add KROU4/BabelDub            # конкретная версия: --ref v0.1.0
codex plugin add babeldub@babeldub
```

Затем откройте новую сессию. В TUI плагины управляются командой `/plugins`, скилл вызывается как `$babeldub-localize` или через `/skills`. Включение на уровне проекта — в `.codex/config.toml`:

```toml
[plugins."babeldub@babeldub"]
enabled = true
```

Субагентов Claude и Claude-хуков в Codex нет: роли выполняются в основной сессии, а защиту папки игры обеспечивает сам CLI (папка только для чтения до `babeldub install`).

## ChatGPT (web, desktop, Work)

**Не проверено.** Плагины со скиллами доступны в ChatGPT через каталог плагинов, команды могут импортировать GitHub-маркетплейс. Скиллы работают в облачной песочнице без доступа к файлам игры и без GPU — как и в claude.ai, только для перевода пакетов. Загрузка отдельного zip-скилла в веб-ChatGPT официально не описана; OpenAI Skills API принимает zip до 50 МБ — подойдут архивы из `claude-ai/` в `babeldub-skills-<версия>.zip`.

## Gemini CLI

**Проверено** на локальном пути (Gemini CLI 0.65.0): ставятся все скиллы и `GEMINI.md`.

```bash
gemini extensions install https://github.com/KROU4/BabelDub --auto-update
# конкретная версия
gemini extensions install https://github.com/KROU4/BabelDub --ref v0.1.0
# разработка: ссылка на клон
gemini extensions link ./BabelDub
```

Установщик спросит необязательную настройку **BabelDub home** (переменная `BABELDUB_HOME`). Оставьте пустой, чтобы использовать путь по умолчанию; изменить позже: `gemini extensions config babeldub`. Без доверия к папке Gemini спросит «Do you trust the files in this folder?».

Gemini сначала смотрит GitHub Release: релизы BabelDub содержат несколько архивов без префиксов `win32.`/`linux.`/`darwin.`, поэтому Gemini берёт исходный архив тега с `gemini-extension.json` (проверено по исходникам `github.ts`).

Отдельно от расширения можно поставить только скиллы: `gemini skills install https://github.com/KROU4/BabelDub --path skills/babeldub-localize` (по документации). Gemini ищет скиллы и в `.agents/skills`.

> Для бесплатного тарифа и Google One Gemini CLI заменён Antigravity CLI (с 18 июня 2026 года). Сам Gemini CLI продолжает выходить.

## Antigravity

**По документации.** Antigravity читает скиллы из `<проект>/.agents/skills/` и `~/.gemini/antigravity-cli/skills/`.

- Проще всего — kit из релиза (`babeldub-kit-<версия>.zip`, см. ниже): в нём уже есть `.agents/skills/`.
- Или универсальный установщик: `npx skills add KROU4/BabelDub -a antigravity` (имя агента — по списку `npx skills --help`; не проверено).
- Формат плагинов Antigravity (`agy plugin install`) BabelDub пока не поддерживает (не изучен).

## GitHub Copilot (CLI и VS Code)

**По документации.** Copilot CLI читает корневой `plugin.json` (Agent Plugins 1.0), а при его отсутствии — `.claude-plugin/plugin.json`; маркетплейс ищется и в `.claude-plugin/marketplace.json`.

```bash
copilot plugin install KROU4/BabelDub
```

Скиллы Copilot также берёт из `.github/skills`, `.claude/skills`, `.agents/skills` и `~/.copilot/skills`. Агенты Claude Code (`claude/agents/*.md`) Copilot, вероятно, не подхватит — у него свой формат `*.agent.md` (не проверено).

## Cursor

**По документации.** Cursor поддерживает Agent Plugins 1.0 (корневой `plugin.json`) и читает скиллы из `.agents/skills`, `.cursor/skills`, `.claude/skills` и их домашних аналогов.

- Плагин: Customize → Plugins → импорт из репозитория `KROU4/BabelDub` (для команд — Team; не проверено).
- Скиллы: `npx skills add KROU4/BabelDub -a cursor` или kit из релиза.

## OpenCode и другие клиенты Agent Skills

**По документации.** Универсальный установщик [`npx skills`](https://github.com/vercel-labs/skills) (Node 22.20+) раскладывает `skills/*` по каталогам нужного агента:

```bash
npx skills add KROU4/BabelDub -g                          # все найденные агенты, глобально
npx skills add KROU4/BabelDub -a opencode                 # конкретный агент
npx skills add KROU4/BabelDub --skill babeldub-localize   # только один скилл
```

OpenCode также читает `.opencode/skills`, `.claude/skills` и `.agents/skills`. Установщик копирует только скиллы; CLI поставит `babeldub-doctor` при первом запуске.

## Kit: рабочая папка рядом с игрой

`babeldub-kit-<версия>.zip` из релиза — готовая папка `BabelDub-Workspace/` для любого агента:

- `AGENTS.md` (и копии `CLAUDE.md`, `GEMINI.md`) с инструкциями;
- `.agents/skills/` — для Codex, Gemini CLI, Antigravity, Cursor, Copilot, OpenCode;
- `.claude/skills/` и `.claude/agents/` — для Claude Code;
- `runtime/` и `install.ps1` / `install.sh` — установка CLI без доступа к GitHub;
- `README-KIT.md` — пошаговая инструкция.

Распакуйте её **рядом** с папкой игры (не внутрь), откройте агента в `BabelDub-Workspace` и попросите: «Переозвучь игру из ..\MyGame на русский». Cursor, Copilot и OpenCode читают и `.agents/skills`, и `.claude/skills`, поэтому скиллы могут показаться дважды — это одни и те же файлы. Автозагрузка `.claude/skills` и `.claude/agents` в Claude Code требует доверия к папке.

## Частые проблемы

| Симптом | Причина и решение |
|---|---|
| `babeldub: command not found` после установки | каталог `uv tool dir --bin` не в `PATH`: `uv tool update-shell` и новый терминал |
| Код выхода 4 у команд | не хватает окружения: `babeldub doctor`, затем `babeldub setup` (или попросите агента) |
| Gemini: `Unexpected token` при установке | JSON-манифест с BOM; в релизах BabelDub BOM нет — проверьте локальные правки |
| Claude Code не видит обновление | у плагина задана версия: обновите `claude plugin update babeldub@babeldub` или включите автообновление маркетплейса |
| claude.ai отклоняет плагин | архив должен содержать `.claude-plugin/plugin.json` в корне или в единственной папке верхнего уровня; используйте `babeldub-plugin-<версия>.zip` из релиза без перепаковки |
