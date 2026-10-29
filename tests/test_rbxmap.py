import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

from conftest import FIXTURE, tree_hashes
from rbxmap.api import RbxMap
from rbxmap.check import run_checks
from rbxmap.mapgen import MAP_END, MAP_START, write_remotes
from rbxmap.project import load_project
from rbxmap.safety import safe_write


def lines_with(proj, rel, word):
    text = (proj / rel).read_text(encoding="utf-8").splitlines()
    return [i for i, l in enumerate(text, 1) if word in l]


# ---- Component A: project reader ------------------------------------------------
def test_reader_classes_paths_nested_init_meta(proj):
    p = load_project(proj)
    got = {s.rbx_path: (s.cls, s.fs_path) for s in p.scripts}
    assert got["ServerScriptService > LobbyBuilder > Builder"] == ("Script", "src/server/LobbyBuilder/Builder.server.lua")
    assert got["StarterPlayer > StarterPlayerScripts > Shop"][0] == "LocalScript"
    assert got["ReplicatedStorage > Shared > Config"][0] == "ModuleScript"
    assert got["ServerScriptService > Leaderboard"] == ("Script", "src/server/Leaderboard/init.server.lua")
    assert got["ServerScriptService > Leaderboard > Format"][0] == "ModuleScript"
    assert got["ServerStorage > Tools > Util"][0] == "ModuleScript"  # nested project file
    assert got["ReplicatedStorage > Shared > Legacy"][0] == "Script"  # .meta.json className hint
    assert ("ReplicatedStorage > Remotes > BuyItem", "RemoteEvent", "default.project.json") in p.remote_defs
    assert any(n.startswith("Lighting") for n in p.not_indexed)


# ---- Test 2: sloppy '>' paths ---------------------------------------------------
@pytest.mark.parametrize("path", [
    "ServerScript Service > Lobby Builder > Builder",
    "  serverscriptservice>lobbybuilder >BUILDER ",
    "game > ServerScriptService > LobbyBuilder > Builder",
    "LobbyBuilder > Builder",
    "ServerScriptService.LobbyBuilder.Builder",
])
def test_sloppy_roblox_paths(app, path):
    r = app.outline(path)
    assert r.get("file") == "src/server/LobbyBuilder/Builder.server.lua", r


# ---- Test 7: Windows paths normalized -------------------------------------------
def test_windows_paths(app, proj):
    for p in ["src\\server\\LobbyBuilder\\Builder.server.lua", str(proj / "src" / "server" / "LobbyBuilder" / "Builder.server.lua"),
              ".\\src\\server\\LobbyBuilder\\Builder.server.lua"]:
        r = app.read_lines(p, 1, 3)
        assert r["file"] == "src/server/LobbyBuilder/Builder.server.lua", (p, r)
    blob = json.dumps([app.find_script("lobby lighting"), app.map(), app.outline("Builder"), app.dependencies("Config")])
    assert "\\\\" not in blob and "src\\" not in blob


# ---- Test 1 + 8: search quality and size ---------------------------------------
def test_lobby_lighting_top_result_and_range(app, proj):
    r = app.find_script("lobby lighting")
    top = r["matches"][0]
    assert top["rbx"] == "ServerScriptService > LobbyBuilder > Builder"
    s, e = map(int, top["lines"].split("-"))
    for word in ("ClockTime", "Atmosphere", "ColorCorrectionEffect", "FogEnd"):
        for ln in lines_with(proj, top["file"], word):
            assert s <= ln <= e, (word, ln, top["lines"])
    assert top.get("in") == "setLighting"
    assert top["why"]
    assert "lighting" in json.dumps(r).lower()


def test_dictated_query(app):
    r = app.find_script("change the lighting in my lobby please")
    assert r["matches"][0]["rbx"].endswith("LobbyBuilder > Builder")


# ---- Test 6: no-match fabricates nothing ----------------------------------------
def test_no_match_fabricates_nothing(app):
    known = {r["rbx_path"] for r in app.idx.all_scripts()}
    r = app.find_script("dragon spaceship xyzzy")
    assert r["matches"] == []
    assert "nothing was guessed" in r["message"].lower()
    assert all(n in known for n in r["nearest"])
    assert r["not_indexed"] and "Studio" in r["note"]
    r2 = app.find_script("lobbbybuilder")  # typo -> only real candidates
    assert all(n in known for n in r2.get("nearest", []))
    bad = app.outline("ServerScriptService > Nope > Missing")
    assert "error" in bad and all(c in known for c in bad.get("candidates", []))


# ---- Test 3: read_lines cap ------------------------------------------------------
def test_read_lines_cap(app, proj):
    big = proj / "src" / "shared" / "Big.lua"  # created by the test inside the temp copy
    big.write_text("\n".join(f"local x{i} = {i}" for i in range(400)) + "\n", encoding="utf-8")
    r = app.read_lines("ReplicatedStorage > Shared > Big", 1, 400)
    assert r["end"] - r["start"] + 1 == 150
    assert r["text"].count("\n") == 149
    assert "capped at 150" in r["truncated"]
    r = app.read_lines("Big", 390, 1000)
    assert (r["start"], r["end"]) == (390, 400)
    r = app.read_lines("Builder", 41, 41)
    assert r["text"].startswith("41: ") and "ClockTime" in r["text"]


# ---- Test 5: incremental indexing -----------------------------------------------
def test_incremental_and_delete(app, proj):
    r = app.idx.refresh()
    assert r["added"] == r["updated"] == r["removed"] == [] and r["unchanged"] == r["total"]
    # change one file: only that file is rescanned
    f = proj / "src" / "shared" / "Config.lua"
    f.write_text(f.read_text(encoding="utf-8") + "\n-- touched\n", encoding="utf-8")
    r = app.idx.refresh()
    assert r["updated"] == ["src/shared/Config.lua"] and r["unchanged"] == r["total"] - 1
    # same content, new mtime: hashed but not re-indexed
    os.utime(f, (time.time() + 5, time.time() + 5))
    r = app.idx.refresh()
    assert r["updated"] == []
    # delete: removed from index and search
    (proj / "src" / "server" / "ShopHandler.server.lua").unlink()
    r = app.refresh()
    assert r["removed"] == 1
    assert all("ShopHandler" not in m["rbx"] for m in app.find_script("shop purchase")["matches"])
    assert "ShopHandler" not in (proj / "MAP.md").read_text(encoding="utf-8")


# ---- outline / symbol / deps / map ----------------------------------------------
def test_outline_symbol_deps_map(app):
    o = app.outline("Builder")
    assert "setLighting 40-56" in o["functions"] and "buildWalls 28-38" in o["functions"]
    assert "text" not in o  # no bodies
    s = app.find_symbol("setLighting")
    assert s["definitions"][0].startswith("src/server/LobbyBuilder/Builder.server.lua:40-56")
    d = app.dependencies("Config")
    assert any("LobbyBuilder > Builder" in u for u in d["required_by"])
    assert any("ShopHandler" in u for u in d["required_by"])
    d2 = app.dependencies("ServerScriptService > Leaderboard")
    assert any("Leaderboard > Format" in q for q in d2["requires"])  # script.Format inside an init script
    m = app.map("ServerScriptService")
    assert "Builder [S]" in m["map"] and "Shop [LS]" not in m["map"]


# ---- MAP.md ----------------------------------------------------------------------
def test_map_md_markers_and_auto_desc(app, proj):
    mp = proj / "MAP.md"
    mp.write_text("# My notes\nkeep me\n\n" + MAP_START + "\nold\n" + MAP_END + "\n\nfooter notes\n", encoding="utf-8")
    app.refresh(force=True)
    from rbxmap.mapgen import write_map
    write_map(app)
    text = mp.read_text(encoding="utf-8")
    assert text.startswith("# My notes\nkeep me") and text.rstrip().endswith("footer notes")
    assert "ServerScriptService > LobbyBuilder > Builder (Script) — `src/server/LobbyBuilder/Builder.server.lua` — Builds the lobby" in text
    assert "Config (ModuleScript) — `src/shared/Config.lua` — ~ " in text  # auto description marked ~
    assert "Creates leaderstats" in text  # --@desc


def test_descriptions_override(app, proj):
    (proj / ".rbxmap" / "descriptions.json").write_text(json.dumps({"ReplicatedStorage > Shared > Config": "Game tuning numbers"}), encoding="utf-8")
    app.refresh()
    assert "Game tuning numbers" in (proj / "MAP.md").read_text(encoding="utf-8")


# ---- Addendum 3: remotes -----------------------------------------------------------
def test_remotes_pairs(app, proj):
    res = write_remotes(app)
    pair = next(p for p in res["pairs"] if p["name"] == "BuyItem")
    assert any("src/client/Shop.client.lua:15" in f and "fire_server" in f for f in pair["fired_by"])
    assert any("src/server/ShopHandler.server.lua:24" in h and "on_server_event" in h for h in pair["handled_by"])
    assert pair["defined"]
    design = (proj / "DESIGN.md").read_text(encoding="utf-8")
    assert "<!-- rbxmap:remotes -->" in design and "| BuyItem |" in design
    for sec in ("Core loop", "Systems", "Data model", "Remote events", "Naming conventions", "Current priorities"):
        assert f"## {sec}" in design


def test_remote_wrappers_config(app, proj):
    (proj / ".rbxmap" / "config.json").write_text(json.dumps({"remote_wrappers": {"Net.Fire": "fire_server"}}), encoding="utf-8")
    (proj / "src" / "client" / "Net.client.lua").write_text('Net.Fire("Jump", 1)\n', encoding="utf-8")
    app.refresh()
    assert any("fire_server" in x for x in app.find_symbol("Jump")["remotes"])


# ---- Addendum 4: check skips missing tools -----------------------------------------
def test_check_skips_missing_tools(app, proj):
    r = run_checks(proj, app.idx.project, which=lambda _t: None)
    assert {v["status"] for v in r["checks"].values()} == {"skipped"}
    assert all("install" in v for v in r["checks"].values())

    calls = []

    def fake_run(cmd, **kw):
        calls.append((cmd, kw["cwd"]))
        return subprocess.CompletedProcess(cmd, 1, "", "ERROR Failed to find tool 'rojo'")
    r = run_checks(proj, app.idx.project, which=lambda t: f"/bin/{t}", runner=fake_run)
    assert r["checks"]["rojo"]["status"] == "failed" and "fix" in r["checks"]["rojo"]
    rojo_cmd = calls[0][0]
    assert rojo_cmd[-1] == ".rbxmap/tmp/check.rbxl"  # build output only inside .rbxmap
    assert "--check" in calls[2][0]  # stylua never rewrites


# ---- safety -------------------------------------------------------------------------
def test_safe_write_refuses_game_code(proj):
    for bad in ("src/shared/Config.lua", "src/x.luau", "default.project.json", "../outside.md", "src/notes.md"):
        with pytest.raises((PermissionError, ValueError)):
            safe_write(proj, bad, "x")


# ---- Test 4 + addendum 5: game files byte-identical after everything ----------------
def test_game_files_untouched(proj):
    before = tree_hashes(proj)
    a = RbxMap(proj)
    a.refresh(force=True)
    a.find_script("lobby lighting"); a.find_script("nothing here at all")
    a.read_lines("Builder", 1, 500); a.outline("Config"); a.find_symbol("BuyItem"); a.dependencies("Config"); a.map()
    write_remotes(a)
    run_checks(proj, a.idx.project, which=lambda _t: None)
    a.idx.db.close()
    after = tree_hashes(proj)
    new_files = set(after) - set(before)
    assert all(not f.endswith((".lua", ".luau")) for f in new_files)
    allowed = {"MAP.md", "DESIGN.md"}
    assert all(f in allowed for f in new_files), new_files
    for f, h in before.items():
        assert after[f] == h, f"modified: {f}"


def test_repo_fixture_untouched_by_test_run():
    assert not (FIXTURE / ".rbxmap").exists() and not (FIXTURE / "MAP.md").exists()


def test_cli_find_and_doctor(proj):
    exe = [sys.executable, "-m", "rbxmap.cli", "--project", str(proj)]
    out = subprocess.run(exe + ["find", "lobby", "lighting"], capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0 and "ServerScriptService > LobbyBuilder > Builder" in out.stdout
    out = subprocess.run(exe + ["find", "lobby lighting", "--json"], capture_output=True, text=True, encoding="utf-8")
    assert json.loads(out.stdout)["matches"][0]["file"] == "src/server/LobbyBuilder/Builder.server.lua"
    out = subprocess.run(exe + ["doctor"], capture_output=True, text=True, encoding="utf-8")
    assert "[ok]   Python" in out.stdout
