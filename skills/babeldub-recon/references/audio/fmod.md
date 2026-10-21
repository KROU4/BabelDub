# FMOD Studio (`.bank`, FSB5)

Читай, если в папке игры есть `*.bank`, `*.strings.bank`, `fmodstudio.dll`/`fmod*.dll` или `*.fsb`.

## Уровень в BabelDub v0.1

| Что | Уровень |
|---|---|
| локализованные аудиотаблицы FMOD Studio (programmer instruments), FSB5 в `.bank` | **A** — адаптер `fmod`: извлечение, сборка своего FSB5 (PCM16 или FADPCM), патч банка, проверка |
| сохранить кодек Vorbis (размер как у оригинала) | B (план v0.2) — нужен FSBank / `fsbankcl.exe` из FMOD Engine, который пользователь ставит сам |
| банки с ключом шифрования | B (план v0.3) |
| банки FMOD в других движках (Unreal, свои) | формат тот же; на играх не-Unity не проверялось |

## Как опознать

- `*.bank`, `*.strings.bank`, `fmodstudio.dll` или `fmod*.dll`. Версия FMOD — версия файла DLL.
- `*.fsb` без `.bank` — FMOD Core (FSB5) или FMOD Ex (FSB4, старше).
- Голос обычно в отдельном банке с локалью в имени: `Dialogue_en-US.bank`, `Dialogue_FRE.bank` и т. п. (localized audio table).

## Устройство `.bank`

- RIFF формы `FEV `: `FMT ` (версия формата банка; vgmstream знает версии от `0x28` до `0x86`, у Low-Budget Repairs — `0x92`), `LIST 'PROJ'` (метаданные: события, шины, инструменты, в том числе `SNDH`, `STBL`, `HASH`), один или несколько чанков `SND `.
- `SND ` = нулевой паддинг + обычный FSB5. FSB5 начинается с **абсолютного** смещения, кратного 32.
- `SNDH` — таблица FSB внутри банка: абсолютное смещение и размер каждого FSB5.
- `STBL` — таблица аудиотаблицы: по наблюдению — хэш ключа → индекс сабсаунда. Если не менять число и порядок сабсаундов, она остаётся рабочей (проверено экспериментом; семантика полей не проверена).
- `Master.strings.bank` — соответствие GUID ↔ путь (`event:/…`, `bank:/…`). Для замены звука не нужен; нужен, чтобы восстановить пути событий.
- **Шифрование:** в FMOD Studio можно задать ключ шифрования sample data; в FMOD for Unity он лежит в сериализованном `FMODStudioSettings` («Bank Encryption Key»). Если магия `FSB5` внутри `SND ` видна — банк не зашифрован.

## FSB5 и кодеки

- Заголовок FSB5 (версия 1 — 0x3C байт): число сабсаундов, размеры заголовков, таблицы имён и данных, **кодек — один на весь FSB**, флаги, 16-байтовый GUID/хэш (нулевой допустим).
- Кодеки: 1 PCM8, 2 PCM16, 3 PCM24, 4 PCM32, 5 PCMFLOAT, 6 GCADPCM, 7 IMAADPCM, 8 VAG, 9 HEVAG, 10 XMA, 11 MPEG, 12 CELT, 13 AT9, 14 XWMA, **15 VORBIS**, **16 FADPCM**, 17 OPUS.
- **Vorbis своим кодом не писать:** setup-заголовок в FSB5 не хранится, записан только его CRC32 (setup ID), а декодер берёт заголовок из встроенной таблицы (у vgmstream их 161). Надёжно кодирует Vorbis только FSBank.
- **PCM16** — сырые little-endian сэмплы; **FADPCM** — ADPCM с кадром 0x8C байт на канал (~3,66:1 к PCM16). Оба BabelDub пишет сам; декодирование совпало с эталоном бит в бит.
- Opus в FSB5 FSBank кодирует только для Xbox Series X|S, PS5 и Switch; на Windows не проверено.

## Как связаны реплика, ключ и субтитр

- **Audio table:** файлы из папки вне проекта FMOD упаковываются в банк и проигрываются programmer instrument по ключу. Ключ по умолчанию — имя файла без расширения; `keys.txt` задаёт пары «ключ,файл». **Localized audio table** собирает по банку на локаль с суффиксом.
- В рантайме: событие с programmer instrument → колбэк `CREATE_PROGRAMMER_SOUND` → `Studio::System::getSoundInfo(key)` → `createSound` → сабсаунд.
- Ключ может содержать подпапку. В Low-Budget Repairs ключ = путь события без `event:/` (например `Dialogue/Act2/Zbyszek/dlg_a2_m16_d00.i00_AboutRoom`), а имя сабсаунда FSB = ключ строки Unity Localization (1187 совпадений).
- **Имена сабсаундов могут повторяться** — адресовать реплику нужно по контейнеру, номеру FSB и индексу. Поэтому `id` реплики в BabelDub: `fmod:<relpath>#fsb<k>:<index>`, ключ — в `entry.key`.
- Говорящий часто виден в пути события (`event:/Dialogue/<Акт>/<Персонаж>/<ключ>`).

## Длительность: где длинная реплика обрежется

- **Асинхронный** programmer instrument играет звук целиком: на тестовой игре замена 6,0 с вместо 1,92 с проиграна полностью (проверено рантаймом FMOD 2.03.06 из папки игры).
- **Синхронный** инструмент играет только часть под курсором таймлайна → длинная замена обрежется. Отсюда правило GUI-утилит сообщества «длительность такая же или меньше».
- Асинхронный инструмент в режиме **Cut** останавливается, когда перестают выполняться условия его запуска (в тестовой игре флаг не проверялся).
- **Катсцены** с таймлайном (у Unity — фиксированная длина клипа) обрежут или наложат длинную реплику: такие строки CLI помечает `fixed_length` и укладывает в длину оригинала.

## Чем извлечь

| Инструмент | Что умеет | Лицензия |
|---|---|---|
| vgmstream (`vgmstream-cli -s N -o out.wav file.bank`) | `.bank` (FEV + FSB5), `.fsb`, Vorbis/FADPCM/PCM/Opus; CLI BabelDub использует его для декодирования | permissive, ISC-типа |
| python-fsb5 | чтение FSB5, OGG из Vorbis через libvorbis | MIT |
| Fmod5Sharp | C#: декодирование `.fsb`/`.bank`, Vorbis → OGG | MIT |
| Fmod-Bank-Tools (+ CLI) | извлечение через FMOD и пересборка через FSBank | GPL-3.0 + FMOD EULA |
| FSB-BANK-Extractor-Rebuilder | GUI: извлечение и пересборка | GPL-3.0 |
| `fmodstudio.dll` самой игры | точный PCM, как его слышит игра | в BabelDub только по флагу `verify --game-runtime` |

## Пересборка

| Путь | Как | Плюсы | Минусы |
|---|---|---|---|
| **A. Свой FSB5 (PCM16/FADPCM) + патч банка** — путь BabelDub | новый FSB5 с теми же числом, порядком, именами и каналами; вставка с выравниванием 32 байта; обновление `SNDH` и размера RIFF | без проприетарных инструментов; рантайм FMOD принял (эксперимент) | файл больше, чем с Vorbis |
| B. FSBank API / `fsbankcl.exe` + патч банка | родной Vorbis | размер как у оригинала, шифрование | FMOD Engine скачивается только после входа на fmod.com; FSBank в плагин не кладём (по копии EULA из стороннего репозитория редистрибуция не разрешена; официальный экземпляр не проверялся) — пользователь ставит сам |
| C. Готовые GUI (Fmod-Bank-Tools, FenixProFmod, openFmodBank, IZH318) | под капотом FSBank и тот же патч | быстро руками | GUI, Windows; требуют тот же тип файла и длительность «такую же или меньше» |
| D. Пересборка в FMOD Studio | нужен исходный проект `.fspro` | официально | для чужой игры нереально |
| E. Свой Vorbis | libvorbis с попаданием в известный setup ID | размер как у оригинала | риск несовпадения setup-заголовка; не стоит усилий |

Особенности пути A:

- **Кодек один на весь FSB**, поэтому перекодируется весь FSB, включая SFX и звук видео, лежащие рядом с репликами. Незаменённые сабсаунды декодируются из оригинала и кодируются в тот же новый кодек.
- Хвост заголовка FSB (`0x1C..0x3B`) лучше сохранить из оригинала; нули тоже работают.
- `babeldub build --codec auto|pcm16|fadpcm`. Размер FSB #1 Low-Budget Repairs (140,7 мин): Vorbis-оригинал 83,5 МБ; PCM16 48 кГц 1115 МБ; PCM16 24 кГц 558 МБ; FADPCM 48 кГц 305 МБ; FADPCM 24 кГц 153 МБ.
- По процессору FADPCM дешевле Vorbis (бенчмарк FMOD для Windows: 3,10 % против 6,88 % DSP).

## Проверка и установка

- `babeldub verify`: структура банка, `SNDH`, число и порядок сабсаундов, декодирование.
- Загрузка банка библиотекой самой игры (`verify --game-runtime`) — только по явной просьбе пользователя.
- **Какой банк грузит игра**, решает её код. Новый `Dialogue_ru-RU.bank` заработает, только если игра его загружает; надёжнее подменить содержимое банка той локали, которую игра реально грузит (в Low-Budget Repairs — `Dialogue_en-US.bank`, потому что меню звука знает только `en-US` и `pl-PL`). Подмена делается `babeldub install` с бэкапом.
- **В запущенной игре пересобранный банк не проверялся.** Если звука нет: `babeldub uninstall` → `babeldub build --codec pcm16` → `verify` → `install`.
- Контрольных сумм у FMOD-банков нет (в отличие от бандлов Unity Addressables).

## Рантайм-альтернатива

Плагин BepInEx 6 (IL2CPP) или MelonLoader перехватывает колбэк programmer sound или `getSoundInfo` и отдаёт `createSound("<файл реплики>", CREATESTREAM)`; FMOD Core читает OGG, WAV и FLAC напрямую. В тестовой игре не проверялось. Минусы: мод-лоадер ставит пользователь, хрупкость к обновлениям.

## Подводные камни

- Синхронные инструменты, режим Cut, таймлайны катсцен обрезают длинные реплики.
- Кодек один на весь FSB: меняется звучание и соседних SFX (в PCM16 — без потерь; FADPCM — сжатие с потерями).
- Ключ шифрования банка.
- Распространять собранный банк нельзя: в нём чужие ассеты.

## Ссылки

- vgmstream — https://github.com/vgmstream/vgmstream (`src/meta/fsb5.c`, `src/meta/fsb5_fev.c`, `src/coding/fadpcm_decoder.c`, `src/coding/libs/vorbis_codebooks_fsb.h`)
- FMOD Studio: Dialogue and Localization — https://www.fmod.com/docs/2.03/studio/dialogue-and-localization.html
- FMOD Studio API Guide, 13.9 — https://www.fmod.com/docs/2.03/api/studio-guide.html
- FMOD Instrument Reference (Async, Cut) — https://www.fmod.com/docs/2.03/studio/instrument-reference.html
- FSBank API — https://www.fmod.com/docs/2.03/api/fsbank-api.html ; Platform Details Windows — https://www.fmod.com/docs/2.03/api/platforms-win.html
- FMOD for Unity Settings — https://www.fmod.com/docs/2.03/unity/settings.html ; лицензирование — https://www.fmod.com/licensing
- python-fsb5 — https://github.com/HearthSim/python-fsb5 ; Fmod5Sharp — https://github.com/SamboyCoding/Fmod5Sharp
- Fmod-Bank-Tools — https://github.com/Wouldubeinta/Fmod-Bank-Tools ; FSB-BANK-Extractor-Rebuilder — https://github.com/IZH318/FSB-BANK-Extractor-Rebuilder
