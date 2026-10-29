"""All rbxmap operations. The CLI calls this."""
from __future__ import annotations

import difflib
import json
import os
import re
from pathlib import Path

from . import search
from .index import Index
from .project import SEP, posix, rbx_key

READ_CAP = 150
CLASS_ABBR = {"Script": "S", "LocalScript": "LS", "ModuleScript": "M"}
STUDIO_NOTE = ("Rojo only syncs files. Anything that exists only in Studio (models, UI built in Studio, "
               "properties set in Studio) is not indexed.")


def finish(d: dict) -> dict:
    return d


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


class NotFound(Exception):
    def __init__(self, payload: dict):
        super().__init__(payload.get("error"))
        self.payload = payload


class RbxMap:
    def __init__(self, root, auto_refresh: bool = True):
        self.root = Path(root).resolve()
        self.idx = Index(self.root)
        self.auto_refresh = auto_refresh
        self.last_refresh = self._refresh()

    # ---- refresh -------------------------------------------------------------
    def _refresh(self, force: bool = False) -> dict:
        res = self.idx.refresh(force=force)
        if res["added"] or res["updated"] or res["removed"] or not (self.root / "MAP.md").exists():
            from .mapgen import write_map
            write_map(self)
        return res

    def _auto(self):
        if self.auto_refresh:
            self.last_refresh = self._refresh()

    def refresh(self, force: bool = False) -> dict:
        r = self._refresh(force=force)
        out = {k: (v if isinstance(v, int) else len(v)) for k, v in r.items()}
        for k in ("added", "updated", "removed"):
            if r[k]:
                out[k + "_files"] = r[k][:10]
        if self.idx.project.warnings:
            out["warnings"] = self.idx.project.warnings[:5]
        return finish(out)

    # ---- path resolution -----------------------------------------------------
    def resolve(self, path: str):
        p = (path or "").strip().strip("`'\"")
        rows = self.idx.all_scripts()
        if not p:
            raise NotFound({"error": "empty path"})
        if ">" in p or ("." in p and "/" not in p and "\\" not in p and not re.search(r"\.(lua|luau)$", p, re.I)):
            key = rbx_key(p.replace(".", ">") if ">" not in p else p)
            exact = [r for r in rows if r["rbx_key"] == key]
            if exact:
                return exact[0]
            suffix = [r for r in rows if r["rbx_key"].endswith(">" + key)]
            if len(suffix) == 1:
                return suffix[0]
            if suffix:
                raise NotFound({"error": f"'{p}' matches several scripts; use the full path",
                                "candidates": [r["rbx_path"] for r in suffix[:8]]})
            near = difflib.get_close_matches(key, [r["rbx_key"] for r in rows], n=5, cutoff=0.5)
            raise NotFound({"error": f"No script at Roblox path '{p}'",
                            "candidates": [r["rbx_path"] for r in rows if r["rbx_key"] in near], "note": STUDIO_NOTE})
        fp = posix(p)
        if os.path.isabs(p):
            try:
                fp = posix(os.path.relpath(p, self.root))
            except ValueError:
                pass
        fp = fp.lstrip("./") if fp.startswith("./") else fp
        low = fp.lower()
        exact = [r for r in rows if r["fs_path"].lower() == low]
        if exact:
            return exact[0]
        suffix = [r for r in rows if r["fs_path"].lower().endswith("/" + low)]
        if not suffix and "/" not in low:
            suffix = [r for r in rows if r["name"].lower() == low]
        if len(suffix) == 1:
            return suffix[0]
        if suffix:
            raise NotFound({"error": f"'{p}' matches several scripts", "candidates": [r["fs_path"] for r in suffix[:8]]})
        near = difflib.get_close_matches(low, [r["fs_path"].lower() for r in rows], n=5, cutoff=0.5)
        raise NotFound({"error": f"No indexed script '{p}'",
                        "candidates": [r["fs_path"] for r in rows if r["fs_path"].lower() in near], "note": STUDIO_NOTE})

    def _guard(self, fn, *a, **kw) -> dict:
        try:
            self._auto()
            return finish(fn(*a, **kw))
        except NotFound as e:
            return finish(e.payload)
        except FileNotFoundError as e:
            return finish({"error": str(e), "fix": "Run rbxmap from your Roblox project folder or pass --project."})

    # ---- tools ---------------------------------------------------------------
    def find_script(self, query: str, limit: int = 3) -> dict:
        return self._guard(self._find, query, limit)

    def _find(self, query: str, limit: int):
        limit = max(1, min(int(limit or 3), 5))
        groups, results = search.search(self.idx, query, limit)
        if not results:
            return {"query": query, "matches": [],
                    "message": "No indexed script matches. Nothing was guessed.",
                    "nearest": search.nearest(self.idx, query),
                    "not_indexed": self.idx.project.not_indexed[:8], "note": STUDIO_NOTE}
        out = []
        for n, (r, matched) in enumerate(results):
            lines = self.idx.read_lines(r["fs_path"])
            funcs = self.idx.functions(r["id"])
            s, e, fname, top = search.best_range(lines, funcs, groups, matched)
            show = sorted(top[: (3 if n == 0 else 1)]) or [s]
            item = {"rbx": r["rbx_path"], "file": r["fs_path"], "class": r["class"],
                    "desc": ("~" if r["desc_auto"] else "") + _clip(r["descr"], 100),
                    "lines": f"{s}-{e}", "why": search.why_text(matched),
                    "preview": [f"{i}: {_clip(lines[i - 1], 90)}" for i in show if i <= len(lines)]}
            if fname:
                item["in"] = fname
            out.append(item)
        res = {"query": query, "matches": out}
        while len(json.dumps(res)) > 1900 and any(len(m["preview"]) > 1 or len(m["desc"]) > 40 for m in out):
            for m in reversed(out):
                if len(m["preview"]) > 1:
                    m["preview"].pop()
                    break
                if len(m["desc"]) > 40:
                    m["desc"] = m["desc"][:39] + "…"
                    break
        res["next"] = "read_lines(file, start, end) for the range above"
        return res

    def read_lines(self, path: str, start: int = 1, end: int | None = None) -> dict:
        return self._guard(self._read_lines, path, start, end)

    def _read_lines(self, path, start, end):
        r = self.resolve(path)
        lines = self.idx.read_lines(r["fs_path"])
        total = len(lines)
        start = max(1, int(start or 1))
        end = total if end is None else int(end)
        if end < start:
            start, end = end, start
            start = max(1, start)
        end = min(end, total)
        res = {"rbx": r["rbx_path"], "file": r["fs_path"], "total_lines": total}
        if end - start + 1 > READ_CAP:
            res["truncated"] = f"capped at {READ_CAP} lines; next call: start={start + READ_CAP}"
            end = start + READ_CAP - 1
        res["start"], res["end"] = start, end
        res["text"] = "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))
        return res

    def outline(self, path: str) -> dict:
        return self._guard(self._outline, path)

    def _outline(self, path):
        r = self.resolve(path)
        funcs = [f for f in self.idx.functions(r["id"]) if f["name"] != "<anon>" or f["end_line"] - f["start"] >= 10]
        db = self.idx.db
        req = db.execute("SELECT expr, line FROM requires WHERE script_id=? ORDER BY line", (r["id"],)).fetchall()
        rem = db.execute("SELECT name, kind, line FROM remotes WHERE script_id=? ORDER BY line", (r["id"],)).fetchall()
        res = {"rbx": r["rbx_path"], "file": r["fs_path"], "class": r["class"], "lines": r["lines"],
               "desc": ("~" if r["desc_auto"] else "") + _clip(r["descr"], 120),
               "functions": [f"{f['name']} {f['start']}-{f['end_line']}" for f in funcs[:80]],
               "requires": [f"{q['line']}: {_clip(q['expr'], 60)}" for q in req[:30]],
               "remotes": [f"{m['line']}: {m['name']} {m['kind']}" for m in rem[:30]],
               "services": r["services"].split() if r["services"] else []}
        if len(funcs) > 80:
            res["more_functions"] = len(funcs) - 80
        return res

    def find_symbol(self, name: str) -> dict:
        return self._guard(self._find_symbol, name)

    def _find_symbol(self, name):
        name = (name or "").strip()
        db = self.idx.db
        low = name.lower()
        defs = []
        for f in db.execute("SELECT f.*, s.rbx_path, s.fs_path FROM functions f JOIN scripts s ON s.id=f.script_id"):
            last = re.split(r"[.:]", f["name"].replace("(fn)", ""))[-1].lower()
            if f["name"].lower() == low or last == low:
                defs.append(f"{f['fs_path']}:{f['start']}-{f['end_line']} {f['name']} ({f['rbx_path']})")
        remotes = [f"{m['fs_path']}:{m['line']} {m['kind']}" for m in db.execute(
            "SELECT r.*, s.fs_path FROM remotes r JOIN scripts s ON s.id=r.script_id WHERE lower(r.name)=? ORDER BY s.fs_path, r.line", (low,))]
        for path, cls, pfile in self.idx.project.remote_defs:
            if path.split(SEP)[-1].lower() == low:
                remotes.insert(0, f"{pfile}: {cls} {path} (defined in project file)")
        res = {"symbol": name}
        svc = [r["fs_path"] for r in db.execute("SELECT fs_path, services FROM scripts") if name in (r["services"] or "").split()]
        if svc:
            res["service_used_in"] = svc[:12]
        uses, more = [], 0
        if re.fullmatch(r"\w+", name):
            word = re.compile(r"\b" + re.escape(name) + r"\b")
            cands = db.execute("SELECT s.fs_path, s.id FROM fts JOIN scripts s ON s.id=fts.rowid WHERE fts MATCH ? LIMIT 40",
                               (f'"{name}"',)).fetchall()
            def_lines = {d.split(" ")[0].rsplit("-", 1)[0] for d in defs}
            for c in cands:
                for i, line in enumerate(self.idx.read_lines(c["fs_path"]), 1):
                    if word.search(line) and f"{c['fs_path']}:{i}" not in def_lines:
                        if len(uses) < 12:
                            uses.append(f"{c['fs_path']}:{i}: {_clip(line, 70)}")
                        else:
                            more += 1
        res.update({"definitions": defs[:10], "remotes": remotes[:12], "uses": uses})
        if more:
            res["more_uses"] = more
        if not (defs or remotes or uses or svc):
            res["message"] = "Not found in indexed files. Nothing was guessed."
            res["note"] = STUDIO_NOTE
        return res

    def dependencies(self, path: str) -> dict:
        return self._guard(self._deps, path)

    def _deps(self, path):
        r = self.resolve(path)
        db = self.idx.db
        by_key = {s["rbx_key"]: s for s in self.idx.all_scripts()}
        names = {}
        for s in by_key.values():
            names.setdefault(s["name"].lower(), []).append(s)
        reqs = []
        for q in db.execute("SELECT * FROM requires WHERE script_id=? ORDER BY line", (r["id"],)):
            t = by_key.get(q["target_key"]) if q["target_key"] else None
            if t is None and q["last"] and len(names.get(q["last"].lower(), [])) == 1:
                t = names[q["last"].lower()][0]
                how = " (matched by name)"
            else:
                how = ""
            reqs.append(f"{q['line']}: {t['rbx_path'] + how if t else 'unresolved: ' + _clip(q['expr'], 60)}")
        users = []
        same_name = len(names.get(r["name"].lower(), [])) == 1
        for q in db.execute("SELECT q.*, s.fs_path, s.rbx_path FROM requires q JOIN scripts s ON s.id=q.script_id"):
            if q["target_key"] == r["rbx_key"] or (q["target_key"] not in by_key and same_name
                                                    and (q["last"] or "").lower() == r["name"].lower()):
                users.append(f"{q['rbx_path']} ({q['fs_path']}:{q['line']})")
        return {"rbx": r["rbx_path"], "file": r["fs_path"], "requires": reqs[:30], "required_by": sorted(set(users))[:30]}

    def map(self, folder: str | None = None) -> dict:
        return self._guard(self._map, folder)

    def _map(self, folder):
        rows = self.idx.all_scripts()
        if folder:
            key = rbx_key(folder)
            fl = posix(folder).lower().rstrip("/")
            rows = [r for r in rows if r["rbx_key"] == key or r["rbx_key"].startswith(key + ">")
                    or r["rbx_key"].find(">" + key + ">") >= 0 or r["fs_path"].lower().startswith(fl + "/")]
            if not rows:
                raise NotFound({"error": f"No indexed scripts under '{folder}'", "note": STUDIO_NOTE,
                                "not_indexed": self.idx.project.not_indexed[:8]})
        out, cur = [], None
        rows = sorted(rows, key=lambda r: (r["rbx_path"].rsplit(SEP, 1)[0].lower() if SEP in r["rbx_path"] else "", r["name"].lower()))
        for r in rows:
            parent = SEP.join(r["rbx_path"].split(SEP)[:-1]) or "(root)"
            if parent != cur:
                out.append(parent)
                cur = parent
            line = f"  {r['name']} [{CLASS_ABBR.get(r['class'], r['class'])}] {r['lines']}L"
            if folder:
                line += " — " + ("~" if r["desc_auto"] else "") + _clip(r["descr"], 60)
            out.append(line)
        text = "\n".join(out)
        res = {"scripts": len(rows), "legend": "S=Script LS=LocalScript M=ModuleScript; full detail: outline(path)"}
        if len(text) > 9000:
            text = text[:9000].rsplit("\n", 1)[0]
            res["truncated"] = "pass folder='Service > Folder' to see the rest"
        res["map"] = text
        return res

