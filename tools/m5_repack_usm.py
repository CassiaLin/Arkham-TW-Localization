"""
Module 5D (M5-Repacker-USM): Reinjects translated UTF-8 subtitles into
CRI Sofdec2 .usm movie @SBT chunks for Batman: Arkham City GOTY.
"""
import json
import struct
import sys
from pathlib import Path
from typing import Dict

sys.path.insert(0, str(Path(__file__).resolve().parent))
from translation_pack import load_aux_lookup

ROOT_DIR = Path(__file__).resolve().parent.parent


def patch_usm_file(src_usm: Path, dst_usm: Path, zh_map: Dict[str, str]) -> int:
    data = src_usm.read_bytes()
    out = bytearray()
    pos = 0
    patched = 0
    n = len(data)

    while pos + 8 <= n:
        magic = data[pos:pos + 4]
        sz = struct.unpack_from(">I", data, pos + 4)[0]
        if sz == 0 or pos + 8 + sz > n:
            out.extend(data[pos:])
            break

        chunk = data[pos:pos + 8 + sz]
        pos += 8 + sz

        if magic == b"@SBT" and len(chunk) >= 52 and chunk[15] == 0:
            lang_id = struct.unpack_from("<I", chunk, 32)[0]
            if lang_id == 0:
                tlen = struct.unpack_from("<I", chunk, 48)[0]
                if 0 < tlen <= len(chunk) - 52:
                    raw_t = chunk[52:52 + tlen].rstrip(b"\x00").decode("utf-8", errors="replace")
                    new_str = None
                    if "|" in raw_t:
                        dur_prefix, en_text = raw_t.split("|", 1)
                        if en_text in zh_map:
                            new_str = f"{dur_prefix}|{zh_map[en_text]}".encode("utf-8") + b"\x00"
                    elif raw_t in zh_map:
                        new_str = zh_map[raw_t].encode("utf-8") + b"\x00"

                    if new_str is not None:
                        new_tlen = len(new_str)
                        total_len = ((52 + new_tlen) + 31) & ~31
                        new_sz = total_len - 8
                        new_pad = total_len - (52 + new_tlen)

                        new_chunk = bytearray(chunk[:52])
                        struct.pack_into(">I", new_chunk, 4, new_sz)
                        struct.pack_into(">H", new_chunk, 10, new_pad)
                        struct.pack_into("<I", new_chunk, 48, new_tlen)
                        new_chunk.extend(new_str)
                        new_chunk.extend(b"\x00" * new_pad)
                        out.extend(new_chunk)
                        patched += 1
                        continue

        out.extend(chunk)

    if patched > 0:
        dst_usm.parent.mkdir(parents=True, exist_ok=True)
        dst_usm.write_bytes(bytes(out))
    return patched


def repack_city_usms() -> None:
    cfg = json.loads((ROOT_DIR / "config" / "game_paths.json").read_text(encoding="utf-8"))
    city_root = Path(cfg["city"]["root_dir"])
    zh_map = load_aux_lookup(ROOT_DIR / "workspace" / "city", "usm_zh.json", "usm_sha256_zh.json")
    if not zh_map:
        print("[city] usm_zh.json / usm_sha256_zh.json not found, skipping USM repack.")
        return
    dist_root = ROOT_DIR / "dist" / "Arkham_City_TW_Mod"

    files_done = 0
    total_lines = 0
    for sub in ("BmGame/Movies", "BmGame/MoviesStereo"):
        src_dir = city_root / sub
        if not src_dir.exists():
            continue
        for p in sorted(src_dir.glob("*.usm")):
            rel = f"{sub}/{p.name}"
            bkp = city_root / "_Backup_TW_Mod" / rel
            src_usm = bkp if bkp.exists() and bkp.stat().st_size > 16 else p
            dst_usm = dist_root / rel
            cnt = patch_usm_file(src_usm, dst_usm, zh_map)
            if cnt > 0:
                files_done += 1
                total_lines += cnt

    print(f"[city] Repack USM done: {files_done} movies, {total_lines} @SBT subtitles patched.")


def repack_origins_usms() -> None:
    cfg = json.loads((ROOT_DIR / "config" / "game_paths.json").read_text(encoding="utf-8"))
    origins_root = Path(cfg["origins"]["root_dir"])
    bkp_root = origins_root / "_Backup_TW_Mod"
    zh_map = load_aux_lookup(ROOT_DIR / "workspace" / "origins", "usm_zh.json", "usm_sha256_zh.json")
    if not zh_map:
        print("[origins] usm_zh.json / usm_sha256_zh.json not found, skipping USM repack.")
        return
    dist_root = ROOT_DIR / "dist" / "Arkham_Origins_TW_Mod"

    sp_dir = origins_root / "SinglePlayer"
    usm_files = [p for p in sorted(sp_dir.rglob("*.usm")) if "_Backup_TW_Mod" not in p.parts]
    files_done = 0
    total_lines = 0
    for p in usm_files:
        rel = p.relative_to(origins_root).as_posix()
        bkp = bkp_root / rel
        src_usm = bkp if bkp.exists() and bkp.stat().st_size > 16 else p
        dst_usm = dist_root / rel
        cnt = patch_usm_file(src_usm, dst_usm, zh_map)
        if cnt > 0:
            files_done += 1
            total_lines += cnt

    print(f"[origins] Repack USM done: {files_done} movies, {total_lines} @SBT subtitles patched.")


def repack_knight_usms() -> None:
    cfg = json.loads((ROOT_DIR / "config" / "game_paths.json").read_text(encoding="utf-8"))
    knight_root = Path(cfg["knight"]["root_dir"])
    bkp_root = knight_root / "_Backup_TW_Mod"
    zh_map = load_aux_lookup(ROOT_DIR / "workspace" / "knight", "usm_zh.json", "usm_sha256_zh.json")
    if not zh_map:
        print("[knight] usm_zh.json / usm_sha256_zh.json not found, skipping USM repack.")
        return
    dist_root = ROOT_DIR / "dist" / "Arkham_Knight_TW_Mod"

    movie_rels = [
        "BmGame/Movies/CH10_Press_Gathers.usm",
        "BmGame/Movies/Ending.usm",
        "BmGame/Movies/Intro.usm",
        "BmGame/Movies/Intro_Part_2.usm",
        "DLC/313102/Content/BmGame/Movies/DLC45_Batgirl_Intro.usm",
        "DLC/313102/Content/BmGame/Movies/DLC45_Batgirl_Outro.usm",
    ]
    files_done = 0
    total_lines = 0
    for rel in movie_rels:
        bkp = bkp_root / rel
        src_usm = bkp if bkp.exists() and bkp.stat().st_size > 16 else (knight_root / rel)
        if not src_usm.exists():
            continue
        dst_usm = dist_root / rel
        cnt = patch_usm_file(src_usm, dst_usm, zh_map)
        if cnt > 0:
            files_done += 1
            total_lines += cnt

    print(f"[knight] Repack USM done: {files_done} movies, {total_lines} @SBT subtitles patched.")


if __name__ == "__main__":
    repack_city_usms()
    repack_origins_usms()
    repack_knight_usms()
