"""The ONLY place rbxmap writes files. Game code (.lua/.luau) can never be written."""
from __future__ import annotations

from pathlib import Path

ALLOWED_TOP = {"map.md", "design.md", "design.md.new"}


def check_target(root: Path, rel: str) -> Path:
    root = Path(root).resolve()
    p = (root / rel).resolve()
    parts = [x.lower() for x in p.relative_to(root).parts]  # ValueError if outside the project
    if p.suffix.lower() in (".lua", ".luau"):
        raise PermissionError(f"rbxmap never writes game code: {rel}")
    ok = (parts and parts[0] == ".rbxmap") or (len(parts) == 1 and parts[0] in ALLOWED_TOP)
    if not ok:
        raise PermissionError(f"rbxmap may not write here: {rel}")
    return p


def safe_write(root: Path, rel: str, text: str) -> Path:
    p = check_target(root, rel)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return p
