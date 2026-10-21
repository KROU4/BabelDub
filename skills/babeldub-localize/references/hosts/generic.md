# Host: web chats and other agents

Read this if you run in a web chat (claude.ai, Claude Desktop without file access, ChatGPT web, a Custom GPT) or in an agent that has no file of its own here (Cursor, OpenCode, GitHub Copilot and others).

## 1. Agents with shell access (Cursor, OpenCode, Copilot CLI and others)

- Installing the skills: `npx skills add KROU4/BabelDub` (flag `-a <agent>`, or `-g` for all of the user's projects).
- Work as in Codex (`references/hosts/codex.md`): `babeldub` commands in the shell, perform the roles yourself in the main session from `references/roles/*.md`, translate one batch at a time.
- `${CLAUDE_PLUGIN_ROOT}` is not substituted; paths in the skills are relative to the skill folder.
- `BABELDUB_HOME` defaults to `~/.babeldub` (`C:\Users\<user>\.babeldub` on Windows); the `BABELDUB_HOME` environment variable overrides it.

## 2. Web chats: batch translation only

In a web chat the skill runs in a cloud sandbox: **no GPU, no access to the game folder or to the user's `BABELDUB_HOME`**. Voicing, extraction and building are impossible there. One stage is possible: translating batches the user exported from their machine.

Tell the user this right away, in one sentence and in their language, and offer one of two paths:

- **Full dub:** install BabelDub into a local agent (Claude Code, Codex, Gemini CLI) — the install commands are in the matching files in `references/hosts/`.
- **Translation only, in the chat:** the user runs the CLI on their own machine and brings batches into the chat.

### "Translation only" procedure

On the user's machine (the user does this):

1. `babeldub init "<game folder>" --lang <code>`, then `scan`, `extract`, `link`, `cast`, `cast select`, `translate plan`.
2. `babeldub batch make`, then `babeldub batch next` — the command shows the batch's `in` and `out` paths.
3. Upload to the chat the file `translate/batches/<bid>.in.jsonl`, plus `translate/style_bible.md` and `translate/glossary.agent.jsonl` if they exist.

In the chat (you do this):

1. Read `references/roles/translator.md` and translate the batch by its rules: the emotional shape of the original, the syllable budget, placeholders, the glossary, `dub_text` and short `variants`.
2. If there is no glossary and no style bible, first perform a reduced lexicon role (`references/roles/lexicon.md`) on the batch itself: the batch's names and terms, the formal/informal address of the scene. Give the result to the user as files to put into `translate/`.
3. Return the file `<bid>.out.jsonl`: one JSON object per line, the same `id` values, UTF-8 without BOM. If the chat can create a downloadable file, create one; if it cannot, print the content as a single code block with no comments inside.
4. The number of objects must equal the number of lines in the batch (excluding the header line).

Back on the user's machine:

1. Save the answer as `translate/batches/<bid>.out.jsonl` in the project folder (the `out` path from `batch next`).
2. `babeldub batch submit <bid>`. On code 2 or 3, bring `errors[]` or `over_budget[]` from the output into the chat; you fix the file and return it again.
3. Next batch: `babeldub batch next`.

The user runs voicing (`synth`, `post`, `qa`) and the build locally or through a local agent.

### Web chat limitations

- Long batches may not fit into one message: ask for batches of 40–60 lines (`babeldub batch make --size 40`).
- Do not promise voicing in the chat and do not imitate CLI output.
- Do not ask the user for game files: the chat needs only the batches and the glossary.
