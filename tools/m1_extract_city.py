"""
Module 1 for Game 2 (Batman: Arkham City GOTY):
Extracts all translatable strings from:
1. BmGame/Localization/INT/*.int (39 files)
2. BmGame/CookedPCConsole/*.upk (all UPK packages containing LocalizedSubtitles)
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

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "game_paths.json"
WORK_DIR = ROOT_DIR / "workspace" / "city"
BATCH_SIZE = 100

INT_BLOCK_RE = re.compile(rb"\x04\x00\x00\x00INT\x00\x09\x00", re.DOTALL)
CUE_TEXT_RE = re.compile(rb"\x0d\x00\x00\x00", re.DOTALL)


def decomp_upk_v805(b: bytes) -> bytes:
    magic, ver, lic, hdr_size, folder_len = struct.unpack("<IHHII", b[:16])
    off = 16 + folder_len
    name_cnt, name_off = struct.unpack("<II", b[off+4:off+12])
    gen_cnt = struct.unpack("<I", b[off+64:off+68])[0]
    c_off = off + 68 + gen_cnt * 12 + 8
    comp_flags, chunk_cnt = struct.unpack("<II", b[c_off:c_off+8])
    if comp_flags == 0:
        return b
    pos = c_off + 8
    chunks = [struct.unpack("<IIII", b[pos+i*16:pos+i*16+16]) for i in range(chunk_cnt)]
    full = bytearray(b[:name_off])
    for uo, us, co, cs in chunks:
        tag, blk_sz, c_tot, u_tot = struct.unpack("<IIII", b[co:co+16])
        n_sub = (u_tot + blk_sz - 1) // blk_sz
        sp = co + 16
        subs = [struct.unpack("<II", b[sp+i*8:sp+i*8+8]) for i in range(n_sub)]
        dp = sp + n_sub * 8
        for cs_sub, us_sub in subs:
            full.extend(b[dp:dp+cs_sub] if cs_sub == us_sub else lzo1x_decompress(b[dp:dp+cs_sub]))
            dp += cs_sub
    return bytes(full)


def categorize_city_int(fname: str, section: str, key: str) -> str:
    fl = fname.lower()
    su = section.upper()
    if fl == "riddler.int" or "ENYGMA" in su or "RIDDLE" in su:
        return "05_riddles_special"
    if fl == "bios.int" or su in ("ARKHAMCITYSTORIES", "BIOS", "TAPES", "CONCEPTS", "GALLERY", "AZRAEL"):
        return "04_lore_tapes"
    if su in ("CUTSCENES", "TVMOVIE", "SEQUENCE") or fl in ("tvmovie.int", "sequence.int", "storydlc1.int", "cwep.int", "pdlc.int"):
        return "02_story_dialogue"
    return "01_ui_system"


def categorize_city_upk(upk_name: str, speaker: str) -> str:
    u = upk_name.upper()
    s = speaker.upper()
    if "RIDDLER" in u or "RIDDLER" in s or "ENIGMA" in u:
        return "05_riddles_special"
    if "TAPE" in u or "STORY" in u or "TEXT_SF" in u:
        return "04_lore_tapes"
    if "THUG" in u or "THUG" in s or "INMATE" in s or "GUARD" in s or "TYGER" in s:
        return "03_thug_barks"
    return "02_story_dialogue"


def run_city_extraction(allow_legacy_backup: bool = False):
    games = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.is_file() else detect_all_games(CONFIG_PATH)
    city_root = Path(games["city"]["root_dir"])
    resolver = resolve_game_source("city", city_root, allow_legacy_backup=allow_legacy_backup)

    backup_dir = WORK_DIR / "raw_backup" / "INT"
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
        "01_ui_system": "C_UI",
        "02_story_dialogue": "C_ST",
        "03_thug_barks": "C_TH",
        "04_lore_tapes": "C_LR",
        "05_riddles_special": "C_RD",
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
    int_files = resolver.iter_dir_files("BmGame/Localization/INT", "*.int", recursive=False)
    int_files_upper = resolver.iter_dir_files("BmGame/Localization/INT", "*.INT", recursive=False)
    seen_int = set()
    int_count = 0
    for _rel_int, p in int_files + int_files_upper:
        if p.name.lower() in seen_int:
            continue
        seen_int.add(p.name.lower())
        fname = p.name
        bkp = backup_dir / fname
        if not bkp.exists():
            shutil.copy2(p, bkp)
        if fname in SKIP_INT_FILES:
            continue
        raw_bytes = p.read_bytes()
        text = raw_bytes.decode("utf-16") if raw_bytes.startswith(b"\xff\xfe") else raw_bytes.decode("latin1")
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
                cat = categorize_city_int(fname, section, k_str)
                sp = infer_speaker_from_token(f"{section}_{k_str}")
                ctx = f"{fname}:{section}" if cat in ("04_lore_tapes", "05_riddles_special") else ""
                register_string(
                    cat=cat,
                    raw_en=v_str,
                    speaker=sp,
                    ctx=ctx,
                    source_ref={"type": "int", "file": fname, "line_idx": line_idx, "section": section, "key": k_str}
                )
                int_count += 1

    # 2. Extract UPKs containing LocalizedSubtitles (via verified OriginalSourceResolver)
    all_upks = resolver.iter_dir_files("BmGame/CookedPCConsole", "*.upk", recursive=True)
    upk_scanned = 0
    upk_cues = 0
    for rel_from_root, p in all_upks:
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
            rel_path = Path(rel_from_root).relative_to("BmGame/CookedPCConsole").as_posix()
            has_match = False
            for m in INT_BLOCK_RE.finditer(full):
                pos = m.end()
                if pos + 22 > len(full):
                    continue
                arr_sz = struct.unpack("<I", full[pos+10:pos+14])[0]
                cnt = struct.unpack("<I", full[pos+18:pos+22])[0]
                if not (1 <= cnt <= 50 and 4 <= arr_sz <= 100000):
                    continue
                arr_slice = full[pos+22 : pos+18+arr_sz]
                # Try to find speaker name after JPN block (~within 2000 bytes after arr_slice)
                sp = infer_speaker_from_token(p.stem)
                cpos = 0
                cue_idx = 0
                while cpos < len(arr_slice) - 8 and cue_idx < cnt:
                    idx_t = arr_slice.find(b"\x0d\x00\x00\x00", cpos)
                    if idx_t == -1:
                        break
                    slen = struct.unpack("<i", arr_slice[idx_t+4:idx_t+8])[0]
                    if slen > 0 and idx_t + 8 + slen <= len(arr_slice):
                        txt = arr_slice[idx_t+8 : idx_t+8+slen-1].decode("latin1", errors="replace").strip()
                        cpos = idx_t + 8 + slen
                    elif slen < 0 and idx_t + 8 + (-slen)*2 <= len(arr_slice):
                        txt = arr_slice[idx_t+8 : idx_t+8+(-slen)*2-2].decode("utf-16le", errors="replace").strip()
                        cpos = idx_t + 8 + (-slen)*2
                    else:
                        cpos = idx_t + 4
                        continue
                    if txt and txt.lower() not in PLACEHOLDER_VALUES:
                        cat = categorize_city_upk(p.name, sp)
                        register_string(
                            cat=cat,
                            raw_en=txt,
                            speaker=sp,
                            ctx="",
                            source_ref={"type": "upk_city", "rel_path": rel_path, "int_offset": m.start(), "cue_idx": cue_idx}
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
        "game": "Batman: Arkham City GOTY",
        "int_files_scanned": len(seen_int),
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
    run_city_extraction()
