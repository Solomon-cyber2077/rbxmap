"""Optional Roblox tool checks: rojo build (into .rbxmap/tmp only), selene, stylua --check. Never installs anything."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .project import posix

INSTALL = {
    "rojo": "rokit add rojo-rbx/rojo   (or: aftman add rojo-rbx/rojo)",
    "selene": "rokit add Kampfkarren/selene",
    "stylua": "rokit add JohnnyMorganz/StyLua",
}


def tool_status(which=shutil.which) -> dict:
    return {t: which(t) for t in INSTALL}


def _tail(text: str, n: int = 15) -> str:
    lines = [l for l in (text or "").splitlines() if l.strip()]
    return "\n".join(lines[-n:])


def run_checks(root: Path, project, which=shutil.which, runner=subprocess.run, timeout: int = 180) -> dict:
    root = Path(root).resolve()
    tmp = root / ".rbxmap" / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    dirs = sorted(set(project.source_dirs)) or ["."]
    plan = {
        "rojo": ["build", posix(project.project_file.relative_to(root)), "-o", ".rbxmap/tmp/check.rbxl"],
        "selene": dirs,
        "stylua": ["--check", *dirs],
    }
    results = {}
    for tool, args in plan.items():
        exe = which(tool)
        if not exe:
            results[tool] = {"status": "skipped", "reason": "not installed", "install": INSTALL[tool]}
            continue
        try:
            p = runner([exe, *args], cwd=str(root), capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="replace")
        except (OSError, subprocess.TimeoutExpired) as e:
            results[tool] = {"status": "error", "output": str(e)[:400]}
            continue
        out = _tail((p.stdout or "") + "\n" + (p.stderr or ""))
        r = {"status": "ok" if p.returncode == 0 else "failed", "exit_code": p.returncode, "output": out}
        if "Failed to find tool" in out or "not found in any project manifest" in out.lower():
            r["fix"] = INSTALL[tool]
        if tool == "selene" and not (root / "selene.toml").exists():
            r["note"] = "no selene.toml: create one with  std = \"roblox\"  so Roblox globals are known"
        results[tool] = r
    return {"checks": results,
            "reminder": "Checks cannot playtest. Test the change in Studio by hand (see the brief's manual steps)."}
