"""rbxmap doctor: checks setup and prints exact fixes."""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

from .check import INSTALL, tool_status
from .project import find_project_file, load_project, posix


def doctor(project_arg=None) -> int:
    problems = 0

    def ok(msg):
        print(f"[ok]   {msg}")

    def bad(msg, fix):
        nonlocal problems
        problems += 1
        print(f"[FAIL] {msg}\n       fix: {fix}")

    def info(msg):
        print(f"[info] {msg}")

    v = sys.version_info
    if v >= (3, 11):
        ok(f"Python {v.major}.{v.minor}.{v.micro} ({posix(sys.executable)})")
    else:
        bad(f"Python {v.major}.{v.minor} is too old", "install Python 3.11+ from python.org, then reinstall rbxmap")
    try:
        c = sqlite3.connect(":memory:")
        c.execute("CREATE VIRTUAL TABLE t USING fts5(a)")
        ok(f"SQLite {sqlite3.sqlite_version} with FTS5")
    except sqlite3.Error:
        bad("SQLite has no FTS5", "install the official python.org Python 3.11+ (it includes FTS5)")

    root = Path(project_arg or os.environ.get("RBXMAP_PROJECT") or ".").resolve()
    pf = find_project_file(root)
    if pf is None:
        bad(f"no default.project.json in {posix(root)} or its parents",
            "cd into your game folder or pass --project; to create a Rojo project run: rojo init")
    else:
        try:
            p = load_project(pf.parent, pf)
            ok(f"project file {posix(pf)}: {len(p.scripts)} scripts under {', '.join(p.source_dirs) or '(none)'}")
            for w in p.warnings[:5]:
                info("project warning: " + w)
            if not p.scripts:
                bad("project maps no .lua/.luau files", 'add "$path": "src/server" style entries to default.project.json')
            db = pf.parent / ".rbxmap" / "index.db"
            if db.exists():
                con = sqlite3.connect(str(db))
                try:
                    n = con.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]
                    ok(f"index {posix(db)}: {n} scripts" + ("" if n == len(p.scripts) else
                                                             f" (project has {len(p.scripts)}; run: rbxmap index)"))
                except sqlite3.Error:
                    bad("index unreadable", "delete .rbxmap/index.db and run: rbxmap index")
                finally:
                    con.close()
            else:
                bad("no index yet", f'rbxmap --project "{posix(pf.parent)}" init')
        except Exception as e:  # noqa: BLE001
            bad(f"cannot read project file: {e}", "fix the JSON syntax (check commas and quotes)")
    for tool, path in tool_status().items():
        if path:
            ok(f"{tool} found ({posix(path)}) — note: rokit tools only run inside a folder whose rokit.toml lists them")
        else:
            info(f"{tool} not found (optional, used by `rbxmap check`). install: {INSTALL[tool]}")
    print("\nReminder: Studio-only content is invisible to the index.")
    print("All good." if not problems else f"{problems} problem(s) found.")
    return 1 if problems else 0
