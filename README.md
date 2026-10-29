# rbxmap

Find the right Roblox script, and the right lines in it, instantly.

rbxmap reads your Rojo project (`default.project.json`), indexes every `.lua`/`.luau` file into a local
SQLite search index, and answers plain-word questions like "lobby lighting" or "buy item remote" with the
script path and the line range to look at.

- Fully local. No internet access, no telemetry.
- Read-only toward your game code. It never creates, edits or deletes `.lua`/`.luau` files. It only writes
  `.rbxmap/` (the index), `MAP.md` and `DESIGN.md` in your project folder.
- Understands Roblox paths (`ServerScriptService > Lobby > Builder`), sloppy paths and Windows paths.

## Install

Needs Python 3.11 or newer (the python.org installer includes everything required).

1. Download this repository (green **Code** button, then **Download ZIP**) and unzip it.
2. Open a terminal in the unzipped folder and run:

```
python -m pip install .
```

3. Check it works:

```
rbxmap doctor
```

## Use

Run these from inside your Roblox project folder, or add `--project "C:/path/to/game"`.

| Command | What it does |
| --- | --- |
| `rbxmap init` | First run: builds the index and writes `MAP.md` and `DESIGN.md` |
| `rbxmap find "lobby lighting"` | Best matching scripts with the line range to read |
| `rbxmap lines <path> 40 80` | Print lines 40 to 80 of a script (max 150 lines) |
| `rbxmap outline <path>` | Functions, remotes and requires in a script |
| `rbxmap symbol BuyItem` | Where a function, remote or service is defined and used |
| `rbxmap deps <path>` | What a script requires and who requires it |
| `rbxmap map [folder]` | Compact map of every script |
| `rbxmap remotes` | Writes a RemoteEvent table (who fires, who handles) into `DESIGN.md` |
| `rbxmap index` | Re-index changed files (searches do this automatically) |
| `rbxmap check` | Runs `rojo build`, `selene` and `stylua --check` if they are installed |
| `rbxmap doctor` | Checks your setup and prints exact fixes |

Add `--json` to any command for machine-readable output.

## Good to know

- Rojo only syncs files. Anything that exists only in Studio (models, UI built in Studio, properties set in
  Studio) is not in the index.
- Custom remote helpers (for example `Net.Fire`) can be added in `.rbxmap/config.json` under
  `remote_wrappers`.
- Write your own one-line script descriptions in `.rbxmap/descriptions.json`.

## License

Use-only: you may download and use rbxmap, but not modify or redistribute it. See [LICENSE](LICENSE).
