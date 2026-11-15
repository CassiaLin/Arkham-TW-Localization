"""
Module 5C (M5-Repacker-UPK): Reinjects translated UTF-16 LE subtitles into
Unreal Engine 3 .upk packages across ALL 4 Arkham games:
  - asylum (v576 SoundNodeWave)
  - city   (v805 LocalizedSubtitles)
  - origins(v807 LocalizedSubtitles / Text)
  - knight (v863 DialogueText)
"""
import os
import re
import sys
import json
import struct
import argparse
from pathlib import Path
from typing import Dict, List, Tuple, Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upk_core import UPKPackage
from m5_repack_int import restore_tags, load_translations
from m5_font_builder import decomp_upk_modern_clean
from translation_pack import load_aux_lookup

ROOT_DIR = Path(__file__).resolve().parent.parent


def encode_fstring_utf16(text: str) -> bytes:
    """Encode a string as UE3 FString (UTF-16 LE if non-ASCII, else ASCII)."""
    if not text:
        return struct.pack("<i", 0)
    try:
        raw = text.encode("ascii")
        return struct.pack("<i", len(raw) + 1) + raw + b"\x00"
    except UnicodeEncodeError:
        raw = text.encode("utf-16-le")
        char_cnt = len(text) + 1
        return struct.pack("<i", -char_cnt) + raw + b"\x00\x00"


def parse_modern_upk(raw_bytes: bytes) -> Tuple[bytearray, List[str], List[Dict[str, Any]]]:
    """Decompress v805/v807/v863 UPK and parse its NameTable & ExportTable."""
    full, exp_cnt, exp_off, hdr_size = decomp_upk_modern_clean(raw_bytes)
    off = 16 + struct.unpack("<I", full[12:16])[0]
    name_cnt, name_off = struct.unpack("<II", full[off + 4:off + 12])
    dep_off = struct.unpack("<I", full[off + 28:off + 32])[0]

    pos = name_off
    names: List[str] = []
    for _ in range(name_cnt):
        slen = struct.unpack("<i", full[pos:pos + 4])[0]
        if slen > 0:
            s = full[pos + 4:pos + 4 + slen - 1].decode("latin1", errors="replace")
            pos += 4 + slen + 8
        elif slen < 0:
            s = full[pos + 4:pos + 4 + (-slen) * 2 - 2].decode("utf-16-le", errors="replace")
            pos += 4 + (-slen) * 2 + 8
        else:
            s = ""
            pos += 4 + 8
        names.append(s)

    exports: List[Dict[str, Any]] = []
    fixed_72 = (dep_off - exp_off) == exp_cnt * 72
    ep = exp_off
    for i in range(exp_cnt):
        sz, off_e = struct.unpack("<ii", full[ep + 36:ep + 44])
        if fixed_72:
            elen = 72
        else:
            net_cnt = struct.unpack("<I", full[ep + 48:ep + 52])[0]
            elen = 72 + net_cnt * 4
        exports.append({
            "index": i,
            "size": sz,
            "offset": off_e,
            "entry_offset": ep,
        })
        ep += elen

    return bytearray(full), names, exports


def find_export_for_offset(exports: List[Dict[str, Any]], target_off: int) -> Optional[Dict[str, Any]]:
    for exp in exports:
        if exp["offset"] <= target_off < exp["offset"] + exp["size"]:
            return exp
    return None


def apply_export_edits(
    out_data: bytearray,
    exp: Dict[str, Any],
    size_deltas: Dict[int, int],
    replacements: List[Tuple[int, int, bytes]],
):
    """Apply size updates and byte replacements to one export and append it to EOF."""
    exp_start = exp["offset"]
    exp_end = exp["offset"] + exp["size"]
    exp_buf = bytearray(out_data[exp_start:exp_end])

    for abs_off, delta in size_deltas.items():
        if delta == 0:
            continue
        rel_off = abs_off - exp_start
        if 0 <= rel_off <= len(exp_buf) - 4:
            old_val = struct.unpack_from("<i", exp_buf, rel_off)[0]
            struct.pack_into("<i", exp_buf, rel_off, old_val + delta)

    replacements.sort(key=lambda x: x[0], reverse=True)
    for r_start, r_end, new_b in replacements:
        rs = r_start - exp_start
        re_ = r_end - exp_start
        exp_buf[rs:re_] = new_b

    new_exp_start = len(out_data)
    new_exp_size = len(exp_buf)
    out_data.extend(exp_buf)
    struct.pack_into("<ii", out_data, exp["entry_offset"] + 36, new_exp_size, new_exp_start)


# ---------------------------------------------------------------------------
# 1. ASYLUM (v576 SoundNodeWave)
# ---------------------------------------------------------------------------
def patch_asylum_upk_file(src_upk: Path, dst_upk: Path, export_edits: Dict[Tuple[str, int], Dict[int, str]]) -> int:
    pkg = UPKPackage(str(src_upk))
    out_data = bytearray(pkg.data)
    cues_patched = 0

    for exp in pkg.exports:
        key = (exp["name"], exp["num"])
        if key not in export_edits or exp["size"] <= 20:
            continue
        cue_map = export_edits[key]
        props, end_pos = pkg.parse_properties(exp["offset"] + 4)
        exp_start = exp["offset"]
        exp_end = exp["offset"] + exp["size"]

        size_deltas: Dict[int, int] = {}
        replacements: List[Tuple[int, int, bytes]] = []

        for pr in props:
            if pr["name"] == "Subtitles" and pr["type"] == "ArrayProperty":
                arr_sz_off = pr["val_pos"] - 8
                for cue_idx, item_props in enumerate(pr["value"]):
                    if cue_idx not in cue_map:
                        continue
                    for ip in item_props:
                        if ip["name"] == "Text" and ip["type"] == "StrProperty":
                            new_fstr = encode_fstring_utf16(cue_map[cue_idx])
                            old_sz = ip["size"]
                            delta = len(new_fstr) - old_sz
                            str_sz_off = ip["val_pos"] - 8
                            size_deltas[str_sz_off] = size_deltas.get(str_sz_off, 0) + delta
                            size_deltas[arr_sz_off] = size_deltas.get(arr_sz_off, 0) + delta
                            replacements.append((ip["val_pos"], ip["val_pos"] + old_sz, new_fstr))
                            cues_patched += 1

            elif pr["name"] == "LocalizedSubtitles" and pr["type"] == "ArrayProperty":
                outer_sz_off = pr["val_pos"] - 8
                for loc_entry in pr["value"]:
                    for lp in loc_entry:
                        if lp["name"] == "Subtitles" and lp["type"] == "ArrayProperty":
                            inner_sz_off = lp["val_pos"] - 8
                            for cue_idx, item_props in enumerate(lp["value"]):
                                if cue_idx not in cue_map:
                                    continue
                                for ip in item_props:
                                    if ip["name"] == "Text" and ip["type"] == "StrProperty":
                                        new_fstr = encode_fstring_utf16(cue_map[cue_idx])
                                        old_sz = ip["size"]
                                        delta = len(new_fstr) - old_sz
                                        str_sz_off = ip["val_pos"] - 8
                                        size_deltas[str_sz_off] = size_deltas.get(str_sz_off, 0) + delta
                                        size_deltas[inner_sz_off] = size_deltas.get(inner_sz_off, 0) + delta
                                        size_deltas[outer_sz_off] = size_deltas.get(outer_sz_off, 0) + delta
                                        replacements.append((ip["val_pos"], ip["val_pos"] + old_sz, new_fstr))

        if not replacements:
            continue

        exp_buf = bytearray(pkg.data[exp_start:exp_end])
        for abs_off, delta in size_deltas.items():
            rel_off = abs_off - exp_start
            old_val = struct.unpack_from("<i", exp_buf, rel_off)[0]
            struct.pack_into("<i", exp_buf, rel_off, old_val + delta)

        replacements.sort(key=lambda x: x[0], reverse=True)
        total_prop_delta = 0
        for r_start, r_end, new_b in replacements:
            rs = r_start - exp_start
            re_ = r_end - exp_start
            total_prop_delta += len(new_b) - (re_ - rs)
            exp_buf[rs:re_] = new_b

        new_exp_size = len(exp_buf)
        in_place = new_exp_size <= exp["size"]
        new_exp_start = exp_start if in_place else len(out_data)
        bulk_rel = (end_pos - exp_start) + total_prop_delta
        while bulk_rel + 16 <= new_exp_size:
            b_flags, _, sz_disk, _ = struct.unpack_from("<IIII", exp_buf, bulk_rel)
            new_off_in_file = new_exp_start + bulk_rel + 16
            struct.pack_into("<I", exp_buf, bulk_rel + 12, new_off_in_file)
            bulk_rel += 16
            if (b_flags & 0x01) == 0 and (b_flags & 0x20) == 0 and sz_disk > 0:
                bulk_rel += sz_disk

        if in_place:
            out_data[exp_start:exp_start + new_exp_size] = exp_buf
        else:
            out_data.extend(exp_buf)
        struct.pack_into("<ii", out_data, exp["entry_offset"] + 32, new_exp_size, new_exp_start)

    dst_upk.parent.mkdir(parents=True, exist_ok=True)
    dst_upk.write_bytes(bytes(out_data))
    return cues_patched


def repack_asylum_upks():
    cfg = json.loads((ROOT_DIR / "config" / "game_paths.json").read_text(encoding="utf-8"))
    cooked_dir = Path(cfg["asylum"]["cooked_dir"])
    work_dir = ROOT_DIR / "workspace" / "asylum"
    dist_cooked = ROOT_DIR / "dist" / "Arkham_Asylum_TW_Mod" / "BmGame" / "CookedPC"
    id_map = json.loads((work_dir / "metadata" / "id_map.json").read_text(encoding="utf-8"))
    translations = load_translations(work_dir)

    upk_tasks: Dict[str, Dict[Tuple[str, int], Dict[int, str]]] = {}
    for eid, meta in id_map.items():
        if eid not in translations:
            continue
        zh_restored = restore_tags(translations[eid], meta.get("tags", []))
        for src in meta.get("sources", []):
            if src.get("type") == "upk":
                upk_tasks.setdefault(src["rel_path"], {}).setdefault((src["export_name"], src["export_num"]), {})[src["cue_idx"]] = zh_restored

    bkp_cooked = Path(cfg["asylum"]["root_dir"]) / "_Backup_TW_Mod" / "BmGame" / "CookedPC"
    files_done = total_cues = 0
    for rel_path, edits in sorted(upk_tasks.items()):
        bkp_p = bkp_cooked / rel_path
        src_p = bkp_p if bkp_p.exists() and bkp_p.stat().st_size > 16 else (cooked_dir / rel_path)
        if src_p.exists():
            total_cues += patch_asylum_upk_file(src_p, dist_cooked / rel_path, edits)
            files_done += 1
    print(f"[asylum] Repack UPK done: {files_done} packages, {total_cues} SoundNodeWave cues patched.")


# ---------------------------------------------------------------------------
# 2. CITY (v805 LocalizedSubtitles)
# ---------------------------------------------------------------------------
def repack_city_upks():
    cfg = json.loads((ROOT_DIR / "config" / "game_paths.json").read_text(encoding="utf-8"))
    city_root = Path(cfg["city"]["root_dir"])
    cooked_dir = city_root / "BmGame" / "CookedPCConsole"
    bkp_cooked = city_root / "_Backup_TW_Mod" / "BmGame" / "CookedPCConsole"
    work_dir = ROOT_DIR / "workspace" / "city"
    dist_cooked = ROOT_DIR / "dist" / "Arkham_City_TW_Mod" / "BmGame" / "CookedPCConsole"
    id_map = json.loads((work_dir / "metadata" / "id_map.json").read_text(encoding="utf-8"))
    translations = load_translations(work_dir)

    # rel_path -> int_offset -> {cue_idx: zh}
    upk_tasks: Dict[str, Dict[int, Dict[int, str]]] = {}
    for eid, meta in id_map.items():
        if eid not in translations:
            continue
        zh = restore_tags(translations[eid], meta.get("tags", []))
        for src in meta.get("sources", []):
            if src.get("type") == "upk_city":
                upk_tasks.setdefault(src["rel_path"], {}).setdefault(src["int_offset"], {})[src["cue_idx"]] = zh

    files_done = total_cues = 0
    for rel_path, offset_map in sorted(upk_tasks.items()):
        bkp_p = bkp_cooked / rel_path
        src_p = bkp_p if bkp_p.exists() and bkp_p.stat().st_size > 16 else (cooked_dir / rel_path)
        if not src_p.exists():
            continue
        out_data, names, exports = parse_modern_upk(src_p.read_bytes())
        loc_idx = names.index("LocalizedSubtitles") if "LocalizedSubtitles" in names else -1
        loc_pat = struct.pack("<II", loc_idx, 0) if loc_idx >= 0 else None
        sub_idx = names.index("Subtitles") if "Subtitles" in names else -1
        sub_pat = struct.pack("<II", sub_idx, 0) if sub_idx >= 0 else None

        exp_edits: Dict[int, Tuple[Dict[str, Any], Dict[int, int], List[Tuple[int, int, bytes]]]] = {}
        for int_off, cue_map in offset_map.items():
            exp = find_export_for_offset(exports, int_off)
            if not exp:
                continue
            eid_idx = exp["index"]
            if eid_idx not in exp_edits:
                exp_edits[eid_idx] = (exp, {}, [])
            _, size_deltas, replacements = exp_edits[eid_idx]

            def patch_cue_array(arr_sz_off: int, arr_start: int, arr_end: int, cnt: int, outer_sz_off: Optional[int]) -> int:
                patched = 0
                cpos = arr_start
                cue_idx = 0
                while cpos < arr_end - 8 and cue_idx < cnt:
                    idx_t = bytes(out_data).find(b"\x0d\x00\x00\x00", cpos, arr_end)
                    if idx_t == -1:
                        break
                    slen = struct.unpack_from("<i", out_data, idx_t + 4)[0]
                    if slen > 0 and idx_t + 8 + slen <= arr_end:
                        old_end = idx_t + 8 + slen
                        cpos = old_end
                    elif slen < 0 and idx_t + 8 + (-slen) * 2 <= arr_end:
                        old_end = idx_t + 8 + (-slen) * 2
                        cpos = old_end
                    else:
                        cpos = idx_t + 4
                        continue

                    if cue_idx in cue_map:
                        new_fstr = encode_fstring_utf16(cue_map[cue_idx])
                        old_len = old_end - (idx_t + 4)
                        delta = len(new_fstr) - old_len
                        replacements.append((idx_t + 4, old_end, new_fstr))
                        size_deltas[arr_sz_off] = size_deltas.get(arr_sz_off, 0) + delta
                        if outer_sz_off is not None:
                            size_deltas[outer_sz_off] = size_deltas.get(outer_sz_off, 0) + delta
                        patched += 1
                    cue_idx += 1
                return patched

            # 1. Patch top-level Subtitles ArrayProperty (used by base INT language)
            if sub_pat:
                p_sub = bytes(out_data).rfind(sub_pat, exp["offset"], int_off)
                if p_sub != -1 and p_sub + 20 <= int_off:
                    s_arr_sz_off = p_sub + 8
                    s_arr_sz = struct.unpack_from("<I", out_data, s_arr_sz_off)[0]
                    s_cnt = struct.unpack_from("<I", out_data, p_sub + 16)[0]
                    if 0 < s_cnt < 100 and p_sub + 16 + s_arr_sz <= int_off:
                        total_cues += patch_cue_array(s_arr_sz_off, p_sub + 20, p_sub + 16 + s_arr_sz, s_cnt, None)

            # 2. Patch LocalizedSubtitles INT entry
            pos = int_off + 10
            arr_sz_off = pos + 10
            arr_sz = struct.unpack_from("<I", out_data, arr_sz_off)[0]
            cnt = struct.unpack_from("<I", out_data, pos + 18)[0]
            arr_start = pos + 22
            arr_end = pos + 18 + arr_sz

            # Find enclosing LocalizedSubtitles sz_off
            outer_sz_off = None
            if loc_pat:
                search_start = exp["offset"]
                p_loc = bytes(out_data).rfind(loc_pat, search_start, int_off)
                if p_loc != -1:
                    outer_sz = struct.unpack_from("<I", out_data, p_loc + 8)[0]
                    if p_loc + 16 <= int_off < p_loc + 16 + outer_sz:
                        outer_sz_off = p_loc + 8

            total_cues += patch_cue_array(arr_sz_off, arr_start, arr_end, cnt, outer_sz_off)

        for exp, size_deltas, replacements in exp_edits.values():
            if replacements:
                apply_export_edits(out_data, exp, size_deltas, replacements)

        dst_p = dist_cooked / rel_path
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        dst_p.write_bytes(bytes(out_data))
        files_done += 1

    print(f"[city] Repack UPK done: {files_done} packages, {total_cues} cues patched.")


# ---------------------------------------------------------------------------
# 3. ORIGINS (v807 LocalizedSubtitles / Text)
# ---------------------------------------------------------------------------
def repack_origins_upks():
    from m1_extract import extract_tags, PLACEHOLDER_VALUES

    cfg = json.loads((ROOT_DIR / "config" / "game_paths.json").read_text(encoding="utf-8"))
    origins_root = Path(cfg["origins"]["root_dir"])
    bkp_root = origins_root / "_Backup_TW_Mod"
    work_dir = ROOT_DIR / "workspace" / "origins"
    dist_root = ROOT_DIR / "dist" / "Arkham_Origins_TW_Mod"
    id_map = json.loads((work_dir / "metadata" / "id_map.json").read_text(encoding="utf-8"))
    translations = load_translations(work_dir)

    en_to_zh: Dict[str, str] = {}
    rel_set = set()
    for eid, meta in id_map.items():
        for src in meta.get("sources", []):
            if src.get("type") == "upk_origins":
                rel_set.add(src["rel_path"])
        if eid in translations:
            zh = restore_tags(translations[eid], meta.get("tags", []))
            en_to_zh[meta["en_raw"].strip()] = zh
            en_to_zh[meta["en_clean"].strip()] = zh

    extra_lookup = load_aux_lookup(work_dir, "upk_extra_zh.json", "upk_extra_sha256_zh.json")

    list_p = work_dir / "upk_list.json"
    if list_p.exists():
        rel_list = json.loads(list_p.read_text(encoding="utf-8"))
    else:
        rel_list = sorted(rel_set)

    files_done = total_cues = 0
    for rel_path in rel_list:
        bkp_p = bkp_root / rel_path
        src_p = bkp_p if bkp_p.exists() and bkp_p.stat().st_size > 16 else (origins_root / rel_path)
        if not src_p.exists() or src_p.stat().st_size <= 16:
            continue
        out_data, names, exports = parse_modern_upk(src_p.read_bytes())
        if "LocalizedSubtitles" not in names or "LANG_Int" not in names or "Text" not in names:
            continue
        loc_idx = names.index("LocalizedSubtitles")
        int_idx = names.index("LANG_Int")
        loc_pat = struct.pack("<Hq", 9, loc_idx)

        exp_edits: Dict[int, Tuple[Dict[str, Any], Dict[int, int], List[Tuple[int, int, bytes]]]] = {}
        raw_view = bytes(out_data)
        cur_search = 0

        while True:
            p_loc = raw_view.find(loc_pat, cur_search)
            if p_loc == -1:
                break
            cur_search = p_loc + 10
            try:
                _, _, arr_sz, cnt = struct.unpack_from("<Hqqi", raw_view, p_loc)
                if not (1 <= cnt <= 20 and 20 <= arr_sz <= 100000):
                    continue
                exp = find_export_for_offset(exports, p_loc)
                if not exp:
                    continue
                cur = p_loc + 22
                for _ in range(cnt):
                    cur += 19  # IsOverriden (=HqqB)
                    _, _, _, lang_ni = struct.unpack_from("<Hqqq", raw_view, cur)
                    cur += 26  # Language (=Hqqq)
                    cur_sub = cur
                    _, _, sub_sz, sub_cnt = struct.unpack_from("<Hqqi", raw_view, cur)
                    cur += 22  # Subtitles (=Hqqi)
                    for _ in range(sub_cnt):
                        cur_text = cur
                        _, _, st, slen = struct.unpack_from("<Hqqi", raw_view, cur)
                        cur += 22
                        old_start = cur_text + 18
                        if slen > 0:
                            txt = raw_view[cur:cur + slen - 1].decode("latin1", errors="replace").strip()
                            cur += slen
                        elif slen < 0:
                            txt = raw_view[cur:cur + (-slen) * 2 - 2].decode("utf-16le", errors="replace").strip()
                            cur += (-slen) * 2
                        else:
                            txt = ""
                        old_end = cur
                        cur += 24  # Time (22) + \x00\x00 (2)

                        if lang_ni == int_idx and len(txt) > 1 and any(c.isalpha() for c in txt) and txt.lower() not in PLACEHOLDER_VALUES:
                            zh = en_to_zh.get(txt)
                            if zh is None:
                                clean_t, tags = extract_tags(txt)
                                if clean_t in en_to_zh:
                                    zh = restore_tags(en_to_zh[clean_t], tags)
                            if zh is None and extra_lookup is not None:
                                zh = extra_lookup.get(txt)
                                if zh is None:
                                    clean_t, tags = extract_tags(txt)
                                    val = extra_lookup.get(clean_t)
                                    if val is not None:
                                        zh = restore_tags(val, tags)
                            if zh is not None:
                                eid_idx = exp["index"]
                                if eid_idx not in exp_edits:
                                    exp_edits[eid_idx] = (exp, {}, [])
                                _, size_deltas, replacements = exp_edits[eid_idx]
                                new_fstr = encode_fstring_utf16(zh)
                                delta = len(new_fstr) - (old_end - old_start)
                                replacements.append((old_start, old_end, new_fstr))
                                size_deltas[cur_text + 10] = size_deltas.get(cur_text + 10, 0) + delta
                                size_deltas[cur_sub + 10] = size_deltas.get(cur_sub + 10, 0) + delta
                                size_deltas[p_loc + 10] = size_deltas.get(p_loc + 10, 0) + delta
                                total_cues += 1
                    cur += 2  # outer struct terminator \x00\x00
            except Exception:
                continue

        if not exp_edits:
            continue

        for exp, size_deltas, replacements in exp_edits.values():
            if replacements:
                apply_export_edits(out_data, exp, size_deltas, replacements)

        dst_p = dist_root / rel_path
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        dst_p.write_bytes(bytes(out_data))
        files_done += 1

    print(f"[origins] Repack UPK done: {files_done} packages, {total_cues} cues patched.")


# ---------------------------------------------------------------------------
# 4. KNIGHT (v863 AkDialogueEvent / DialogueText)
# ---------------------------------------------------------------------------
def lzo1x_compress_fast(src: bytes) -> bytes:
    """Fast pure-Python LZO1X-1 compressor for 128 KB UE3 UPK sub-blocks."""
    n = len(src)
    if n <= 238:
        return bytes([n + 17]) + src + b"\x11\x00\x00"
    out = bytearray()
    ht = {}
    ip = 0
    lit_start = 0
    ip_end = n - 12
    while ip < ip_end:
        dv = src[ip:ip + 4]
        m_pos = ht.get(dv, -1)
        ht[dv] = ip
        off = ip - m_pos
        if m_pos >= 0 and 0 < off <= 0x4000:
            lit_len = ip - lit_start
            if lit_len > 0:
                if len(out) > 0 and lit_len <= 3:
                    out[-2] |= lit_len
                    out.extend(src[lit_start:ip])
                elif len(out) == 0 and lit_len <= 238:
                    out.append(lit_len + 17)
                    out.extend(src[lit_start:ip])
                elif lit_len <= 18:
                    out.append(lit_len - 3)
                    out.extend(src[lit_start:ip])
                else:
                    rem = lit_len - 18
                    out.append(0)
                    q, r = divmod(rem - 1, 255)
                    out.extend(b"\x00" * q)
                    out.append(r + 1)
                    out.extend(src[lit_start:ip])
            m_len = 4
            max_m = n - ip
            while m_len < max_m and src[m_pos + m_len] == src[ip + m_len]:
                m_len += 1
            d = off - 1
            if m_len <= 33:
                out.append(32 | (m_len - 2))
            else:
                rem = m_len - 33
                out.append(32)
                q, r = divmod(rem - 1, 255)
                out.extend(b"\x00" * q)
                out.append(r + 1)
            out.append((d & 63) << 2)
            out.append(d >> 6)
            ip += m_len
            lit_start = ip
        else:
            ip += 1
    lit_len = n - lit_start
    if lit_len > 0:
        if len(out) > 0 and lit_len <= 3:
            out[-2] |= lit_len
            out.extend(src[lit_start:n])
        elif len(out) == 0 and lit_len <= 238:
            out.append(lit_len + 17)
            out.extend(src[lit_start:n])
        elif lit_len <= 18:
            out.append(lit_len - 3)
            out.extend(src[lit_start:n])
        else:
            rem = lit_len - 18
            out.append(0)
            q, r = divmod(rem - 1, 255)
            out.extend(b"\x00" * q)
            out.append(r + 1)
            out.extend(src[lit_start:n])
    out.extend(b"\x11\x00\x00")
    return bytes(out)


def patch_knight_compressed_upk(
    src_p: Path,
    dst_p: Path,
    ak_events_zh: Dict[str, str],
    ak_en_zh: Dict[str, str],
) -> int:
    """Surgically patch AkDialogueEvent DialogueText in-place inside a compressed Knight UPK."""
    import zlib
    from upk_core import lzo1x_decompress

    raw_file = src_p.read_bytes()
    if len(raw_file) < 64:
        return 0
    magic, ver, lic, hdr_size, folder_len = struct.unpack_from("<IHHII", raw_file, 0)
    if magic != 0x9E2A83C1:
        return 0
    off = 16 + folder_len
    pkg_flags, name_cnt, name_off, exp_cnt, exp_off, imp_cnt, imp_off, dep_off = struct.unpack_from("<IIIIIIII", raw_file, off)
    gen_cnt = struct.unpack_from("<I", raw_file, off + 64)[0]
    c_off = off + 68 + gen_cnt * 12 + 8
    comp_flags, chunk_cnt = struct.unpack_from("<II", raw_file, c_off)
    if comp_flags == 0 or chunk_cnt == 0:
        return 0

    chunks = [struct.unpack_from("<IIII", raw_file, c_off + 8 + i * 16) for i in range(chunk_cnt)]
    sub_dir = []
    chunk_subs = []
    for ci_idx, (uo, us, co, cs) in enumerate(chunks):
        tag, blk_sz, c_tot, u_tot = struct.unpack_from("<IIII", raw_file, co)
        n_sub = (u_tot + blk_sz - 1) // blk_sz
        subs = [struct.unpack_from("<II", raw_file, co + 16 + j * 8) for j in range(n_sub)]
        cur_u = uo
        cur_c = co + 16 + n_sub * 8
        c_list = []
        for j, (scs, sus) in enumerate(subs):
            s_idx = len(sub_dir)
            sub_dir.append((cur_u, cur_u + sus, cur_c, scs, sus, ci_idx, j))
            c_list.append((s_idx, cur_u, cur_u + sus, cur_c, scs, sus))
            cur_u += sus
            cur_c += scs
        chunk_subs.append((tag, blk_sz, c_list))

    cache: Dict[int, bytearray] = {}
    dirty_subs = set()

    def get_sub(s_idx: int) -> bytearray:
        if s_idx not in cache:
            su0, su1, cpos, scs, sus, _, _ = sub_dir[s_idx]
            sb = raw_file[cpos:cpos + scs]
            if scs == sus:
                cache[s_idx] = bytearray(sb)
            elif comp_flags == 1:
                cache[s_idx] = bytearray(zlib.decompress(sb))
            else:
                cache[s_idx] = bytearray(lzo1x_decompress(sb))
        return cache[s_idx]

    def read_uncomp(u_start: int, u_len: int) -> bytes:
        u_end = u_start + u_len
        out = bytearray()
        out_s = None
        for s_idx, (su0, su1, _, _, _, _, _) in enumerate(sub_dir):
            if su1 <= u_start or su0 >= u_end:
                continue
            if out_s is None:
                out_s = su0
            out.extend(get_sub(s_idx))
        rel = u_start - (out_s or 0)
        return bytes(out[rel:rel + u_len])

    def write_uncomp(u_start: int, data_bytes: bytes) -> None:
        u_end = u_start + len(data_bytes)
        for s_idx, (su0, su1, _, _, _, _, _) in enumerate(sub_dir):
            if su1 <= u_start or su0 >= u_end:
                continue
            sbuf = get_sub(s_idx)
            dirty_subs.add(s_idx)
            ov_s = max(u_start, su0)
            ov_e = min(u_end, su1)
            sbuf[ov_s - su0:ov_e - su0] = data_bytes[ov_s - u_start:ov_e - u_start]

    tables_buf = read_uncomp(name_off, exp_off + exp_cnt * 72 - name_off)
    pos = 0
    names: List[str] = []
    for _ in range(name_cnt):
        slen = struct.unpack_from("<i", tables_buf, pos)[0]
        pos += 4
        if slen > 0:
            s = tables_buf[pos:pos + slen - 1].decode("latin1", errors="replace")
            pos += slen + 8
        elif slen < 0:
            s = tables_buf[pos:pos + (-slen) * 2 - 2].decode("utf-16-le", errors="replace")
            pos += (-slen) * 2 + 8
        else:
            s = ""
            pos += 8
        names.append(s)

    if "AkDialogueEvent" not in names or "DialogueText" not in names:
        return 0
    dt_idx = names.index("DialogueText")

    imp_rel = imp_off - name_off
    imports = [struct.unpack_from("<qiiiii", tables_buf, imp_rel + i * 28)[4] for i in range(imp_cnt)]
    exp_rel = exp_off - name_off
    exports = []
    for i in range(exp_cnt):
        l = struct.unpack_from("<12i", tables_buf, exp_rel + i * 72)
        exports.append({
            "name": l[3],
            "num": l[4] - 1,
            "class": l[0],
            "group": l[2] - 1,
            "size": l[9],
            "offset": l[10],
            "entry_uoff": exp_off + i * 72,
        })

    patched_cues = 0

    for exp in exports:
        c = exp["class"]
        s_class = names[imports[-c - 1]] if c < 0 else (names[exports[c - 1]["name"]] if c > 0 else "Class")
        if s_class != "AkDialogueEvent" or exp["size"] <= 24:
            continue
        enum = exp["num"]
        s_name = names[exp["name"]] + (f"_{enum}" if enum >= 0 else "")
        grp = []
        ci = exp["group"]
        while ci >= 0:
            ge = exports[ci]
            gnum = ge["num"]
            gn = names[ge["name"]] + (f"_{gnum}" if gnum >= 0 else "")
            grp.append(gn)
            ci = ge["group"]
        grp.reverse()
        grp_str = ".".join(grp).lower()
        ev_key = f"{grp_str}|{s_name.lower()}"

        ebuf = bytearray(read_uncomp(exp["offset"], exp["size"]))
        p_pos = 0
        modified = False
        while p_pos + 4 <= len(ebuf):
            tdata, tname = struct.unpack_from("<HH", ebuf, p_pos)
            if tdata == 0:
                break
            elif tdata == 1:
                if p_pos + 20 > len(ebuf):
                    break
                tpname, dlen = struct.unpack_from("<qq", ebuf, p_pos + 4)
                p_pos += 20 + (1 if dlen == 0 else dlen)
            elif (2 <= tdata <= 4) or tdata == 16:
                p_pos += 8
            elif tdata == 9:
                if p_pos + 20 > len(ebuf):
                    break
                tpname, old_dlen = struct.unpack_from("<qq", ebuf, p_pos + 4)
                if tpname == dt_idx and p_pos + 20 + old_dlen <= len(ebuf) and old_dlen >= 8:
                    num, slen = struct.unpack_from("<ii", ebuf, p_pos + 20)
                    zh = ak_events_zh.get(ev_key)
                    if not zh:
                        en_s = ""
                        if slen > 0 and p_pos + 28 + slen <= len(ebuf):
                            en_s = ebuf[p_pos + 28:p_pos + 28 + slen - 1].decode("latin1", errors="replace")
                        elif slen < 0 and p_pos + 28 + (-slen) * 2 <= len(ebuf):
                            en_s = ebuf[p_pos + 28:p_pos + 28 + (-slen) * 2 - 2].decode("utf-16-le", errors="replace")
                        zh = ak_en_zh.get(en_s)
                    if zh:
                        new_fstr = encode_fstring_utf16(zh)
                        t_data = struct.pack("<i", num) + new_fstr + (b"\x00\x00\x00\x00" * (num - 1))
                        if len(t_data) <= old_dlen:
                            ebuf[p_pos + 12:p_pos + 20 + old_dlen] = struct.pack("<q", len(t_data)) + t_data
                            modified = True
                            patched_cues += 1
                    break
                p_pos += 20 + old_dlen
            elif tdata == 13:
                if p_pos + 8 > len(ebuf):
                    break
                dlen = struct.unpack_from("<i", ebuf, p_pos + 4)[0]
                p_pos += 8 + (dlen if dlen >= 0 else (-dlen) * 2)
            else:
                break

        if modified:
            new_sz = len(ebuf)
            write_uncomp(exp["offset"], bytes(ebuf))
            write_uncomp(exp["entry_uoff"] + 36, struct.pack("<ii", new_sz, exp["offset"]))

    if not patched_cues:
        return 0

    def compress_sub(piece: bytes) -> bytes:
        if comp_flags == 1:
            return zlib.compress(piece)
        return lzo1x_compress_fast(piece)

    header_end = chunks[0][2]
    out_upk = bytearray(raw_file[:header_end])
    new_chunks_meta = []

    for ci_idx, (uo, us, co, cs) in enumerate(chunks):
        new_co = len(out_upk)
        tag, blk_sz, c_list = chunk_subs[ci_idx]
        if any(s_idx in dirty_subs for s_idx, _, _, _, _, _ in c_list):
            sub_hdrs = bytearray()
            sub_payloads = bytearray()
            c_tot = 0
            for s_idx, su0, su1, cpos, scs, sus in c_list:
                if s_idx in dirty_subs:
                    comp_b = compress_sub(bytes(get_sub(s_idx)))
                else:
                    comp_b = raw_file[cpos:cpos + scs]
                sub_hdrs.extend(struct.pack("<II", len(comp_b), sus))
                sub_payloads.extend(comp_b)
                c_tot += len(comp_b)
            chunk_bytes = struct.pack("<IIII", tag, blk_sz, c_tot, us) + sub_hdrs + sub_payloads
            out_upk.extend(chunk_bytes)
            new_chunks_meta.append((uo, us, new_co, len(chunk_bytes)))
        else:
            out_upk.extend(raw_file[co:co + cs])
            new_chunks_meta.append((uo, us, new_co, cs))

    for ci_idx, (uo, us, co, cs) in enumerate(new_chunks_meta):
        struct.pack_into("<IIII", out_upk, c_off + 8 + ci_idx * 16, uo, us, co, cs)

    dst_p.parent.mkdir(parents=True, exist_ok=True)
    dst_p.write_bytes(bytes(out_upk))
    return patched_cues


def repack_knight_upks():
    cfg = json.loads((ROOT_DIR / "config" / "game_paths.json").read_text(encoding="utf-8"))
    knight_root = Path(cfg["knight"]["root_dir"])
    bkp_root = knight_root / "_Backup_TW_Mod"
    work_dir = ROOT_DIR / "workspace" / "knight"
    dist_root = ROOT_DIR / "dist" / "Arkham_Knight_TW_Mod"

    ak_events_zh_p = work_dir / "ak_events_zh.json"
    upk_list_p = work_dir / "metadata" / "upk_list.json"

    ak_events_zh = json.loads(ak_events_zh_p.read_text(encoding="utf-8")) if ak_events_zh_p.exists() else {}
    ak_en_zh = load_aux_lookup(work_dir, "ak_en_zh.json", "ak_en_sha256_zh.json") or {}
    upk_set = {u.lower() for u in json.loads(upk_list_p.read_text(encoding="utf-8"))} if upk_list_p.exists() else set()

    gamedirs = [knight_root / "BMGame" / "CookedPCConsole"]
    dlc_root = knight_root / "DLC"
    if dlc_root.is_dir():
        for d in sorted(dlc_root.iterdir()):
            p = d / "Content" / "BmGame" / "CookedPCConsole"
            if p.is_dir():
                gamedirs.append(p)

    candidate_files: List[Path] = []
    for gd in gamedirs:
        if not gd.is_dir():
            continue
        for p in sorted(gd.iterdir()):
            if p.is_file() and p.name.lower() in upk_set:
                candidate_files.append(p)

    files_done = total_cues = 0
    for idx_f, p in enumerate(candidate_files):
        rel = p.relative_to(knight_root)
        src_p = bkp_root / rel if (bkp_root / rel).exists() else p
        dst_p = dist_root / rel
        cues = patch_knight_compressed_upk(src_p, dst_p, ak_events_zh, ak_en_zh)
        if cues > 0:
            files_done += 1
            total_cues += cues
        if (idx_f + 1) % 200 == 0:
            print(f"  [knight] Processed {idx_f + 1}/{len(candidate_files)} UPKs ({files_done} patched, {total_cues} cues)...", flush=True)

    print(f"[knight] Repack UPK done: {files_done} packages, {total_cues} cues patched.", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Repack UE3 UPK subtitles for all Arkham games")
    parser.add_argument("game", choices=["asylum", "city", "origins", "knight", "all"])
    args = parser.parse_args()

    targets = ["asylum", "city", "origins", "knight"] if args.game == "all" else [args.game]
    for g in targets:
        if g == "asylum":
            repack_asylum_upks()
        elif g == "city":
            repack_city_upks()
        elif g == "origins":
            repack_origins_upks()
        elif g == "knight":
            repack_knight_upks()
