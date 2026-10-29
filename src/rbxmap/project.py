"""Read a Rojo project file and map script files to Roblox instance paths (read-only)."""
from __future__ import annotations

import fnmatch
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

SEP = " > "
SCRIPT_CLASSES = {"Script", "LocalScript", "ModuleScript"}
REMOTE_CLASSES = {"RemoteEvent", "RemoteFunction", "UnreliableRemoteEvent", "BindableEvent", "BindableFunction"}
COMMON_SERVICES = [
    "Workspace", "Lighting", "ReplicatedFirst", "ReplicatedStorage", "ServerScriptService",
    "ServerStorage", "StarterGui", "StarterPack", "StarterPlayer", "SoundService", "Teams",
]
SKIP_DIRS = {".git", ".rbxmap", "node_modules", ".vscode"}


def posix(p) -> str:
    """Forward slashes on every OS."""
    return str(p).replace("\\", "/")


def norm_part(part: str) -> str:
    """Case- and space-insensitive form of one path segment ("Lobby Builder" == "lobbybuilder")."""
    return "".join(part.split()).lower()


def rbx_key(path) -> str:
    """Normalized key for a Roblox path given as 'A > B > C' text or a list of names."""
    parts = path.split(">") if isinstance(path, str) else path
    parts = [norm_part(p) for p in parts if p.strip()]
    if parts and parts[0] == "game":
        parts = parts[1:]
    return ">".join(parts)


def classify(filename: str):
    """Return (instance name, class) for a script file name, or None if not a script."""
    low = filename.lower()
    for ext in (".luau", ".lua"):
        if low.endswith(ext):
            stem = filename[: -len(ext)]
            break
    else:
        return None
    sl = stem.lower()
    for suffix, cls in ((".server", "Script"), (".client", "LocalScript"), (".plugin", "Script"),
                        (".legacy", "Script"), (".local", "LocalScript")):
        if sl.endswith(suffix):
            return stem[: -len(suffix)], cls
    return stem, "ModuleScript"


@dataclass
class ScriptEntry:
    fs_path: str  # relative to project root, forward slashes
    rbx: list
    cls: str

    @property
    def rbx_path(self) -> str:
        return SEP.join(self.rbx)


@dataclass
class Project:
    root: Path
    project_file: Path
    scripts: list = field(default_factory=list)
    remote_defs: list = field(default_factory=list)  # (rbx path, className, project file)
    not_indexed: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    source_dirs: list = field(default_factory=list)


def find_project_file(start: Path) -> Path | None:
    """Walk up from start looking for default.project.json (or any *.project.json)."""
    start = Path(start).resolve()
    for d in [start, *start.parents]:
        if (d / "default.project.json").is_file():
            return d / "default.project.json"
        others = sorted(d.glob("*.project.json"))
        if others:
            return others[0]
    return None


class _Reader:
    def __init__(self, root: Path, project_file: Path):
        self.p = Project(root=root, project_file=project_file)
        self.seen_projects: set = set()
        self.seen_files: set = set()
        self.ignore: list = []

    def rel(self, path: Path) -> str:
        try:
            return posix(os.path.relpath(path, self.p.root))
        except ValueError:  # different drive on Windows
            return posix(path)

    def load_project(self, pfile: Path, rbx: list):
        pfile = pfile.resolve()
        if pfile in self.seen_projects:
            self.p.warnings.append(f"project file included twice, skipped: {self.rel(pfile)}")
            return
        self.seen_projects.add(pfile)
        try:
            data = json.loads(pfile.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            self.p.warnings.append(f"cannot read {self.rel(pfile)}: {e}")
            return
        base_rel = self.rel(pfile.parent)
        for g in data.get("globIgnorePaths", []) or []:
            self.ignore.append(posix(g) if base_rel in (".", "") else f"{base_rel}/{posix(g)}")
        self.node(data.get("tree", {}) or {}, rbx, pfile.parent)

    def node(self, node: dict, rbx: list, base: Path):
        cls = node.get("$className")
        if cls in REMOTE_CLASSES and rbx:
            self.p.remote_defs.append((SEP.join(rbx), cls, self.rel(self.p.project_file)))
        path_val = node.get("$path")
        if isinstance(path_val, dict):
            path_val = path_val.get("optional")
        if path_val:
            self.path(base / path_val, rbx)
        children = {k: v for k, v in node.items() if not k.startswith("$") and isinstance(v, dict)}
        if not path_val and not children and rbx and cls not in REMOTE_CLASSES:
            what = "properties only" if "$properties" in node else "no $path"
            self.p.not_indexed.append(f"{SEP.join(rbx)} ({what} in project file; Studio content not indexed)")
        for k, v in children.items():
            self.node(v, rbx + [k], base)

    def path(self, target: Path, rbx: list):
        if not target.exists():
            self.p.warnings.append(f"$path not found: {self.rel(target)}")
            return
        if target.is_file():
            if target.name.lower().endswith(".project.json"):
                self.load_project(target, rbx)
                return
            c = classify(target.name)
            if c:
                self.add(target, rbx, c[1], target.with_name(c[0] + ".meta.json"))
            return
        nested = target / "default.project.json"
        if nested.is_file() and nested.resolve() not in self.seen_projects and nested.resolve() != self.p.project_file.resolve():
            self.load_project(nested, rbx)
            return
        self.p.source_dirs.append(self.rel(target))
        self.walk_dir(target, rbx)

    def ignored(self, path: Path) -> bool:
        rel = self.rel(path)
        return any(fnmatch.fnmatch(rel, pat) for pat in self.ignore)

    def walk_dir(self, d: Path, rbx: list):
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name.lower())
        except OSError as e:
            self.p.warnings.append(f"cannot list {self.rel(d)}: {e}")
            return
        init = None
        for e in entries:
            c = classify(e.name) if e.is_file() else None
            if c and c[0].lower() == "init":
                init = e
                self.add(Path(e.path), rbx, c[1], d / "init.meta.json")
                break
        for e in entries:
            if e is init or e.name in SKIP_DIRS or self.ignored(Path(e.path)):
                continue
            if e.is_dir():
                sub = Path(e.path)
                if (sub / "default.project.json").is_file():
                    self.load_project(sub / "default.project.json", rbx + [e.name])
                else:
                    self.walk_dir(sub, rbx + [e.name])
            elif e.is_file():
                low = e.name.lower()
                if low.endswith(".project.json") and low != "default.project.json":
                    self.load_project(Path(e.path), rbx + [e.name[: -len(".project.json")]])
                    continue
                c = classify(e.name)
                if c:
                    self.add(Path(e.path), rbx + [c[0]], c[1], d / (c[0] + ".meta.json"))

    def add(self, path: Path, rbx: list, cls: str, meta: Path | None = None):
        if meta is not None and meta.is_file():
            try:
                hint = json.loads(meta.read_text(encoding="utf-8-sig")).get("className")
                if hint in SCRIPT_CLASSES:
                    cls = hint
            except (OSError, ValueError):
                self.p.warnings.append(f"bad meta file: {self.rel(meta)}")
        rel = self.rel(path)
        if rel.lower() in self.seen_files:
            return
        self.seen_files.add(rel.lower())
        self.p.scripts.append(ScriptEntry(rel, list(rbx), cls))


def load_project(root: Path, project_file: Path | None = None) -> Project:
    root = Path(root).resolve()
    pfile = Path(project_file) if project_file else find_project_file(root)
    if pfile is None or not pfile.is_file():
        raise FileNotFoundError(f"No default.project.json found in {posix(root)}")
    r = _Reader(root, pfile.resolve())
    r.load_project(pfile, [])
    covered = {s.rbx[0].lower() for s in r.p.scripts if s.rbx}
    listed = {n.split(SEP)[0].split(" (")[0].lower() for n in r.p.not_indexed}
    for svc in COMMON_SERVICES:
        if svc.lower() not in covered and svc.lower() not in listed:
            r.p.not_indexed.append(f"{svc} (not in project files; anything there exists only in Studio)")
    return r.p
