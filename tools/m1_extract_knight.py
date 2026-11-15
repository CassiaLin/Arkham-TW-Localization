"""
Module 1 for Game 4 (Batman: Arkham Knight):
Extracts all translatable strings from:
1. BmGame/Localization/INT/*.int + DLC/**/INT/*.int (UI, descriptions, missions, lore)
2. BmGame/CookedPCConsole/*.upk (UE3 ver 863/32995, zlib compressed chunks, DialogueText / Subtitles)
Categorizes into 5 categories and outputs token-efficient JSONL batches + id_map.json.
"""
import os
import re
import sys
import json
import shutil
import struct
import zlib
from pathlib import Path
from typing import Dict, List, Tuple, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from game_detector import detect_all_games
from source_resolver import resolve_game_source
from m1_extract import extract_tags, infer_speaker_from_token, should_translate_int_value, SKIP_INT_FILES, PLACEHOLDER_VALUES

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "game_paths.json"
WORK_DIR = ROOT_DIR / "workspace" / "knight"
BATCH_SIZE = 100


def decomp_upk_knight(b: bytes) -> bytes:
    """Decompress Unreal Engine 3 ver 863 UPK with zlib compressed chunks."""
    if len(b) < 120:
        return b
    magic, ver, lic, hdr_size, folder_len = struct.unpack("<IHHII", b[:16])
    off = 16 + folder_len
    gen_cnt = struct.unpack("<I", b[off+64:off+68])[0]
    c_off = off + 68 + gen_cnt * 12 + 8
    comp_flags, chunk_cnt = struct.unpack("<II", b[c_off:c_off+8])
    if comp_flags == 0 or chunk_cnt == 0:
        return b

    uo_first = struct.unpack("<I", b[c_off+8:c_off+12])[0]
    out = bytearray(b[:uo_first] if uo_first > 0 else b[:hdr_size])

    p = c_off + 8
    for _ in range(chunk_cnt):
        uo, us, co, cs = struct.unpack("<IIII", b[p:p+16])
        p += 16
        tag, blk_sz, c_tot, u_tot = struct.unpack("<IIII", b[co:co+16])
        n_sub = (u_tot + blk_sz - 1) // blk_sz
        sp = co + 16
        sub_headers = []
        for _ in range(n_sub):
            scs, sus = struct.unpack("<II", b[sp:sp+8])
            sp += 8
            sub_headers.append((scs, sus))

        data_p = sp
        chunk_decomp = bytearray()
        for scs, sus in sub_headers:
            sb = b[data_p:data_p+scs]
            data_p += scs
            if scs == sus:
                chunk_decomp.extend(sb)
            elif comp_flags == 1:
                chunk_decomp.extend(zlib.decompress(sb))

        if len(out) < uo:
            out.extend(b"\x00" * (uo - len(out)))
        out[uo:uo+len(chunk_decomp)] = chunk_decomp
    return bytes(out)


def is_clean_ascii_text(b: bytes) -> bool:
    if not b or b[-1] != 0:
        return False
    body = b[:-1]
    if len(body) < 3:
        return False
    if not all(32 <= x <= 126 or x in (10, 13, 9) for x in body):
        return False
    s = body.decode("latin1")
    return any(c.isalpha() for c in s)


def categorize_knight_int(fname: str, section: str, key: str) -> str:
    fl = fname.lower()
    su = section.upper()
    if "RIDDLE" in fl or "RIDDLE" in su or "ENIGMA" in su:
        return "05_riddles_special"
    if fl == "bios.int" or su in ("BIOS", "PROFILES", "DATABASE", "GALLERY"):
        return "04_lore_tapes"
    if "thug" in fl or "barks" in fl:
        return "03_thug_barks"
    if "story" in fl or "cutscene" in fl or "patchedsubtitles" in fl or "mostwanted" in fl:
        return "02_story_dialogue"
    return "01_ui_system"


def categorize_knight_upk(upk_name: str, speaker: str) -> str:
    u = upk_name.upper()
    s = speaker.upper()
    if "RIDDLE" in u or "ENIGMA" in u:
        return "05_riddles_special"
    if "TAPE" in u or "PROFILE" in u or "BIO" in u:
        return "04_lore_tapes"
    if "THUG" in u or "THUG" in s or "BARK" in u or "MILITIA" in u or "COP" in u or "GCPD" in u:
        return "03_thug_barks"
    return "02_story_dialogue"


def run_knight_extraction(allow_legacy_backup: bool = False):
    games = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.is_file() else detect_all_games(CONFIG_PATH)
    knight_root = Path(games["knight"]["root_dir"])
    resolver = resolve_game_source("knight", knight_root, allow_legacy_backup=allow_legacy_backup)

    backup_dir = WORK_DIR / "raw_backup"
    meta_dir = WORK_DIR / "metadata"
    batches_dir = WORK_DIR / "batches"
    backup_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    categories = [
        "01_ui_system",
        "02_story_dialogue",
        "03_thug_barks",
        "04_lore_tapes",
        "05_riddles_special",
    ]
    cat_prefixes = {
        "01_ui_system": "K_UI",
        "02_story_dialogue": "K_ST",
        "03_thug_barks": "K_TH",
        "04_lore_tapes": "K_LR",
        "05_riddles_special": "K_RD",
    }

    dedup_map: Dict[Tuple[str, str], str] = {}
    id_map: Dict[str, Dict[str, Any]] = {}
    cat_counters = {c: 0 for c in categories}
    cat_items: Dict[str, List[Dict[str, Any]]] = {c: [] for c in categories}

    def register_string(cat: str, raw_en: str, speaker: str, ctx: str, source_ref: Dict[str, Any]) -> str:
        clean_en, tags = extract_tags(raw_en)
        dedup_key = (cat, raw_en)
        if dedup_key in dedup_map:
            eid = dedup_map[dedup_key]
            id_map[eid]["sources"].append(source_ref)
            if speaker and not id_map[eid].get("sp"):
                id_map[eid]["sp"] = speaker
            if ctx and not id_map[eid].get("ctx"):
                id_map[eid]["ctx"] = ctx
            return eid

        cat_counters[cat] += 1
        eid = f"{cat_prefixes[cat]}_{cat_counters[cat]:04d}"
        dedup_map[dedup_key] = eid
        id_map[eid] = {
            "id": eid,
            "category": cat,
            "sp": speaker,
            "en_raw": raw_en,
            "en_clean": clean_en,
            "tags": tags,
            "ctx": ctx,
            "sources": [source_ref],
        }
        obj: Dict[str, Any] = {"id": eid}
        if speaker:
            obj["sp"] = speaker
        obj["en"] = clean_en
        if ctx:
            obj["ctx"] = ctx
        cat_items[cat].append(obj)
        return eid

    # 1. Extract .int files (via verified OriginalSourceResolver)
    int_files = [
        (rel_p, p) for rel_p, p in resolver.iter_dir_files(".", "*.int", recursive=True)
        if "Engine" not in Path(rel_p).parts and p.name not in SKIP_INT_FILES and not p.name.lower().startswith("credits")
    ]
    int_count = 0
    for rel_p, p in int_files:
        bkp = backup_dir / rel_p
        if not bkp.exists():
            bkp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, bkp)

        raw_bytes = p.read_bytes()
        text = raw_bytes.decode("utf-16", errors="replace") if raw_bytes.startswith(b"\xff\xfe") else raw_bytes.decode("latin1", errors="replace")
        section = ""
        for line_idx, line in enumerate(text.splitlines()):
            s = line.strip()
            if not s or s.startswith(";"):
                continue
            if s.startswith("[") and s.endswith("]"):
                section = s[1:-1]
                continue
            if "=" in s:
                k, v = s.split("=", 1)
                k_str, v_str = k.strip(), v.strip()
                if not should_translate_int_value(p.name, section, k_str, v_str):
                    continue
                cat = categorize_knight_int(p.name, section, k_str)
                sp = infer_speaker_from_token(f"{p.name}_{section}_{k_str}")
                ctx = f"{p.name}:{section}" if cat in ("04_lore_tapes", "05_riddles_special") else ""
                register_string(
                    cat=cat,
                    raw_en=v_str,
                    speaker=sp,
                    ctx=ctx,
                    source_ref={"type": "int", "rel_path": rel_p, "line_idx": line_idx, "section": section, "key": k_str}
                )
                int_count += 1

    # 2. Extract dialogue UPKs in BmGame/CookedPCConsole (via verified OriginalSourceResolver)
    candidate_upks = [
        (rel_path, p) for rel_path, p in resolver.iter_dir_files("BmGame/CookedPCConsole", "*.upk", recursive=False)
        if any(k in p.name.upper() for k in ["BARK", "CINE", "CONVO", "DIALOGUE", "DLG", "AUDIO", "OW_THUG"])
    ]
    upk_scanned = 0
    upk_cues = 0

    for rel_path, p in candidate_upks:
        try:
            raw = p.read_bytes()
            if len(raw) < 120:
                continue
            decomp = decomp_upk_knight(raw)

            magic, ver, lic, hdr_size, folder_len = struct.unpack("<IHHII", decomp[:16])
            off = 16 + folder_len
            name_cnt, name_off, exp_cnt, exp_off = struct.unpack("<IIII", decomp[off+4:off+20])
            pos = name_off
            names = []
            for _ in range(name_cnt):
                slen = struct.unpack("<i", decomp[pos:pos+4])[0]
                s = decomp[pos+4:pos+4+slen-1].decode("latin1", errors="replace")
                names.append(s)
                pos += 4 + slen + 8

            if "DialogueText" not in names:
                continue

            dt_idx = names.index("DialogueText")
            target = struct.pack("<II", dt_idx, 0)
            sp = infer_speaker_from_token(p.stem)
            cur = 0
            has_match = False
            cue_idx = 0

            while True:
                pos_dt = decomp.find(target, cur)
                if pos_dt == -1:
                    break
                cur = pos_dt + 8
                if pos_dt + 24 <= len(decomp):
                    slen = struct.unpack("<i", decomp[pos_dt+20:pos_dt+24])[0]
                    if 3 <= slen < 2000 and pos_dt + 24 + slen <= len(decomp):
                        raw_s = decomp[pos_dt+24:pos_dt+24+slen]
                        if is_clean_ascii_text(raw_s):
                            txt = raw_s[:-1].decode("latin1").strip()
                            if txt.lower() not in PLACEHOLDER_VALUES:
                                cat = categorize_knight_upk(p.name, sp)
                                register_string(
                                    cat=cat,
                                    raw_en=txt,
                                    speaker=sp,
                                    ctx="",
                                    source_ref={"type": "upk_knight", "rel_path": rel_path, "int_offset": pos_dt, "cue_idx": cue_idx}
                                )
                                upk_cues += 1
                                has_match = True
                                cue_idx += 1
            if has_match:
                upk_scanned += 1
        except Exception:
            pass

    (meta_dir / "id_map.json").write_text(json.dumps(id_map, indent=2, ensure_ascii=False), encoding="utf-8")

    stats = {
        "game": "Batman: Arkham Knight",
        "int_files_scanned": len(int_files),
        "upk_files_with_subtitles": upk_scanned,
        "raw_int_occurrences": int_count,
        "raw_upk_occurrences": upk_cues,
        "unique_translatable_entries": len(id_map),
        "categories": {}
    }

    for cat in categories:
        cat_dir = batches_dir / cat
        cat_dir.mkdir(parents=True, exist_ok=True)
        for old_f in cat_dir.glob("*.jsonl"):
            old_f.unlink()
        items = cat_items[cat]
        num_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE
        for b_idx in range(num_batches):
            chunk = items[b_idx * BATCH_SIZE : (b_idx + 1) * BATCH_SIZE]
            b_file = cat_dir / f"batch_{b_idx + 1:03d}.input.jsonl"
            b_file.write_text("\n".join(json.dumps(obj, ensure_ascii=False) for obj in chunk) + "\n", encoding="utf-8")
        stats["categories"][cat] = {
            "unique_entries": len(items),
            "batches": num_batches,
            "total_english_words": sum(len(x["en"].split()) for x in items)
        }

    (meta_dir / "extraction_stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run_knight_extraction()
