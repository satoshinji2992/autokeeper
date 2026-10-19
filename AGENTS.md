# Repository Guidelines

## Project Structure & Module Organization

AutoKeeper runs Chinese Call of Cthulhu 7th-edition games on Python 3.12+.

- `src/autokeeper/`: `engine/` manages turns/state; `agents/` and `harness/` handle models; `rules/` implements mechanics; `plugins/` runs tested extensions; `net/` supports multiplayer; `module/` handles scenarios (`module/package.py` is the on-disk layout of one); `world/` is the persistent world record. `paths.py` owns every location on disk: ask it instead of joining paths yourself.
- `src/autokeeper/tui/`: `app.py` (the Textual app) and `library.py` (save index, module loading, `art_text`); `screens/` full pages (title, setup, game, cards, LAN host, lobby, saves, import); `modals/` dialogs over a screen; `widgets/` panels, speaker, pages, tips; `drawing/` title art, cover and dice. Each `.tcss` sits beside its code.
- `bridge/`: Node.js JSON-lines bridge to BCDice.
- `tests/`: tests, `conftest.py` fixtures (every test gets its own `paths.DATA`, `paths.DEV_OUTPUT` and `paths.USER_DIR`), and `cassettes/` recordings.
- `modules/<id>/`: the three bundled scenarios: `module.yaml` plus `places.yaml`, `people.yaml`, `clues.yaml`, `story.yaml`, `plugins/`, and `art/{places,people,items,ascii}/` with one `art/credits.yaml`. `src/autokeeper/module/example/` is the format fixture. Never commit derived files into a module (prepared pictures, compiled skills); they live under `data/cache/` and `skills/module-*`.
- `skills/`, `agents/`, `commands/`, `.claude-plugin/` and `.mcp.json`: the Claude Code plugin (paths fixed by the plugin format). Shared skills are tracked; `skills/module-*` are compiled and ignored. `docs/` contains technical documentation; `docs/img/` holds screenshots.
- `dev/`: developer scripts (public-share tunnel); their output and eval/autoplay reports go to ignored `dev/output/`.

## Build, Test, and Development Commands

Prerequisites: Python 3.12+, uv, Node.js 18+.

- `uv sync --extra claude`: install dependencies.
- `npm ci --prefix bridge --no-audit --no-fund`: install the locked dice-bridge dependencies.
- `uv run autokeeper doctor`: inspect the local environment.
- `uv run autokeeper`: launch the terminal game; `uv run autokeeper serve` starts browser play.
- `uv build`: build Python distributions with Hatchling.
- `uv run ruff check src tests`: run lint and import-order checks.
- `uv run pytest -q`: run the test suite.
- `uv run autokeeper check src/autokeeper/module/example`: validate the format example.
- `uv run agentskills validate skills/<name>`: validate a skill.

## Coding Style & Naming Conventions

Use four-space indentation, annotate new interfaces, and use `snake_case` functions/modules, `PascalCase` classes, and `UPPER_CASE` constants. Preserve Chinese UI terminology and the colored ASCII aesthetic. Retain original module images and readable text; unlock spoiler images with their clues. Ruff targets 120-character lines and checks imports, modernization, and bugs; its configuration permits Chinese punctuation. Keep Textual styles in `.tcss` files. Write concise, professional Chinese UI copy: state the action, status or outcome directly; avoid chatty phrasing, personified loading messages, redundant instructions and generic requests to rephrase after technical errors. Put detailed rules in help, and avoid internal agent names, marketing claims, or tactical coaching. Preserve the narrative voice in story text and NPC dialogue.

## Testing Guidelines

Use pytest and pytest-asyncio (automatic async mode). Name files `test_<area>.py` and functions `test_<behavior>`. Use scripted agents, `FakeDice`, recordings, and temporary directories. BCDice tests skip without its Node bridge. No numeric coverage threshold; add regression tests for behavior changes.

## Commit & Pull Request Guidelines

Use focused commits with concise Chinese messages, optionally prefixed by an area such as `地图：`. PRs must describe behavior changes, linked issues, and validation. Include screenshots for UI/artwork changes. Document changes to commands, configuration, and module formats.

## Configuration & Local Data

Keep API keys in environment variables or ignored `.env` files. User configuration lives in `~/.config/autokeeper/config.toml`. Keep game data, imported copyrighted material, and generated module skills in their ignored locations: `data/` (layout in `paths.py`), `modules/local/`, `modules/inbox/`, every module's `source/`, `dev/output/`, and `skills/module-*`.
