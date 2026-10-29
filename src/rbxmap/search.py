"""FTS5/BM25 search with field weights, alias expansion, 'matched because' text and best line range."""
from __future__ import annotations

import difflib
import re

# bm25 weights per column: rbx path, name, description, tags, functions, body
WEIGHTS = (10.0, 10.0, 5.0, 5.0, 3.0, 1.0)
FIELDS = ("rbx", "name", "descr", "tags", "funcs", "body")
STOP = {"the", "a", "an", "in", "my", "of", "to", "for", "and", "on", "with", "please", "change", "make", "fix",
        "update", "add", "edit", "script", "scripts", "code", "game", "where", "is", "it", "that", "this", "i",
        "me", "want", "can", "you", "how", "do", "does", "at", "be", "so", "some", "set", "new", "file", "find"}


def stemlike(term: str) -> str:
    t = term.lower()
    return t if len(t) <= 5 else t[: max(5, len(t) - 3)]


def query_groups(query: str, aliases: dict) -> list:
    """One group per query word: the word plus its alias expansions."""
    groups = []
    for w in re.findall(r"[A-Za-z0-9_]+", query):
        lw = w.lower()
        if lw in STOP or len(lw) < 2:
            continue
        terms = {w}
        for key in (lw, lw[:-1] if lw.endswith("s") else None):
            if key and key in aliases:
                terms.update(aliases[key])
        groups.append((w, sorted(terms, key=str.lower)))
    return groups


def _fts_query(groups) -> str:
    out = []
    for _, terms in groups:
        for t in terms:  # whole terms only: CamelCase parts like 'End' (from FogEnd) would match everything
            if re.fullmatch(r"[A-Za-z0-9_]+", t) and len(t) >= 2:
                out.append(f'"{t}"*' if len(t) >= 4 else f'"{t}"')
    return " OR ".join(sorted(set(out)))


def _hit(term: str, text_low: str) -> bool:
    return stemlike(term) in text_low


def search(idx, query: str, limit: int = 3):
    groups = query_groups(query, idx.aliases())
    q = _fts_query(groups)
    if not q:
        return groups, []
    rows = idx.db.execute(
        f"SELECT s.*, bm25(fts, {', '.join(map(str, WEIGHTS))}) AS score, fts.rbx AS f_rbx, fts.name AS f_name,"
        " fts.descr AS f_descr, fts.tags AS f_tags, fts.funcs AS f_funcs, fts.body AS f_body"
        " FROM fts JOIN scripts s ON s.id = fts.rowid WHERE fts MATCH ? ORDER BY score LIMIT 60", (q,)).fetchall()
    scored = []
    for r in rows:
        low = {f: (r["f_" + f] or "").lower() for f in FIELDS}
        matched = []  # (group word, [fields])
        wcov = 0.0
        for word, terms in groups:
            fields = [f for f in FIELDS if any(_hit(t, low[f]) for t in terms)]
            if fields:
                matched.append((word, terms, fields))
                # the word you typed, found in the path/name, beats an alias match; body-only counts least
                literal = _hit(word, low["rbx"]) or _hit(word, low["name"])
                wcov += 1.0 if literal else (0.8 if fields[0] != "body" else 0.5)
        if wcov:
            scored.append((-wcov, r["score"], r["rbx_path"], r, matched))
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return groups, [(r, matched) for _, _, _, r, matched in scored[:limit]]


def why_text(matched) -> str:
    bits = []
    for word, _terms, fields in matched:
        short = [f.replace("descr", "desc").replace("rbx", "path") for f in fields[:3]]
        bits.append(f"{word}: {'+'.join(short)}")
    return "; ".join(bits)[:140]


def best_range(lines: list, funcs: list, groups, matched, max_span: int = 60, ctx: int = 3):
    """Pick the function/section with the most hits on query words the path/name did not already explain."""
    explained = {w for w, _t, f in matched if f[0] in ("rbx", "name")}
    terms = [t for w, ts, _f in matched if w not in explained for t in ts] or [t for _w, ts, _f in matched for t in ts]
    stems = sorted({stemlike(t) for t in terms if len(t) >= 3})
    hits = [0] * (len(lines) + 2)
    for i, line in enumerate(lines, 1):
        low = line.lower()
        hits[i] = sum(1 for s in stems if s in low)
    total = len(lines)
    if total == 0:
        return 1, 1, None, []
    fn_best = None
    for f in funcs:
        s, e = f["start"], min(f["end_line"], total)
        h = sum(hits[s: e + 1])
        if h and (fn_best is None or h > fn_best[0] or (h == fn_best[0] and e - s < fn_best[2] - fn_best[1])):
            fn_best = (h, s, e, f["name"])
    win = 30
    w_best = (0, 1, min(total, win))
    for s in range(1, max(2, total - win + 2), 5):
        h = sum(hits[s: min(total, s + win - 1) + 1])
        if h > w_best[0]:
            w_best = (h, s, min(total, s + win - 1))
    if fn_best and fn_best[0] * 2 >= w_best[0]:
        _, s, e, fname = fn_best
    elif w_best[0]:
        _, s, e = w_best
        fname = None
    else:
        first = next((f for f in funcs if f["name"] != "<anon>"), None)
        s, e, fname = (first["start"], first["end_line"], first["name"]) if first else (1, min(total, 20), None)
    if e - s + 1 > max_span:  # narrow a long function to its densest window
        b = (sum(hits[s: s + max_span]), s)
        for ws in range(s, e - max_span + 2, 3):
            h = sum(hits[ws: ws + max_span])
            if h > b[0]:
                b = (h, ws)
        s, e = b[1], b[1] + max_span - 1
    s, e = max(1, s - ctx), min(total, e + ctx)
    top = sorted((i for i in range(s, e + 1) if hits[i]), key=lambda i: (-hits[i], i))
    return s, e, fname, top


def nearest(idx, query: str, limit: int = 5) -> list:
    """Existing scripts whose names/path parts look like the query words. Never invents anything."""
    rows = idx.all_scripts()
    vocab = {}
    for r in rows:
        for part in r["rbx_path"].split(" > ") + [r["name"]]:
            vocab.setdefault(part.lower(), []).append(r["rbx_path"])
        for f in idx.functions(r["id"]):
            vocab.setdefault(f["name"].split(":")[-1].split(".")[-1].lower(), []).append(r["rbx_path"])
    out = []
    for w in re.findall(r"[A-Za-z0-9_]+", query):
        if w.lower() in STOP:
            continue
        for m in difflib.get_close_matches(w.lower(), list(vocab), n=3, cutoff=0.6):
            for p in vocab[m]:
                if p not in out:
                    out.append(p)
    return out[:limit]
