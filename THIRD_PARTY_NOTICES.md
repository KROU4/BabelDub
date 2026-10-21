# Сторонние компоненты и лицензии

Код BabelDub распространяется по лицензии MIT (см. [LICENSE](LICENSE)). В репозитории и релизных архивах **нет** чужих бинарников, весов моделей и файлов игр: всё перечисленное ниже пользователь получает сам — через `uv` и PyPI при установке CLI или командой `babeldub setup`, которая скачивает инструменты и модели в `BABELDUB_HOME` и перед загрузкой показывает лицензию каждой модели.

Этот файл — справка, а не юридическая консультация. Действуют лицензии в первоисточниках по ссылкам; при расхождении верны они. Актуальный перечень моделей с лицензиями выводит `babeldub models list` (каталог `runtime/src/babeldub/data/models.yaml`).

## Python-зависимости CLI

Ставятся из PyPI вместе с `babeldub` и не входят в репозиторий.

| Пакет | Лицензия | Примечание |
|---|---|---|
| [NumPy](https://numpy.org/) | BSD-3-Clause | |
| [soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause | колёса содержат libsndfile (LGPL-2.1-or-later), подключается динамически |
| [PyYAML](https://pyyaml.org/) | MIT | |
| [UnityPy](https://github.com/K0lb3/UnityPy) | MIT | чтение ассетов Unity; тянет свои зависимости (Pillow, texture2ddecoder, etcpak, fmod-toolkit и др.) с собственными лицензиями |
| [fmod-toolkit](https://pypi.org/project/fmod-toolkit/) / [pyfmodex](https://github.com/tyrylu/pyfmodex) | обёртки: MIT; библиотеки FMOD — проприетарная лицензия FMOD Engine (Firelight Technologies) | приходят как зависимости UnityPy; BabelDub их не распространяет. FMOD — товарный знак Firelight Technologies Pty Ltd |
| [pytest](https://pytest.org/), [ruff](https://docs.astral.sh/ruff/) | MIT | только для разработки |

## Внешние инструменты

CLI вызывает их как отдельные программы. BabelDub их не вендорит; `babeldub setup --tools` скачивает официальные сборки в `BABELDUB_HOME/tools`, остальное пользователь ставит сам.

| Инструмент | Лицензия | Как используется |
|---|---|---|
| [vgmstream](https://github.com/vgmstream/vgmstream) (`vgmstream-cli`) | собственная разрешительная лицензия в стиле ISC; сборки включают сторонние декодеры (в том числе FFmpeg, LGPL) | декодирование игрового аудио (FSB5, Wwise и др.) |
| [FFmpeg](https://ffmpeg.org/) (`ffmpeg`, `ffprobe`) | LGPL-2.1-or-later или GPL — в зависимости от сборки | конвертация, `atempo` при подгонке длительности |
| [Rubber Band](https://breakfastquay.com/rubberband/) (CLI `rubberband`) | **GPL-2.0-or-later** (коммерческая лицензия у авторов) | качественное растяжение по времени; **не вендорится**, используется только если пользователь установил его сам, иначе CLI переходит на `ffmpeg atempo` |
| [uv](https://github.com/astral-sh/uv) | MIT или Apache-2.0 | установка CLI и окружений движков |
| [PyTorch](https://pytorch.org/) | BSD-3-Clause | в отдельных venv движков (`BABELDUB_HOME/envs`) |

## Модели (скачиваются по требованию)

Веса не входят в BabelDub. Лицензия модели действует и для звука, который она создаёт, в той мере, в какой это предусмотрено её условиями. Модели с некоммерческой лицензией в каталог по умолчанию не входят и включаются только явным согласием пользователя.

### Каталог версии 0.1

| Модель | Назначение | Лицензия весов | Коммерческое использование |
|---|---|---|---|
| [Qwen3-TTS-12Hz-1.7B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-Base), [0.6B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) (пакет `qwen-tts`) | клонирование голоса, движок по умолчанию | Apache-2.0 | да |
| [siriusfreak/qwen3-tts-12hz-1.7b-ru-stress-cf](https://huggingface.co/siriusfreak/qwen3-tts-12hz-1.7b-ru-stress-cf) | русские ударения для Qwen3-TTS | Apache-2.0 | да, но автор предупреждает, что часть обучающих текстов взята из датасета без объявленной лицензии: проверьте перед коммерческим использованием |
| [Chatterbox Multilingual V3](https://huggingface.co/ResembleAI/chatterbox) (Resemble AI) | клонирование для pl, tr и других языков | MIT | да; каждый файл содержит неслышимый водяной знак PerTh |
| [faster-whisper large-v3-turbo](https://huggingface.co/dropbox-dash/faster-whisper-large-v3-turbo) ([faster-whisper](https://github.com/SYSTRAN/faster-whisper)) | распознавание речи для QA | MIT | да |
| [SpeechBrain ECAPA-TDNN](https://huggingface.co/speechbrain/spkrec-ecapa-voxceleb) ([SpeechBrain](https://github.com/speechbrain/speechbrain)) | сходство голоса с оригиналом, кластеризация голосов | Apache-2.0 | да |

Пакеты окружений: `qwen-tts` (Apache-2.0), `chatterbox-tts` (MIT), `faster-whisper` (MIT), `speechbrain` (Apache-2.0), `jiwer` (Apache-2.0), `num2words` (LGPL-2.1).

### Модели, которые могут появиться как опции

Для справки: исследование BabelDub рассматривало и другие модели. Если они будут добавлены, то только с указанной лицензией и, для некоммерческих, только по явному согласию.

| Модель | Лицензия весов |
|---|---|
| OmniVoice (k2-fsa) | код Apache-2.0, **веса CC-BY-NC** (некоммерческая) |
| VoxCPM2, FireRedTTS3, Fun-CosyVoice3, MOSS-TTS | Apache-2.0 |
| GigaAM-v3 (ASR для русского), RUAccent (ударения), UTMOSv2 | MIT |
| F5-TTS и русские дообучения | код MIT, **веса CC-BY-NC** |
| XTTS-v2 | **Coqui Public Model License (некоммерческая)** |
| Fish Audio S2-Pro | **Fish Audio Research License (некоммерческая)** |
| Seed-VC | **GPL-3.0** |

## Справочные материалы и прототипы

Разбор форматов (FSB5, FADPCM, банки FMOD, таблицы Unity Localization) реализован по открытым описаниям и собственным исследованиям проекта. Код других проектов под GPL и AGPL в BabelDub не копировался.

## Товарные знаки

Unity, Unreal Engine, FMOD, Wwise, Godot, Ren'Py, Steam и названия игр — товарные знаки их владельцев. BabelDub не связан с ними и не одобрен ими.
