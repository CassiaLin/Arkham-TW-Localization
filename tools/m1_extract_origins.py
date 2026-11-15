"""
Module 1 for Game 3 (Batman: Arkham Origins):
Extracts all translatable strings from:
1. SinglePlayer/BMGame/Localization/INT/*.int + SinglePlayer/DLC/**/INT/*.int + Online/BmGame/Localization/INT/*.int
2. SinglePlayer/BMGame/CookedPCConsole/*.upk + SinglePlayer/DLC/**/*.upk (LocalizedSubtitles)
Categorizes into 5 categories and outputs token-efficient JSONL batches + id_map.json.
"""
import os
import re
import sys
import json
import shutil
import struct
from pathlib import Path
from typing import Dict, List, Tuple, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upk_core import lzo1x_decompress
from game_detector import detect_all_games
from source_resolver import resolve_game_source
from m1_extract import extract_tags, infer_speaker_from_token, should_translate_int_value, SKIP_INT_FILES, PLACEHOLDER_VALUES
from m1_extract_city import decomp_upk_v805, INT_BLOCK_RE

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "game_paths.json"
WORK_DIR = ROOT_DIR / "workspace" / "origins"
BATCH_SIZE = 100


def categorize_origins_int(rel_path: str, fname: str, section: str, key: str) -> str:
    fl = fname.lower()
    su = section.upper()
    if "ENIGMA" in su or "RIDDLE" in su or "ANARKY" in su:
        return "05_riddles_special"
    if fl == "bios.int" or su in (
        "BIOS", "TAPES", "CONCEPTS", "GALLERY", "DATABASE",
        "GOTHAMINTELLIGENCE", "CASEFILES", "DBCRIMEREPORTCSI", "SYNOPSIS"
    ):
        return "04_lore_tapes"
    if fl.startswith("speechdatabase_"):
        if "thug" in fl or "bane" in fl or "joker" in fl:
            return "03_thug_barks"
        return "02_story_dialogue"
    if su in ("CUTSCENES", "TVMOVIE", "SEQUENCE", "SIDESTORY", "MOSTWANTED") or "dlcfreeze" in fl or "dlcstory" in fl:
        return "02_story_dialogue"
    return "01_ui_system"


def categorize_origins_upk(upk_name: str, speaker: str) -> str:
    u = upk_name.upper()
    s = speaker.upper()
    if "RIDDLER" in u or "ENIGMA" in u or "ANARKY" in u:
        return "05_riddles_special"
    if "TAPE" in u or "DATABASE" in u or "PROFILE" in u:
        return "04_lore_tapes"
    if "THUG" in u or "THUG" in s or "CRIME" in u or "SWAT" in u or "COP" in u or "GCPD" in u:
        return "03_thug_barks"
    return "02_story_dialogue"


def run_origins_extraction(allow_legacy_backup: bool = False):
    games = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.is_file() else detect_all_games(CONFIG_PATH)
    origins_root = Path(games["origins"]["root_dir"])
    resolver = resolve_game_source("origins", origins_root, allow_legacy_backup=allow_legacy_backup)

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
        "01_ui_system": "O_UI",
        "02_story_dialogue": "O_ST",
        "03_thug_barks": "O_TH",
        "04_lore_tapes": "O_LR",
        "05_riddles_special": "O_RD",
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

    # 1. Extract all game .int files (excluding Engine/, via verified OriginalSourceResolver)
    int_files = [
        (rel_p, p) for rel_p, p in resolver.iter_dir_files(".", "*", recursive=True)
        if p.suffix.lower() == ".int" and "Engine" not in Path(rel_p).parts
    ]
    int_count = 0
    for rel_p, p in int_files:
        fname = p.name
        if fname in SKIP_INT_FILES or fname.lower().startswith("credits"):
            continue
        bkp = backup_dir / rel_p
        if not bkp.exists():
            bkp.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, bkp)

        raw_bytes = p.read_bytes()
        text = raw_bytes.decode("utf-16") if raw_bytes.startswith(b"\xff\xfe") else raw_bytes.decode("latin1", errors="replace")
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
                if not should_translate_int_value(fname, section, k_str, v_str):
                    continue
                cat = categorize_origins_int(rel_p, fname, section, k_str)
                sp = infer_speaker_from_token(f"{fname}_{section}_{k_str}")
                ctx = f"{fname}:{section}" if cat in ("04_lore_tapes", "05_riddles_special") else ""
                register_string(
                    cat=cat,
                    raw_en=v_str,
                    speaker=sp,
                    ctx=ctx,
                    source_ref={"type": "int", "rel_path": rel_p, "line_idx": line_idx, "section": section, "key": k_str}
                )
                int_count += 1

    # 2. Extract UPKs containing LocalizedSubtitles in SinglePlayer (via verified OriginalSourceResolver)
    all_upks = resolver.iter_dir_files("SinglePlayer", "*.upk", recursive=True)
    upk_scanned = 0
    upk_cues = 0
    for rel_path, p in all_upks:
        try:
            with open(p, "rb") as f:
                b0 = f.read(160000)
            if len(b0) < 120:
                continue
            magic, ver, lic, hdr_size, folder_len = struct.unpack("<IHHII", b0[:16])
            off = 16 + folder_len
            gen_cnt = struct.unpack("<I", b0[off+64:off+68])[0]
            c_off = off + 68 + gen_cnt * 12 + 8
            comp_flags, chunk_cnt = struct.unpack("<II", b0[c_off:c_off+8])
            if comp_flags == 2 and chunk_cnt > 0:
                uo, us, co, cs = struct.unpack("<IIII", b0[c_off+8:c_off+24])
                tag, blk_sz, c_tot, u_tot = struct.unpack("<IIII", b0[co:co+16])
                n_sub = (u_tot + blk_sz - 1) // blk_sz
                cs0, us0 = struct.unpack("<II", b0[co+16:co+24])
                dp = co + 16 + n_sub * 8
                if dp + cs0 > len(b0):
                    continue
                sb0 = b0[dp:dp+cs0] if cs0 == us0 else lzo1x_decompress(b0[dp:dp+cs0])
            else:
                sb0 = b0
            if b"LocalizedSubtitles" not in sb0:
                continue

            full = decomp_upk_v805(p.read_bytes())
            magic, ver, lic, hdr_size, folder_len = struct.unpack("<IHHII", full[:16])
            off = 16 + folder_len
            name_cnt, name_off, exp_cnt, exp_off = struct.unpack("<IIII", full[off+4:off+20])
            pos = name_off
            names = []
            for _ in range(name_cnt):
                slen = struct.unpack("<i", full[pos:pos+4])[0]
                s = full[pos+4:pos+4+slen-1].decode("latin1", errors="replace")
                names.append(s)
                pos += 4 + slen + 8

            if "LocalizedSubtitles" not in names or "Text" not in names:
                continue

            text_idx = names.index("Text")
            lang_int_idx = names.index("LANG_Int") if "LANG_Int" in names else None
            sp = infer_speaker_from_token(p.stem)
            has_match = False
            cue_idx = 0

            if lang_int_idx is not None:
                target_lang = struct.pack("<II", lang_int_idx, 0)
                cur = 0
                while True:
                    pos_lang = full.find(target_lang, cur)
                    if pos_lang == -1:
                        break
                    cur = pos_lang + 8
                    window = full[pos_lang:pos_lang+250]
                    p_text = 0
                    while True:
                        found_t = window.find(struct.pack("<I", text_idx), p_text)
                        if found_t == -1:
                            break
                        abs_t = pos_lang + found_t
                        p_text = found_t + 4
                        for off_s in [16, 20, 24]:
                            if abs_t + off_s + 4 <= len(full):
                                slen = struct.unpack("<i", full[abs_t+off_s:abs_t+off_s+4])[0]
                                if 2 <= slen < 2000 and abs_t + off_s + 4 + slen <= len(full):
                                    txt = full[abs_t+off_s+4:abs_t+off_s+4+slen-1].decode("latin1", errors="replace").strip()
                                    if len(txt) > 1 and any(c.isalpha() for c in txt) and txt.lower() not in PLACEHOLDER_VALUES:
                                        cat = categorize_origins_upk(p.name, sp)
                                        register_string(
                                            cat=cat,
                                            raw_en=txt,
                                            speaker=sp,
                                            ctx="",
                                            source_ref={"type": "upk_origins", "rel_path": rel_path, "int_offset": abs_t, "cue_idx": cue_idx}
                                        )
                                        upk_cues += 1
                                        has_match = True
                                        cue_idx += 1
                                    break
                                elif -2000 < slen <= -2 and abs_t + off_s + 4 + (-slen)*2 <= len(full):
                                    txt = full[abs_t+off_s+4:abs_t+off_s+4+(-slen)*2-2].decode("utf-16le", errors="replace").strip()
                                    if len(txt) > 1 and any(c.isalpha() for c in txt) and txt.lower() not in PLACEHOLDER_VALUES:
                                        cat = categorize_origins_upk(p.name, sp)
                                        register_string(
                                            cat=cat,
                                            raw_en=txt,
                                            speaker=sp,
                                            ctx="",
                                            source_ref={"type": "upk_origins", "rel_path": rel_path, "int_offset": abs_t, "cue_idx": cue_idx}
                                        )
                                        upk_cues += 1
                                        has_match = True
                                        cue_idx += 1
                                    break
            else:
                target_t = struct.pack("<I", text_idx)
                cur = 0
                while True:
                    pos_t = full.find(target_t, cur)
                    if pos_t == -1:
                        break
                    cur = pos_t + 4
                    for off_s in [16, 20, 24]:
                        if pos_t + off_s + 4 <= len(full):
                            slen = struct.unpack("<i", full[pos_t+off_s:pos_t+off_s+4])[0]
                            if 2 <= slen < 2000 and pos_t + off_s + 4 + slen <= len(full):
                                txt = full[pos_t+off_s+4:pos_t+off_s+4+slen-1].decode("latin1", errors="replace").strip()
                                if len(txt) > 1 and any(c.isalpha() for c in txt) and " " in txt and txt.lower() not in PLACEHOLDER_VALUES:
                                    cat = categorize_origins_upk(p.name, sp)
                                    register_string(
                                        cat=cat,
                                        raw_en=txt,
                                        speaker=sp,
                                        ctx="",
                                        source_ref={"type": "upk_origins", "rel_path": rel_path, "int_offset": pos_t, "cue_idx": cue_idx}
                                    )
                                    upk_cues += 1
                                    has_match = True
                                    cue_idx += 1
                                break
                            elif -2000 < slen <= -2 and pos_t + off_s + 4 + (-slen)*2 <= len(full):
                                txt = full[pos_t+off_s+4:pos_t+off_s+4+(-slen)*2-2].decode("utf-16le", errors="replace").strip()
                                if len(txt) > 1 and any(c.isalpha() for c in txt) and " " in txt and txt.lower() not in PLACEHOLDER_VALUES:
                                    cat = categorize_origins_upk(p.name, sp)
                                    register_string(
                                        cat=cat,
                                        raw_en=txt,
                                        speaker=sp,
                                        ctx="",
                                        source_ref={"type": "upk_origins", "rel_path": rel_path, "int_offset": pos_t, "cue_idx": cue_idx}
                                    )
                                    upk_cues += 1
                                    has_match = True
                                    cue_idx += 1
                                break
            if has_match:
                upk_scanned += 1
        except Exception:
            pass

    (meta_dir / "id_map.json").write_text(json.dumps(id_map, indent=2, ensure_ascii=False), encoding="utf-8")

    stats = {
        "game": "Batman: Arkham Origins",
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
            b_file.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in chunk) + "\n", encoding="utf-8")
        stats["categories"][cat] = {
            "unique_entries": len(items),
            "batches": num_batches,
            "total_english_words": sum(len(x["en"].split()) for x in items)
        }

    (meta_dir / "extraction_stats.json").write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run_origins_extraction()
