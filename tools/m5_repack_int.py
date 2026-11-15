"""
Module 5A (M5-Repacker-INT): Reconstruct localized .int files in UTF-16 LE with BOM
from translated JSONL outputs + id_map.json + raw_backup/.
Supports all 4 Arkham titles: asylum, city, origins, knight.
"""
import os
import re
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Any

ROOT_DIR = Path(__file__).resolve().parent.parent


def restore_tags(zh_text: str, tags: List[str]) -> str:
    """Replace {0}, {1}, ... placeholders back with their original engine tags."""
    if not tags:
        return zh_text

    def repl(m):
        idx = int(m.group(1))
        if 0 <= idx < len(tags):
            return tags[idx]
        return m.group(0)

    return re.sub(r"\{(\d+)\}", repl, zh_text)


def load_translations(work_dir: Path) -> Dict[str, str]:
    """Load all translated JSONL outputs into {id: zh_text}."""
    trans_dir = work_dir / "translated"
    translations: Dict[str, str] = {}
    if not trans_dir.exists():
        return translations

    for jsonl_path in sorted(trans_dir.rglob("*.output.jsonl")):
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                    eid = obj.get("id")
                    zh = obj.get("zh")
                    if eid and isinstance(zh, str):
                        translations[eid] = zh
                except json.JSONDecodeError:
                    continue
    return translations


def repack_game_int(game: str) -> Dict[str, Any]:
    work_dir = ROOT_DIR / "workspace" / game
    id_map_path = work_dir / "metadata" / "id_map.json"
    backup_root = work_dir / "raw_backup"
    dist_dir = ROOT_DIR / "dist" / f"Arkham_{game.capitalize()}_TW_Mod"

    if not id_map_path.exists():
        raise FileNotFoundError(f"Missing id_map.json for {game}: {id_map_path}")

    with open(id_map_path, "r", encoding="utf-8") as f:
        id_map = json.load(f)

    translations = load_translations(work_dir)
    if not translations:
        print(f"[{game}] Warning: No translations loaded.")

    # Map backup file relative path -> {line_idx: (expected_key, new_value)}
    file_edits: Dict[str, Dict[int, Tuple[str, str]]] = {}
    replaced_entries = 0
    missing_entries = 0

    for eid, meta in id_map.items():
        int_sources = [s for s in meta.get("sources", []) if s.get("type") == "int"]
        if not int_sources:
            continue

        if eid not in translations:
            missing_entries += 1
            continue

        zh_raw = translations[eid]
        zh_restored = restore_tags(zh_raw, meta.get("tags", []))
        # Prevent literal newlines inside single-line .int values
        zh_restored = zh_restored.replace("\r\n", "\\n").replace("\n", "\\n")
        replaced_entries += 1

        for src in int_sources:
            if game in ("asylum", "city"):
                rel_bkp = f"INT/{src['file']}"
            else:
                rel_bkp = src["rel_path"].replace("\\", "/")
            line_idx = src["line_idx"]
            key = src["key"]
            file_edits.setdefault(rel_bkp, {})[line_idx] = (key, zh_restored)

    files_written = 0
    lines_patched = 0

    # Walk all backed-up .int files so non-translated .int files (e.g. Version.int) are preserved cleanly
    for bkp_path in sorted(backup_root.rglob("*.int")):
        rel_bkp = bkp_path.relative_to(backup_root).as_posix()
        raw_bytes = bkp_path.read_bytes()
        text = raw_bytes.decode("utf-16") if raw_bytes.startswith(b"\xff\xfe") else raw_bytes.decode("latin1")
        lines = text.splitlines()

        edits = file_edits.get(rel_bkp, {})
        if edits:
            for lidx, (expected_key, new_val) in edits.items():
                if 0 <= lidx < len(lines) and "=" in lines[lidx]:
                    orig_k, _ = lines[lidx].split("=", 1)
                    if orig_k.strip().lower() == expected_key.lower():
                        lines[lidx] = f"{orig_k}={new_val}"
                        lines_patched += 1

        # Determine destination path inside mod package
        if game in ("asylum", "city"):
            # INT/<file> -> BmGame/Localization/INT/<file>
            fname = bkp_path.name
            out_path = dist_dir / "BmGame" / "Localization" / "INT" / fname
        else:
            out_path = dist_dir / rel_bkp

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_text = "\r\n".join(lines) + "\r\n"
        # Write UTF-16 LE with BOM (\xff\xfe)
        out_path.write_bytes(b"\xff\xfe" + out_text.encode("utf-16-le"))
        files_written += 1

    stats = {
        "game": game,
        "files_written": files_written,
        "lines_patched": lines_patched,
        "replaced_entries": replaced_entries,
        "missing_entries": missing_entries,
        "dist_dir": str(dist_dir),
    }
    print(f"[{game}] Repack INT done: {stats}")
    return stats


def main():
    parser = argparse.ArgumentParser(description="Repack translated .int files for Arkham series")
    parser.add_argument("game", choices=["asylum", "city", "origins", "knight", "all"])
    args = parser.parse_args()

    games = ["asylum", "city", "origins", "knight"] if args.game == "all" else [args.game]
    for g in games:
        repack_game_int(g)


if __name__ == "__main__":
    main()
