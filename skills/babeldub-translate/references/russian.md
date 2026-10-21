# Russian in dubbing

Read this when translating or adapting into Russian. The rules apply to `dub_text`, the text the character's voice will speak; subtitles follow the usual norms. Russian examples below are sample dub text; keep them in Russian.

## 1. «Ты» and «вы» (informal and formal "you")

- **The source of truth** is the official translation (if there is one) and the style bible: a matrix of who addresses whom and how, by character pairs.
- Defaults: friends, peers, relatives, enemies in a quarrel use «ты»; unfamiliar adults, superiors, clients, elderly people and formal settings use «вы». Addressing the player in the UI and in hints follows the official translation; if there is none, use «вы».
- **Switching** from «вы» to «ты» is a plot event. Do not switch it yourself in the middle of a scene; if the original has an explicit reason (getting acquainted, a quarrel), mark it in `notes`.
- English *you* addressed to a group is «вы» (plural) regardless of the relationship.
- Polish *pan/pani* is the polite «вы»; the official translation may keep «пан» as local colour. Follow the glossary.

## 2. Gender and agreement

- Past-tense verbs, short adjectives and participles agree with the gender of the speaker («я пришёл / пришла», «я рад / рада») and of the addressee («ты готов / готова?»).
- Gender comes from casting (the character's `gender`), the style bible and the context. If the gender is unknown (`u`), phrase the line neutrally: present tense («я тут»), impersonal constructions («Готово»), «мне удалось» instead of «я смог / смогла»; mark it in `notes`.
- If the player can choose the hero's gender and one line is recorded for both, write neutrally. v0.1 has no separate per-gender text variants.

## 3. The letter «ё»

- In `dub_text`, write «ё» wherever it is pronounced («ещё», «всё», «идём», «её»): TTS reads «е» as «е», and «все» instead of «всё» changes both the meaning and the sound.
- By default the CLI keeps «ё» as written (`tts.yo=keep`). The `restore` mode restores «ё» only in a small list of unambiguous words; do not rely on it.
- In subtitles, «ё» follows the rules of the official translation.

## 4. Stress marks

- Qwen3-TTS models understand the stress mark **U+0301** (a combining acute after the stressed vowel: «за́мок» / «замо́к»). The base model, which voices by default, does not always follow it; the fine-tune `qwen3-tts-12hz-1.7b-ru-stress-cf` is more reliable but preserves the hero's voice worse and is enabled only explicitly (skill `babeldub-voice`).
- Place a stress mark **only** where an error is likely and changes the meaning or grates on the ear: homographs (за́мок / замо́к, мука́ / му́ка, до́рога / доро́га), rare names and place names, foreign words. The source is the glossary's `pronounce` field.
- Do not put stress marks on every word: it is extra work and a risk of errors.
- The stress mark does not interfere with syllable counting or the placeholder check. Do not put stress marks in `sub_text`.

## 5. Numbers, dates, units

- In `dub_text`, write them **in words and in the correct case**: «Мне нужно три детали», «к пяти часам», «двадцать первого мая», «пятьдесят процентов». Do not count on automatic conversion: in the CLI it works only if the `num2words` package is installed, and it does not guess the case.
- Time: «в семь тридцать» or «в половине восьмого», whichever is more natural for the character.
- Expand money and units: «$20» → «двадцать долларов», «5 km» → «пять километров».
- Numbers, codes and model names follow the glossary's `pronounce` («Т-34» → «тэ тридцать четыре»).

## 6. Abbreviations, acronyms, Latin script

- Pronounce acronyms the way a native speaker says them: «ФБР» → «эф-бэ-эр», «НАТО» → «нато»; the rule goes into the glossary's `pronounce`.
- Latin script in `dub_text` is undesirable: the model pronounces it with an English accent or wrongly. Transliterate brands and names per the glossary («Ford» → «Форд»).
- Expand «т. е.», «и т. д.», «г.», «ул.» in full («то есть», «и так далее»).
- Internet slang and chat abbreviations («ок», «лол»): write them the way people actually say them; «OK» → «окей» or «ладно», depending on the character.

## 7. Shortening text for dubbing

Russian text is usually 10–20% longer than English in syllables. Techniques that save syllables without losing meaning or intonation:

- **remove redundant pronouns**: «Я думаю, что я знаю» → «Кажется, знаю»;
- **short synonyms**: «необходимо» → «надо», «в настоящий момент» → «сейчас», «потому что» → «раз», «так как»;
- **colloquial forms** where the character would use them: «сейчас» → «щас» only for vernacular speech and only if the style bible allows it;
- **compress the form of address**: «Послушай меня, Мачек» → «Мачек, слушай»;
- **a verb instead of a verbal noun**: «осуществить проверку» → «проверить»;
- **incomplete sentences**, natural in live speech: «Ты куда?» instead of «Куда ты идёшь?»;
- **remove parentheticals and softeners** if they carry no character: «ну», «вообще-то», «как бы»; keep them if they are the character's manner.

Do not shorten at the expense of glossary names, negations, key quest information, or the emotion of the phrase.

## 8. Emotions, interjections, profanity

- Translate interjections into Russian ones: *Ugh* → «Тьфу» or «Фу», *Wow* → «Ого», *Hmm* → «Хм», *Ouch* → «Ай»; the table lives in the glossary.
- Render hesitations and repetitions the way they are pronounced: «Я… я не знаю», «Н-нет!»; the model renders a hyphen and an ellipsis as a pause.
- Wordless laughter, coughing and sighs are not translated (they are `keep_original` or a short interjection).
- Profanity at the same level as the original: *damn* → «чёрт», *shit* → «блин» or «чёрт» in a mild register, stronger if the original is stronger and the style bible says so. No asterisks: they cannot be pronounced.

## 9. Punctuation for the voice

- Exclamation and question marks are mandatory wherever the original has them: the model builds the intonation from them.
- An ellipsis is a pause or a cut-off; a dash is a pause before an explanation.
- Do not put quotation marks or brackets around spoken text.
- Do not split one line into several sentences where the original has one: the model adds extra pauses and the line gets longer.
