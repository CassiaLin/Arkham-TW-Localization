"""
Auto-detect Steam / Epic Games installations of the Batman: Arkham series on Windows.
Designed for GitHub distribution so any player can run the toolkit without manual path editing.
"""
import os
import re
import json
import string
from pathlib import Path
from typing import Dict, Optional

GAME_SIGNATURES = {
    "asylum": {
        "title": "Batman: Arkham Asylum GOTY Edition",
        "folder_names": ["Batman Arkham Asylum GOTY", "Batman Arkham Asylum"],
        "steam_appid": "35140",
        "check_rel": "BmGame/Localization/INT/GFxUI.int"
    },
    "city": {
        "title": "Batman: Arkham City GOTY",
        "folder_names": ["Batman Arkham City GOTY", "Batman Arkham City"],
        "steam_appid": "200260",
        "check_rel": "BmGame/Localization/INT"
    },
    "origins": {
        "title": "Batman: Arkham Origins",
        "folder_names": ["Batman Arkham Origins"],
        "steam_appid": "209000",
        "check_rel": "SinglePlayer/BMGame/Localization/INT"
    },
    "knight": {
        "title": "Batman: Arkham Knight",
        "folder_names": ["Batman Arkham Knight"],
        "steam_appid": "208650",
        "check_rel": "BMGame/Config"
    }
}


def get_steam_library_dirs() -> list[Path]:
    candidates = []
    # 1. Check Windows Registry for SteamPath
    try:
        import winreg
        for hive, subkey in [
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        ]:
            try:
                with winreg.OpenKey(hive, subkey) as k:
                    val, _ = winreg.QueryValueEx(k, "SteamPath")
                    if val and os.path.isdir(val):
                        candidates.append(Path(val))
            except OSError:
                pass
    except ImportError:
        pass

    # 2. Scan common drive roots for SteamLibrary / Steam / Epic Games
    for drv in string.ascii_uppercase:
        root = Path(f"{drv}:/")
        if not root.exists():
            continue
        for sub in [
            "SteamLibrary",
            "Steam",
            "Program Files (x86)/Steam",
            "Program Files/Steam",
            "Games/SteamLibrary",
            "Program Files/Epic Games",
            "Epic Games",
        ]:
            p = root / sub
            if p.is_dir():
                candidates.append(p)

    # 3. Parse libraryfolders.vdf from any discovered Steam dir
    expanded = []
    seen = set()
    for c in candidates:
        vdf = c / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            try:
                txt = vdf.read_text(encoding="utf-8", errors="ignore")
                for m in re.findall(r'"path"\s+"([^"]+)"', txt):
                    p = Path(m.replace("\\\\", "\\"))
                    if p.is_dir():
                        expanded.append(p)
            except OSError:
                pass
        expanded.append(c)

    result = []
    for p in expanded:
        norm = str(p.resolve()).lower()
        if norm not in seen:
            seen.add(norm)
            result.append(p.resolve())
    return result


def detect_all_games(config_path: Optional[Path] = None) -> Dict[str, dict]:
    lib_dirs = get_steam_library_dirs()
    detected = {}

    for key, sig in GAME_SIGNATURES.items():
        found_root = None
        for lib in lib_dirs:
            for base in [lib / "steamapps" / "common", lib]:
                for fname in sig["folder_names"]:
                    cand = base / fname
                    if cand.is_dir() and (cand / sig["check_rel"]).exists():
                        found_root = cand
                        break
                if found_root:
                    break
            if found_root:
                break

        if found_root:
            root_str = found_root.as_posix()
            info = {
                "title": sig["title"],
                "root_dir": root_str,
            }
            if key == "asylum":
                info["int_dir"] = f"{root_str}/BmGame/Localization/INT"
                info["cooked_dir"] = f"{root_str}/BmGame/CookedPC"
                info["startup_upk"] = f"{root_str}/BmGame/CookedPC/Startup_INT.upk"
            detected[key] = info

    if config_path:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(detected, indent=2, ensure_ascii=False), encoding="utf-8")

    return detected


if __name__ == "__main__":
    cfg = Path(__file__).resolve().parent.parent / "config" / "game_paths.json"
    res = detect_all_games(cfg)
    print(f"Auto-detected {len(res)} Batman: Arkham games -> saved to {cfg}")
    for k, v in res.items():
        print(f"  [{k}] {v['title']}: {v['root_dir']}")
