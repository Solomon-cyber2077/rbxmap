"""Regex/token based Luau scanner. Not a full parser: fast, deterministic, good enough to locate code."""
from __future__ import annotations

import bisect
import re

LONG_OPEN = re.compile(r"\[(=*)\[")
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\.\.\.|\.\.|==|~=|<=|>=|->|::|[^\sA-Za-z0-9_]")
WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")
# A token before `if` that means it is an if-EXPRESSION (no matching `end`).
EXPR_BEFORE_IF = {"=", "(", ",", "{", "[", "return", "and", "or", "not", "..", "+", "-", "*", "/",
                  "%", "^", "==", "~=", "<", ">", "<=", ">=", "#", "::", "in"}

SERVICES = {
    "Workspace", "Players", "Lighting", "ReplicatedFirst", "ReplicatedStorage", "ServerScriptService",
    "ServerStorage", "StarterGui", "StarterPack", "StarterPlayer", "SoundService", "Teams", "Chat",
    "TweenService", "RunService", "UserInputService", "ContextActionService", "DataStoreService",
    "MarketplaceService", "HttpService", "CollectionService", "PhysicsService", "PathfindingService",
    "TeleportService", "BadgeService", "TextChatService", "MemoryStoreService", "Debris", "GuiService",
    "ProximityPromptService", "SocialService", "PolicyService", "LocalizationService", "MessagingService",
}

TAG_PATTERNS = {
    "Lighting": r"\bLighting\b", "Atmosphere": r"\bAtmosphere\b", "Sky": r"\bSky(box)?\b",
    "ColorCorrection": r"ColorCorrection", "Bloom": r"\bBloom", "Blur": r"\bBlurEffect\b",
    "SunRays": r"SunRays", "DepthOfField": r"DepthOfField", "Clouds": r"\bClouds\b",
    "TweenService": r"TweenService|TweenInfo", "DataStore": r"DataStore",
    "MarketplaceService": r"MarketplaceService|ProcessReceipt|PromptProductPurchase|PromptGamePassPurchase",
    "GamePass": r"GamePass", "Remotes": r"RemoteEvent|RemoteFunction|FireServer|FireClient|OnServerEvent|OnClientEvent",
    "UI": r"ScreenGui|\bFrame\b|TextLabel|TextButton|ImageLabel|ImageButton|BillboardGui|SurfaceGui|UIListLayout",
    "Sound": r"\bSound\b|SoundService|SoundId", "Animation": r"AnimationTrack|\bAnimator\b|LoadAnimation",
    "Camera": r"CurrentCamera|\bCamera\b", "Input": r"UserInputService|ContextActionService",
    "Physics": r"PhysicsService|CollisionGroup|AssemblyLinearVelocity|LinearVelocity", "Raycast": r"Raycast",
    "Pathfinding": r"PathfindingService", "CollectionService": r"CollectionService",
    "Players": r"PlayerAdded|PlayerRemoving", "Character": r"CharacterAdded|\bHumanoid\b",
    "Leaderstats": r"leaderstats", "Badges": r"BadgeService", "Teleport": r"TeleportService",
    "Chat": r"TextChatService", "Particles": r"ParticleEmitter|\bBeam\b|\bTrail\b",
    "ProximityPrompt": r"ProximityPrompt", "RunService": r"RunService|Heartbeat|RenderStepped",
    "Http": r"HttpService", "MemoryStore": r"MemoryStore",
}
_TAG_RES = {k: re.compile(v) for k, v in TAG_PATTERNS.items()}

REMOTE_CALLS = {"FireServer": "fire_server", "FireClient": "fire_client", "FireAllClients": "fire_client",
                "InvokeServer": "invoke_server", "InvokeClient": "invoke_client"}
REMOTE_EVENTS = {"OnServerEvent": "on_server_event", "OnClientEvent": "on_client_event",
                 "OnServerInvoke": "on_server_invoke", "OnClientInvoke": "on_client_invoke"}
_REMOTE_CALL_RE = re.compile(r"[:.]\s*(FireServer|FireClient|FireAllClients|InvokeServer|InvokeClient)\s*\(")
_REMOTE_EVENT_RE = re.compile(r"\.\s*(OnServerEvent|OnClientEvent|OnServerInvoke|OnClientInvoke)\b")
_NEW_REMOTE_RE = re.compile(r"Instance\.new\(\s*[\"'](RemoteEvent|RemoteFunction|UnreliableRemoteEvent)[\"']")
_NAME_SET_RE = re.compile(r"\.Name\s*=\s*[\"']([^\"']+)[\"']")
_SERVICE_RE = re.compile(r"GetService\s*\(\s*[\"'](\w+)[\"']\s*\)")
_ALIAS_RE = re.compile(r"^\s*local\s+([A-Za-z_]\w*)\s*(?::\s*[^=]+?)?=\s*(.+?)\s*;?\s*$")
_CHAIN_OK = re.compile(r"[\w.:\[\]()\"' ]+")
_CHAIN_TOK = re.compile(r"[A-Za-z_]\w*|\"[^\"]*\"|'[^']*'|[.:\[\]()]")
LUA_KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local", "nil",
                "not", "or", "repeat", "return", "then", "true", "until", "while", "continue"}
_DESC_TAG = re.compile(r"^\s*--+\s*@desc\s*:?\s*(.+)$", re.I)


def expand_words(text: str) -> str:
    """Append CamelCase parts so 'LobbyBuilder' is also found by 'lobby'."""
    extra = []
    for w in set(WORD_RE.findall(text)):
        parts = CAMEL_RE.findall(w)
        if len(parts) > 1:
            extra.extend(parts)
    return text + ("\n" + " ".join(sorted(extra)) if extra else "")


def strip_code(src: str):
    """Return (no_comments, no_comments_no_strings); same length as src, newlines kept."""
    nc = list(src)
    ns = list(src)
    n = len(src)

    def blank(lst, a, b):
        lst[a:b] = ["\n" if ch == "\n" else " " for ch in src[a:b]]

    i = 0
    while i < n:
        c = src[i]
        if c == "-" and src.startswith("--", i):
            m = LONG_OPEN.match(src, i + 2)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                j = n if j < 0 else j + len(close)
            else:
                j = src.find("\n", i)
                j = n if j < 0 else j
            blank(nc, i, j)
            blank(ns, i, j)
            i = j
            continue
        if c in "\"'`":
            j = i + 1
            while j < n and src[j] != c:
                if src[j] == "\\":
                    j += 1
                elif src[j] == "\n" and c != "`":
                    break
                j += 1
            j = min(j + 1, n)
            blank(ns, i + 1, max(i + 1, j - 1))
            i = j
            continue
        if c == "[":
            m = LONG_OPEN.match(src, i)
            if m:
                close = "]" + m.group(1) + "]"
                j = src.find(close, m.end())
                j = n if j < 0 else j + len(close)
                blank(ns, m.end(), max(m.end(), j - len(close)))
                i = j
                continue
        i += 1
    return "".join(nc), "".join(ns)


def header_desc(lines: list) -> str | None:
    """Description from a '--@desc' line or the leading comment block."""
    for line in lines[:400]:
        m = _DESC_TAG.match(line)
        if m:
            return _tidy(m.group(1))
    out = []
    in_block = False
    for line in lines[:60]:
        s = line.strip()
        if in_block:
            if "]]" in s:
                out.append(s.split("]]")[0])
                break
            out.append(s)
            continue
        if not s or s.startswith("--!"):
            if out:
                break
            continue
        m = re.match(r"--\[(=*)\[(.*)", s)
        if m:
            rest = m.group(2)
            if "]" + m.group(1) + "]" in rest:
                out.append(rest.split("]")[0])
                break
            out.append(rest)
            in_block = True
            continue
        if s.startswith("--"):
            out.append(s.lstrip("-"))
            continue
        break
    text = " ".join(t.strip(" =*#-") for t in out if t.strip(" =*#-"))
    return _tidy(text) if text else None


def _tidy(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = cut.rfind(". ")
    return cut[: dot + 1] if dot > 40 else cut.rstrip() + "…"


def chain_of(expr: str) -> list:
    """'script.Parent:WaitForChild("Config")' -> ['script', 'Parent', 'Config']."""
    toks = _CHAIN_TOK.findall(expr)
    out = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t[0] in "\"'":
            out.append(t[1:-1])
        elif t in (".", ":", "[", "]", ")"):
            pass
        elif t == "(":
            # method call: keep its first string argument (WaitForChild("x"), GetService("x"), getRemote("x"))
            if out and out[-1] in ("WaitForChild", "FindFirstChild", "GetService", "FindFirstAncestor"):
                out.pop()
            j = i + 1
            depth = 1
            arg = None
            while j < len(toks) and depth:
                if toks[j] == "(":
                    depth += 1
                elif toks[j] == ")":
                    depth -= 1
                elif depth == 1 and arg is None and toks[j][0] in "\"'":
                    arg = toks[j][1:-1]
                j += 1
            if arg is not None:
                out.append(arg)
            i = j
            continue
        else:
            out.append(t)
        i += 1
    return out


def resolve_chain(chain: list, aliases: dict, own: list, depth: int = 0):
    """Turn a chain into an absolute Roblox path list, or None if unknown."""
    if not chain:
        return None
    base, rest = chain[0], chain[1:]
    if base in aliases and depth < 6:
        sub = resolve_chain(aliases[base], aliases, own, depth + 1)
        return None if sub is None else _walk(sub, rest)
    if base == "script":
        return _walk(list(own), rest)
    if base == "game":
        return _walk([], rest)
    if base == "workspace":
        return _walk(["Workspace"], rest)
    if base in SERVICES:
        return _walk([base], rest)
    return None


def _walk(cur: list, rest: list) -> list:
    for seg in rest:
        if seg == "Parent":
            if cur:
                cur.pop()
        else:
            cur.append(seg)
    return cur


def _balanced(text: str, start: int) -> str:
    """Text inside parentheses beginning at text[start] == '('."""
    depth = 0
    for j in range(start, min(len(text), start + 400)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return text[start + 1: j]
    return text[start + 1: start + 200]


def _receiver(line: str, end: int) -> str:
    """Expression just before position `end` (e.g. 'Remotes.BuyItem' before ':FireServer')."""
    j = end
    depth = 0
    while j > 0:
        ch = line[j - 1]
        if ch == ")":
            depth += 1
        elif ch == "(":
            if depth == 0:
                break
            depth -= 1
        elif not (ch.isalnum() or ch in "_.:[]\"'" or (depth and ch in " ,")):
            break
        j -= 1
    return line[j:end]


def scan(src: str, own_rbx: list, wrappers: dict | None = None) -> dict:
    """Extract description, functions, requires, services, remotes and tags from Luau source."""
    lines = src.splitlines()
    nc, ns = strip_code(src)
    nc_lines = nc.splitlines()
    nl_pos = [i for i, ch in enumerate(ns) if ch == "\n"]

    def line_of(pos: int) -> int:
        return bisect.bisect_right(nl_pos, pos - 1) + 1

    # aliases: local X = <chain>
    aliases = {}
    for ln in nc_lines:
        m = _ALIAS_RE.match(ln)
        if m and "require" not in m.group(2) and _CHAIN_OK.fullmatch(m.group(2)):
            ch = chain_of(m.group(2))
            if ch:
                aliases[m.group(1)] = ch

    functions = _functions(ns, line_of, len(lines))

    requires = []
    for m in re.finditer(r"\brequire\s*\(", nc):
        expr = " ".join(_balanced(nc, m.end() - 1).split())
        ch = chain_of(expr)
        target = resolve_chain(ch, aliases, own_rbx) if ch else None
        requires.append({"expr": expr[:120], "line": line_of(m.start()),
                         "target": target, "last": ch[-1] if ch else None})

    services = sorted(set(_SERVICE_RE.findall(nc)))

    remotes = []
    for idx, ln in enumerate(nc_lines, 1):
        for rx, kinds in ((_REMOTE_CALL_RE, REMOTE_CALLS), (_REMOTE_EVENT_RE, REMOTE_EVENTS)):
            for m in rx.finditer(ln):
                remotes.append({"name": _remote_name(_receiver(ln, m.start()), aliases),
                                "kind": kinds[m.group(1)], "line": idx})
        m = _NEW_REMOTE_RE.search(ln)
        if m:
            name = None
            for look in nc_lines[idx - 1: idx + 4]:
                nm = _NAME_SET_RE.search(look)
                if nm:
                    name = nm.group(1)
                    break
            remotes.append({"name": name or "?", "kind": "define", "line": idx, "cls": m.group(1)})
        for call, kind in (wrappers or {}).items():
            for m in re.finditer(re.escape(call) + r"\s*\(\s*[\"']([^\"']+)[\"']", ln):
                remotes.append({"name": m.group(1), "kind": kind, "line": idx})

    tags = sorted(t for t, rx in _TAG_RES.items() if rx.search(nc))
    tags = sorted(set(tags) | {s for s in services if s not in ("Players", "ReplicatedStorage", "RunService")})
    return {"lines": len(lines), "desc": header_desc(lines), "functions": functions, "requires": requires,
            "services": services, "remotes": remotes, "tags": tags}


def _remote_name(expr: str, aliases: dict) -> str:
    ch = chain_of(expr)
    for _ in range(6):
        if ch and ch[0] in aliases:
            ch = aliases[ch[0]] + ch[1:]
        else:
            break
    return ch[-1] if ch else (expr or "?")


def _functions(ns: str, line_of, total_lines: int) -> list:
    toks = [(m.group(0), m.start()) for m in TOKEN_RE.finditer(ns)]
    funcs = []
    stack = []
    for i, (t, pos) in enumerate(toks):
        if t == "function":
            name, kind = _func_name(toks, i)
            funcs.append([name, line_of(pos), None, kind])
            stack.append(len(funcs) - 1)
        elif t in ("do", "repeat"):
            stack.append(("repeat" if t == "repeat" else None))
        elif t == "if":
            prev = toks[i - 1][0] if i else None
            if prev not in EXPR_BEFORE_IF:
                stack.append(None)
        elif t in ("end", "until"):
            if stack:
                top = stack.pop()
                if isinstance(top, int):
                    funcs[top][2] = line_of(pos)
    for f in funcs:
        if f[2] is None:
            f[2] = total_lines
    return [{"name": f[0], "start": f[1], "end": f[2], "kind": f[3]} for f in funcs]


def _func_name(toks, i):
    nxt = toks[i + 1][0] if i + 1 < len(toks) else ""
    prev = toks[i - 1][0] if i else ""
    if WORD_RE.fullmatch(nxt):
        j = i + 1
        name = ""
        while j < len(toks) and toks[j][0] != "(":
            name += toks[j][0]
            j += 1
            if len(name) > 80:
                break
        return name, ("local" if prev == "local" else "function")
    if prev == "=":
        j = i - 2
        parts = []
        while j >= 0 and (WORD_RE.fullmatch(toks[j][0]) or toks[j][0] in (".", ":")):
            if toks[j][0] in LUA_KEYWORDS:
                break
            parts.append(toks[j][0])
            j -= 1
        return ("".join(reversed(parts)) or "<anon>"), "assign"
    if prev in ("(", ","):
        depth = 0
        j = i - 1
        while j >= 0:
            if toks[j][0] == ")":
                depth += 1
            elif toks[j][0] == "(":
                if depth == 0:
                    break
                depth -= 1
            j -= 1
        k = j - 1
        parts = []
        while k >= 0 and (WORD_RE.fullmatch(toks[k][0]) or toks[k][0] in (".", ":")):
            if toks[k][0] in LUA_KEYWORDS:
                break
            parts.append(toks[k][0])
            k -= 1
        if parts:
            return "".join(reversed(parts)) + "(fn)", "callback"
    return "<anon>", "anon"
