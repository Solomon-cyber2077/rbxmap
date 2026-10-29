"""SQLite + FTS5 index at .rbxmap/index.db. Incremental: only files whose content hash changed are rescanned."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from . import scanner
from .project import SEP, load_project, posix, rbx_key
from .safety import safe_write

SCHEMA_VERSION = "3"
DEFAULT_ALIASES = {
    "lobby": ["LobbyBuilder", "Lobby"],
    "lighting": ["Lighting", "Atmosphere", "ColorCorrection", "Bloom", "Brightness", "ClockTime", "Ambient", "FogEnd", "FogColor"],
    "shop": ["Shop", "Purchase", "MarketplaceService"],
    "ui": ["ScreenGui", "Frame"],
    "gui": ["ScreenGui", "Frame", "TextLabel"],
    "data": ["DataStore"],
    "save": ["DataStore", "SetAsync", "UpdateAsync"],
    "sky": ["Sky", "Skybox", "Atmosphere", "Clouds"],
    "fog": ["FogEnd", "FogStart", "FogColor", "Atmosphere"],
    "sound": ["Sound", "SoundService", "SoundId"],
    "music": ["Sound", "SoundService", "SoundId"],
    "leaderboard": ["leaderstats", "Leaderboard", "OrderedDataStore"],
    "money": ["Coins", "Cash", "Currency", "leaderstats"],
    "purchase": ["MarketplaceService", "ProcessReceipt", "PromptProductPurchase", "GamePass"],
    "gamepass": ["GamePass", "UserOwnsGamePassAsync"],
    "camera": ["Camera", "CurrentCamera"],
    "input": ["UserInputService", "ContextActionService", "InputBegan"],
    "controls": ["UserInputService", "ContextActionService", "InputBegan"],
    "animation": ["Animator", "AnimationTrack", "LoadAnimation"],
    "spawn": ["SpawnLocation", "CharacterAdded", "Spawn"],
    "remote": ["RemoteEvent", "RemoteFunction", "FireServer", "OnServerEvent"],
    "network": ["RemoteEvent", "FireServer", "FireClient"],
    "tween": ["TweenService", "TweenInfo"],
    "effects": ["ParticleEmitter", "Beam", "Trail", "Bloom"],
    "vfx": ["ParticleEmitter", "Beam", "Trail"],
    "teleport": ["TeleportService"],
    "badge": ["BadgeService", "AwardBadge"],
    "player": ["Players", "PlayerAdded", "Character"],
}
DEFAULT_CONFIG = {
    "_help": "remote_wrappers maps your own remote helper calls to a kind, e.g. {\"Net.Fire\": \"fire_server\", \"Net.On\": \"on_client_event\"}. Kinds: fire_server, fire_client, on_server_event, on_client_event, invoke_server, on_server_invoke.",
    "remote_wrappers": {},
}


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def ensure_rbxmap_dir(root: Path):
    d = root / ".rbxmap"
    d.mkdir(exist_ok=True)
    if not (d / "aliases.json").exists():
        safe_write(root, ".rbxmap/aliases.json", json.dumps(DEFAULT_ALIASES, indent=2) + "\n")
    if not (d / "descriptions.json").exists():
        safe_write(root, ".rbxmap/descriptions.json",
                   json.dumps({"_help": "Map a Roblox path or file path to your own description. It overrides the auto one.",
                               "ServerScriptService > Example": "What this script does"}, indent=2) + "\n")
    if not (d / "config.json").exists():
        safe_write(root, ".rbxmap/config.json", json.dumps(DEFAULT_CONFIG, indent=2) + "\n")
    if not (d / ".gitignore").exists():
        safe_write(root, ".rbxmap/.gitignore", "index.db*\ntmp/\n")


def auto_desc(info: dict) -> str:
    named = [f["name"].split(":")[-1].split(".")[-1] for f in info["functions"] if f["kind"] in ("local", "function", "assign")]
    uses = [t for t in info["tags"] if t not in ("Remotes",)][:4]
    parts = []
    if uses:
        parts.append("Uses " + ", ".join(uses))
    if named:
        parts.append("functions: " + ", ".join(list(dict.fromkeys(named))[:4]))
    return "; ".join(parts) or "No functions or known services found"


class Index:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        ensure_rbxmap_dir(self.root)
        self.db = sqlite3.connect(str(self.root / ".rbxmap" / "index.db"), timeout=15, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA busy_timeout=15000")
        self._schema()
        self.project = None

    def _schema(self):
        db = self.db
        db.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
        row = db.execute("SELECT v FROM meta WHERE k='schema'").fetchone()
        if row is None or row[0] != SCHEMA_VERSION:
            for t in ("scripts", "functions", "requires", "remotes", "fts"):
                db.execute(f"DROP TABLE IF EXISTS {t}")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS scripts(id INTEGER PRIMARY KEY, fs_path TEXT UNIQUE, rbx_path TEXT, rbx_key TEXT,
            name TEXT, class TEXT, lines INT, bytes INT, hash TEXT, mtime_ns INT, descr TEXT, desc_auto INT,
            tags TEXT, services TEXT);
        CREATE TABLE IF NOT EXISTS functions(script_id INT, name TEXT, kind TEXT, start INT, end_line INT);
        CREATE TABLE IF NOT EXISTS requires(script_id INT, expr TEXT, line INT, target_key TEXT, last TEXT);
        CREATE TABLE IF NOT EXISTS remotes(script_id INT, name TEXT, kind TEXT, line INT);
        CREATE INDEX IF NOT EXISTS fn_sid ON functions(script_id);
        CREATE INDEX IF NOT EXISTS rq_sid ON requires(script_id);
        CREATE INDEX IF NOT EXISTS rm_sid ON remotes(script_id);
        CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(rbx, name, descr, tags, funcs, body, tokenize='porter unicode61');
        """)
        db.execute("INSERT OR REPLACE INTO meta VALUES('schema', ?)", (SCHEMA_VERSION,))
        db.commit()

    def config(self) -> dict:
        return read_json(self.root / ".rbxmap" / "config.json", DEFAULT_CONFIG)

    def aliases(self) -> dict:
        data = read_json(self.root / ".rbxmap" / "aliases.json", DEFAULT_ALIASES)
        return {k.lower(): v for k, v in data.items() if isinstance(v, list)}

    def descriptions(self) -> dict:
        data = read_json(self.root / ".rbxmap" / "descriptions.json", {})
        return {(rbx_key(k) if ">" in k else posix(k).lower()): v for k, v in data.items()
                if isinstance(v, str) and not k.startswith("_")}

    def _settings_hash(self) -> str:
        blob = b""
        for name in ("config.json", "descriptions.json"):
            try:
                blob += (self.root / ".rbxmap" / name).read_bytes()
            except OSError:
                pass
        return file_hash(blob)

    def refresh(self, force: bool = False) -> dict:
        """Cheap stat check of every script; rescan only files whose hash changed; drop deleted files."""
        self.project = load_project(self.root)
        db = self.db
        settings = self._settings_hash()
        row = db.execute("SELECT v FROM meta WHERE k='settings'").fetchone()
        if row is None or row[0] != settings:
            force = True
        wrappers = self.config().get("remote_wrappers") or {}
        descs = self.descriptions()
        existing = {r["fs_path"]: r for r in db.execute("SELECT id, fs_path, rbx_path, class, hash, mtime_ns, bytes FROM scripts")}
        added, updated, unchanged = [], [], 0
        seen = set()
        for entry in self.project.scripts:
            seen.add(entry.fs_path)
            full = self.root / entry.fs_path
            try:
                st = full.stat()
            except OSError:
                continue
            old = existing.get(entry.fs_path)
            same_place = old is not None and old["rbx_path"] == entry.rbx_path and old["class"] == entry.cls
            if old is not None and not force and same_place and old["mtime_ns"] == st.st_mtime_ns and old["bytes"] == st.st_size:
                unchanged += 1
                continue
            data = full.read_bytes()  # read-only
            h = file_hash(data)
            if old is not None and not force and same_place and old["hash"] == h:
                db.execute("UPDATE scripts SET mtime_ns=?, bytes=? WHERE id=?", (st.st_mtime_ns, st.st_size, old["id"]))
                unchanged += 1
                continue
            self._store(entry, data, h, st, wrappers, descs, old["id"] if old is not None else None)
            (updated if old is not None else added).append(entry.fs_path)
        removed = [p for p in existing if p not in seen]
        for p in removed:
            sid = existing[p]["id"]
            for t in ("functions", "requires", "remotes"):
                db.execute(f"DELETE FROM {t} WHERE script_id=?", (sid,))
            db.execute("DELETE FROM fts WHERE rowid=?", (sid,))
            db.execute("DELETE FROM scripts WHERE id=?", (sid,))
        db.execute("INSERT OR REPLACE INTO meta VALUES('settings', ?)", (settings,))
        db.commit()
        return {"added": added, "updated": updated, "removed": removed, "unchanged": unchanged,
                "total": len(self.project.scripts)}

    def _store(self, entry, data: bytes, h: str, st, wrappers: dict, descs: dict, sid):
        db = self.db
        text = data.decode("utf-8", errors="replace")
        info = scanner.scan(text, entry.rbx, wrappers)
        manual = descs.get(rbx_key(entry.rbx)) or descs.get(entry.fs_path.lower())
        if manual:
            desc, auto = manual, 0
        elif info["desc"]:
            desc, auto = info["desc"], 0
        else:
            desc, auto = auto_desc(info), 1
        name = entry.rbx[-1] if entry.rbx else Path(entry.fs_path).stem
        row = (entry.fs_path, entry.rbx_path, rbx_key(entry.rbx), name, entry.cls, info["lines"], st.st_size, h,
               st.st_mtime_ns, desc, auto, " ".join(info["tags"]), " ".join(info["services"]))
        if sid is None:
            cur = db.execute("INSERT INTO scripts(fs_path, rbx_path, rbx_key, name, class, lines, bytes, hash, mtime_ns,"
                             " descr, desc_auto, tags, services) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", row)
            sid = cur.lastrowid
        else:
            db.execute("UPDATE scripts SET fs_path=?, rbx_path=?, rbx_key=?, name=?, class=?, lines=?, bytes=?, hash=?,"
                       " mtime_ns=?, descr=?, desc_auto=?, tags=?, services=? WHERE id=?", row + (sid,))
            for t in ("functions", "requires", "remotes"):
                db.execute(f"DELETE FROM {t} WHERE script_id=?", (sid,))
            db.execute("DELETE FROM fts WHERE rowid=?", (sid,))
        db.executemany("INSERT INTO functions VALUES(?,?,?,?,?)",
                       [(sid, f["name"], f["kind"], f["start"], f["end"]) for f in info["functions"]])
        db.executemany("INSERT INTO requires VALUES(?,?,?,?,?)",
                       [(sid, r["expr"], r["line"], rbx_key(r["target"]) if r["target"] else None, r["last"])
                        for r in info["requires"]])
        db.executemany("INSERT INTO remotes VALUES(?,?,?,?)",
                       [(sid, r["name"], r["kind"], r["line"]) for r in info["remotes"]])
        funcs = " ".join(f["name"] for f in info["functions"] if f["name"] != "<anon>")
        fs_words = entry.fs_path.replace("/", " ").replace(".", " ")
        db.execute("INSERT INTO fts(rowid, rbx, name, descr, tags, funcs, body) VALUES(?,?,?,?,?,?,?)",
                   (sid, scanner.expand_words(entry.rbx_path + " " + fs_words), scanner.expand_words(name),
                    scanner.expand_words(desc), scanner.expand_words(" ".join(info["tags"] + info["services"])),
                    scanner.expand_words(funcs), scanner.expand_words(text)))

    # ---- small query helpers -------------------------------------------------
    def all_scripts(self):
        return self.db.execute("SELECT * FROM scripts ORDER BY rbx_path COLLATE NOCASE").fetchall()

    def script(self, sid: int):
        return self.db.execute("SELECT * FROM scripts WHERE id=?", (sid,)).fetchone()

    def functions(self, sid: int):
        return self.db.execute("SELECT * FROM functions WHERE script_id=? ORDER BY start", (sid,)).fetchall()

    def read_lines(self, fs_path: str) -> list:
        with open(self.root / fs_path, "rb") as f:  # read-only
            return f.read().decode("utf-8", errors="replace").splitlines()

    def counts(self) -> dict:
        n = self.db.execute("SELECT COUNT(*) FROM scripts").fetchone()[0]
        return {"scripts": n, "db": posix(os.path.relpath(self.root / ".rbxmap" / "index.db", self.root))}

