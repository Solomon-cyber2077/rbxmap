"""Command line interface."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .project import find_project_file, posix


def _out(obj, as_json: bool):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=1))
    else:
        print(render(obj).rstrip())


def render(obj, indent: int = 0) -> str:
    pad = " " * indent
    if isinstance(obj, dict):
        s = ""
        for k, v in obj.items():
            if isinstance(v, (dict, list)) and v:
                s += f"{pad}{k}:\n{render(v, indent + 2)}"
            elif isinstance(v, str) and "\n" in v:
                s += f"{pad}{k}:\n" + "".join(f"{pad}  {line}\n" for line in v.splitlines())
            else:
                s += f"{pad}{k}: {v if v != [] else '(none)'}\n"
        return s
    if isinstance(obj, list):
        s = ""
        for item in obj:
            if isinstance(item, dict):
                body = render(item, indent + 2)
                s += f"{pad}- {body.lstrip()}"
            else:
                s += f"{pad}- {item}\n"
        return s
    return f"{pad}{obj}\n"


def project_root(arg: str | None) -> Path:
    if arg:
        root = Path(arg).resolve()
        if not root.is_dir():
            raise SystemExit(f"Project folder not found: {posix(root)}\n"
                             "Fix: check the path in --project (use quotes if it has spaces).")
        return root
    env = os.environ.get("RBXMAP_PROJECT")
    if env:
        return Path(env).resolve()
    pf = find_project_file(Path.cwd())
    if pf is None:
        raise SystemExit("No default.project.json found here or in any parent folder.\n"
                         "Fix: cd into your Roblox project folder, or pass --project \"C:/path/to/game\". "
                         "Run `rbxmap doctor` for help.")
    return pf.parent


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    ap = argparse.ArgumentParser(prog="rbxmap", description="Fast code finder for Rojo/Luau projects.")
    ap.add_argument("--project", help="Roblox project folder (default: search upward from the current folder)")
    ap.add_argument("--json", action="store_true", help="print raw JSON")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create .rbxmap/, index, MAP.md, DESIGN.md")
    p = sub.add_parser("index", help="re-index changed files")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("find", help='find_script, e.g. rbxmap find "lobby lighting"')
    p.add_argument("query", nargs="+")
    p.add_argument("--limit", type=int, default=3)
    p = sub.add_parser("lines", help="read_lines <path> <start> <end>")
    p.add_argument("path")
    p.add_argument("start", type=int)
    p.add_argument("end", type=int)
    p = sub.add_parser("outline")
    p.add_argument("path")
    p = sub.add_parser("symbol", help="where a function/remote/service is defined and used")
    p.add_argument("name")
    p = sub.add_parser("deps", help="what a script requires / who requires it")
    p.add_argument("path")
    p = sub.add_parser("map", help="compact script map (MAP.md is regenerated automatically)")
    p.add_argument("folder", nargs="?")
    sub.add_parser("doctor", help="check setup and print exact fixes")
    sub.add_parser("remotes", help="write the RemoteEvent table into DESIGN.md")
    sub.add_parser("check", help="rojo build + selene + stylua --check (if installed)")
    argv = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in argv
    argv = [x for x in argv if x != "--json"]
    a = ap.parse_args(argv)
    a.json = a.json or as_json

    if a.cmd == "doctor":
        from .doctor import doctor
        return doctor(a.project)

    root = project_root(a.project)

    from .api import RbxMap
    app = RbxMap(root)
    if a.cmd == "init":
        from .mapgen import ensure_design, write_map, write_remotes
        res = app.refresh(force=True)
        write_map(app)
        notes = [ensure_design(root)]
        notes.append(f"remotes table: {write_remotes(app)['remotes']} remotes")
        print(f"Indexed {res['total']} scripts in {posix(root)}")
        for n in notes + ["MAP.md updated", ".rbxmap/aliases.json, descriptions.json, config.json ready"]:
            print(" -", n)
        return 0
    if a.cmd == "index":
        _out(app.refresh(force=a.force), a.json)
    elif a.cmd == "find":
        _out(app.find_script(" ".join(a.query), a.limit), a.json)
    elif a.cmd == "lines":
        r = app.read_lines(a.path, a.start, a.end)
        _out(r, a.json)
    elif a.cmd == "outline":
        _out(app.outline(a.path), a.json)
    elif a.cmd == "symbol":
        _out(app.find_symbol(a.name), a.json)
    elif a.cmd == "deps":
        _out(app.dependencies(a.path), a.json)
    elif a.cmd == "map":
        _out(app.map(a.folder), a.json)
    elif a.cmd == "remotes":
        from .mapgen import write_remotes
        res = write_remotes(app)
        _out({"remotes": res["remotes"], "written": res["written"]} if not a.json else res, a.json)
    elif a.cmd == "check":
        from .check import run_checks
        _out(run_checks(root, app.idx.project), a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
