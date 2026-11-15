"""
Module 1 (M1-Extractor): Extract and categorize all translatable text from
Batman: Arkham Asylum GOTY (.int files + *_LOC_INT.upk SoundNodeWave packages)
into token-efficient Two-Tier JSONL batches + local id_map.json.
"""
import os
import re
import sys
import json
import glob
import shutil
from pathlib import Path
from typing import Dict, List, Tuple, Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from upk_core import UPKPackage
from game_detector import detect_all_games
from source_resolver import resolve_game_source

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config" / "game_paths.json"
WORK_DIR = ROOT_DIR / "workspace" / "asylum"

BATCH_SIZE = 100

# Hybrid Mode (方案 C): Built-in Riddle & Easter Egg Context Hints for Riddler.int
RIDDLE_CONTEXT_MAP = {
    ("Admin", "1"): "謎底：大廳牆上的高譚市長/院長肖像畫 (Main man in the main hall)",
    ("Admin", "2"): "謎底：雨果·史崔奇 (Hugo Strange) 的調職申請文件（雙關 Strange 奇怪/史崔奇姓氏）",
    ("Admin", "3"): "謎底：停屍間屍體腳趾上的「雷肖古 (Ra's al Ghul)」名牌（雙關 Ghul 惡魔與人物名）",
    ("Admin", "4"): "謎底：企鵝人 (Penguin / Cobblepot) 的雨傘展示櫃（雙關 bird 企鵝與雨傘）",
    ("Admin", "5"): "謎底：貓女 (Catwoman) 的貓爪手套與皮鞭展示櫃（雙關 Cat and mouse 貓捉老鼠）",
    ("Admin", "6"): "謎底：腹語師的木偶「疤面 (Scarface)」展示櫃（puppet show 木偶戲）",
    ("Admin", "7"): "謎底：圓形牢房牆上阿瑪迪斯·阿卡漢 (Amadeus Arkham) 留下的螺旋塗鴉",
    ("Admin", "8"): "謎底：利用偵探模式視角對齊牆上與柱子上的問號塗鴉 (問號憑空出現)",
    ("Admin", "9"): "謎底：黑面具 (Black Mask / Roman Sionis) 的骷髏面具掛畫（雙關 mask 掩飾/面具）",
    ("Cave", "1"): "謎底：蝙蝠洞區域需要對齊視角才能組合出的隱藏問號",
    ("Cell", "1"): "謎底：腹語師 (The Ventriloquist) 的湯米衝鋒槍展示品（兩個人一個聲音＝腹語師與木偶）",
    ("Cell", "2"): "謎底：急凍人 (Mr. Freeze) 結冰的特殊牢房（雙關 break the ice 破冰/打破沉默）",
    ("Cell", "3"): "謎底：反派「普羅米修斯 (Prometheus)」的通緝海報",
    ("Cell", "4"): "謎底：泥面人 (Clayface) 偽裝成警衛/院長站在玻璃牢房內（雙關 mistaken identity 認錯身分）",
    ("Cell", "5"): "謎底：雙面人 (Two-Face / 前檢察官哈維·丹特 ex-DA) 的競選海報（雙關 two sides 兩面）",
    ("Cell", "6"): "謎底：日曆人 (Calendar Man) 貼滿日期的牢房（雙關 end of days 日子盡頭）",
    ("Cell", "7"): "謎底：看著鏡子裡的蝙蝠俠自己（雙關 reflect 反思/鏡面反射）",
    ("Cell", "8"): "謎底：開偵探模式才看得到的隱藏問號塗鴉",
    ("Garden", "1"): "謎底：瘋帽客 (Mad Hatter) 的下午茶派對茶具佈景（仙境 Wonderland 的喝茶時間）",
    ("Garden", "2"): "謎底：植物園裡失蹤警衛留下的制服與鞋子",
    ("Garden", "3"): "謎底：花園長椅上向開發團隊致敬的名牌",
    ("Garden", "4"): "謎底：玩具人/矮胖子 (Humpty Dumpty) 弄壞並重新拼湊的玩具兵",
    ("Garden", "5"): "謎底：暴牙雙胞胎 (Tweedledum and Tweedledee) 的翹翹板與帽子",
    ("Garden", "6"): "謎底：植物園天花板與結構對齊的問號謎題",
    ("Max", "1"): "謎底：宙斯 (Maxie Zeus) 電氣治療室的希臘風格牢房",
    ("Max", "2"): "謎底：殺手蛾 (Killer Moth) 被釘在牆上的骷髏與飛蛾標本",
    ("Max", "3"): "謎底：捕鼠人 (Ratcatcher) 藏在通風口的防毒面具與老鼠手套",
    ("Max", "4"): "謎底：薩斯 (Victor Zsasz) 將遇害警衛擺拍成打撲克牌的恐怖現場",
    ("Max", "5"): "謎底：大白鯊 (Great White Shark / Warren White) 裝在罐子裡的斷指與鼻子",
    ("Max", "6"): "謎底：重症監護室區域的視角對齊問號",
    ("Medical", "1"): "謎底：緘默 (Hush / Tommy Elliot) 寫在白板上的排班表名字",
    ("Medical", "2"): "謎底：小丑女哈莉或瘋帽客相關的醫療室擺設（雙關 hat/coat）",
    ("Medical", "3"): "謎底：醫療大樓牆上貼著韋恩夫婦/布魯斯韋恩的新聞剪報",
    ("Medical", "4"): "謎底：毒藤女或稻草人實驗室內的特殊標本",
    ("Medical", "5"): "謎底：殺手鱷 (Killer Croc) 巨大的頸圈或醫療紀錄",
    ("Medical", "6"): "謎底：院長夏普 (Quincy Sharp) 的畫像（雙關 Sharp 鋒利與姓氏）",
    ("Medical", "7"): "謎底：泰坦藥劑 (Titan) 改造實驗的骨骼 X 光片",
    ("Medical", "8"): "謎底：醫療中心內的視角對齊問號",
    ("Overworld", "1"): "謎底：遠眺高譚市天際線閃爍的韋恩企業大樓 (Wayne Enterprises)",
    ("Overworld", "2"): "謎底：火螢人 (Firefly) 的噴火背包與新聞剪報",
    ("Overworld", "3"): "謎底：阿卡漢島戶外墓園或雕像彩蛋",
}

TAG_REGEX = re.compile(r"(<[^>]+>|%[sdif]|\{[0-9]+\}|\\[nt])")

SKIP_INT_FILES = {"GFxFonts.int", "Version.int", "VersionData.int"}
PLACEHOLDER_VALUES = {"speech", "test", "stuff", "wavs", "placeholder", "placeholder txt."}


def extract_tags(raw_str: str) -> Tuple[str, List[str]]:
    tags: List[str] = []
    def repl(m):
        idx = len(tags)
        tags.append(m.group(0))
        return f"{{{idx}}}"
    clean_str = TAG_REGEX.sub(repl, raw_str)
    return clean_str, tags


def infer_speaker_from_token(token: str) -> str:
    t = token.upper()
    if "BATMAN" in t or "_BM_" in t or t.startswith("BM_"):
        return "Batman"
    if "JOKER" in t or "_J_" in t or t.startswith("J_"):
        return "Joker"
    if "HARLEY" in t or "_HQ_" in t or "QUINN" in t:
        return "Harley"
    if "ORACLE" in t or "_OR_" in t:
        return "Oracle"
    if "GORDON" in t or "_JG_" in t or "_G_" in t:
        return "Gordon"
    if "RIDDLER" in t or "_RID_" in t:
        return "Riddler"
    if "AMADEUS" in t or "SPIRIT" in t or "CYPHER" in t:
        return "Amadeus"
    if "SCARE" in t or "_SC_" in t:
        return "Scarecrow"
    if "IVY" in t or "POISON" in t:
        return "PoisonIvy"
    if "CROC" in t:
        return "KillerCroc"
    if "ZSASZ" in t or "_Z_" in t:
        return "Zsasz"
    if "BANE" in t:
        return "Bane"
    if "WARDEN" in t or "SHARP" in t:
        return "WardenSharp"
    if "YOUNG" in t or "_PY_" in t:
        return "DrYoung"
    if "CASH" in t or "_AC_" in t:
        return "AaronCash"
    if "BOLES" in t or "_FB_" in t:
        return "FrankBoles"
    if "THUG" in t or "HENCH" in t or "_HM_" in t or "ENEMY" in t:
        return "Thug"
    if "LUNATIC" in t:
        return "Lunatic"
    if "GUARD" in t or "ORDERLY" in t or "_O_" in t or "_ZF_" in t:
        return "Guard"
    return ""


def categorize_int_entry(fname: str, section: str, key: str) -> str:
    fl = fname.lower()
    sec_u = section.upper()
    if fl in ("riddler.int", "amadeus.int"):
        return "05_riddles_special"
    if fl in ("bio.int", "rewards.int"):
        return "04_lore_tapes"
    if fl == "enemy_l1.int":
        return "03_thug_barks"
    if fl in ("cutscene.int", "scarecrow_taunts.int", "ivy.int", "garden_b5.int", "cave_b5.int"):
        return "02_story_dialogue"
    if fl in (
        "achievements.int", "credits.int", "demo.int", "dlc0.int", "dlc1.int",
        "dlc2.int", "dlc3.int", "dlc6.int", "forensics.int", "frontend.int",
        "gfxui.int", "investigation.int", "objectives.int", "pc.int",
        "presence.int", "signs.int", "tutorial.int", "xbox.int", "xlast_dlc.int"
    ):
        return "01_ui_system"
    # Map-specific .int files (Admin_B1.int, Cave_B1.int, Max_*.int, Medical_*.int, Overworld_*.int)
    if "TUT" in sec_u or "HELP" in sec_u or "REMINDER" in sec_u:
        return "01_ui_system"
    return "02_story_dialogue"


def categorize_upk_wave(upk_name: str, outer_name: str, exp_name: str) -> str:
    comb = f"{upk_name} {outer_name} {exp_name}".upper()
    if "RIDDLER" in comb or "AMADEUS" in comb or "SPIRIT_OF_ARKHAM" in comb:
        return "05_riddles_special"
    if "TAPE_" in comb:
        return "04_lore_tapes"
    if (
        "SPEECH-ENEMY" in comb or "_HM_" in comb or "_THUG" in comb
        or "_HENCH" in comb or "LUNATIC" in comb or "ENEMY_" in comb
        or "CHATTER" in comb
    ):
        return "03_thug_barks"
    return "02_story_dialogue"


def should_translate_int_value(fname: str, section: str, key: str, val: str) -> bool:
    v = val.strip()
    if not v:
        return False
    if v.lower() in PLACEHOLDER_VALUES:
        return False
    # Skip pure numbers or coordinates
    if re.fullmatch(r"[-+]?[0-9.,%/:xX\s]+", v):
        return False
    # Skip Cutscene.int [BIG_WAVS] unsplit concatenations if individual [CHx_xxx] lines exist?
    # Wait: keep [BIG_WAVS] if non-placeholder just in case, or translate it too.
    # For Credits.int, only translate titles/headers (key=="Title" or key=="Job"), skip staff personal names!
    if fname.lower() == "credits.int":
        if key.lower() not in ("title", "job", "heading", "role") and not any(
            w in v.lower() for w in (
                "director", "producer", "designer", "programmer", "artist",
                "lead", "manager", "special thanks", "voice", "music",
                "sound", "writer", "animation", "quality", "tester", "president",
                "cast", "starring", "created", "published", "developed", "copyright"
            )
        ):
            return False
    return True


def run_extraction(allow_legacy_backup: bool = False):
    if CONFIG_PATH.is_file():
        games = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    else:
        games = detect_all_games(CONFIG_PATH)

    if "asylum" not in games:
        raise RuntimeError("Batman: Arkham Asylum GOTY not found!")

    asylum_cfg = games["asylum"]
    resolver = resolve_game_source("asylum", Path(asylum_cfg["root_dir"]), allow_legacy_backup=allow_legacy_backup)

    # Prepare workspace dirs
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
        "01_ui_system": "UI",
        "02_story_dialogue": "ST",
        "03_thug_barks": "TH",
        "04_lore_tapes": "LR",
        "05_riddles_special": "RD",
    }

    # Deduplication registry per category: (category, clean_en) -> entry_id
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
        prefix = cat_prefixes[cat]
        eid = f"{prefix}_{cat_counters[cat]:04d}"
        dedup_map[dedup_key] = eid

        entry_meta = {
            "id": eid,
            "category": cat,
            "sp": speaker,
            "en_raw": raw_en,
            "en_clean": clean_en,
            "tags": tags,
            "ctx": ctx,
            "sources": [source_ref],
        }
        id_map[eid] = entry_meta

        jsonl_obj: Dict[str, Any] = {"id": eid}
        if speaker:
            jsonl_obj["sp"] = speaker
        jsonl_obj["en"] = clean_en
        if ctx:
            jsonl_obj["ctx"] = ctx
        cat_items[cat].append(jsonl_obj)
        return eid

    # 1. Extract from all .int files (via verified OriginalSourceResolver)
    int_files = resolver.iter_dir_files("BmGame/Localization/INT", "*.int", recursive=False)
    int_source_count = 0
    for _rel_int, p in int_files:
        fname = p.name
        # Backup raw .int if not already backed up
        bkp_target = backup_dir / fname
        if not bkp_target.exists():
            shutil.copy2(p, bkp_target)

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
                k_str = k.strip()
                v_str = v.strip()
                if not should_translate_int_value(fname, section, k_str, v_str):
                    continue
                cat = categorize_int_entry(fname, section, k_str)
                sp = infer_speaker_from_token(f"{section}_{k_str}")
                ctx = ""
                if fname.lower() == "riddler.int":
                    sp = "Riddler"
                    ctx = RIDDLE_CONTEXT_MAP.get((section, k_str), "")
                elif fname.lower() == "amadeus.int":
                    sp = "Amadeus"
                    ctx = "阿卡漢精神病院創辦人 Amadeus Arkham 的歷史自白石碑，語氣古典深沉"
                elif fname.lower() == "bio.int":
                    ctx = f"人物傳記檔案：{section}"
                elif fname.lower() == "rewards.int":
                    ctx = f"精神病患訪談錄音帶：{section}"

                register_string(
                    cat=cat,
                    raw_en=v_str,
                    speaker=sp,
                    ctx=ctx,
                    source_ref={
                        "type": "int",
                        "file": fname,
                        "line_idx": line_idx,
                        "section": section,
                        "key": k_str,
                    }
                )
                int_source_count += 1

    # 2. Extract from all *_LOC_INT.upk files (via verified OriginalSourceResolver)
    loc_upks = resolver.iter_dir_files("BmGame/CookedPC", "*_LOC_INT.upk", recursive=True)
    upk_source_count = 0
    for rel_from_root, p in loc_upks:
        rel_path = Path(rel_from_root).relative_to("BmGame/CookedPC").as_posix()
        pkg = UPKPackage(str(p))
        for exp in pkg.exports:
            if exp["class_name"] == "SoundNodeWave" and exp["size"] > 20:
                outer = pkg.exports[exp["outer_idx"] - 1]["name"] if exp["outer_idx"] > 0 else ""
                props, _ = pkg.parse_properties(exp["offset"] + 4)
                for pr in props:
                    if pr["name"] == "Subtitles":
                        for cue_idx, cue in enumerate(pr["value"]):
                            for f in cue:
                                if f["name"] == "Text" and f["value"].strip():
                                    txt = f["value"].strip()
                                    if txt.lower() in PLACEHOLDER_VALUES:
                                        continue
                                    cat = categorize_upk_wave(p.name, outer, exp["name"])
                                    sp = infer_speaker_from_token(f"{outer}_{exp['name']}")
                                    ctx = ""
                                    if cat == "05_riddles_special":
                                        ctx = "語音字幕：謎語人挑釁或阿卡漢之魂獨白"
                                    register_string(
                                        cat=cat,
                                        raw_en=txt,
                                        speaker=sp,
                                        ctx=ctx,
                                        source_ref={
                                            "type": "upk",
                                            "rel_path": rel_path,
                                            "export_name": exp["name"],
                                            "export_num": exp["num"],
                                            "cue_idx": cue_idx,
                                        }
                                    )
                                    upk_source_count += 1

    # 3. Write id_map.json and batch_XXX.input.jsonl
    id_map_path = meta_dir / "id_map.json"
    id_map_path.write_text(json.dumps(id_map, indent=2, ensure_ascii=False), encoding="utf-8")

    stats = {
        "total_int_files_scanned": len(int_files),
        "total_upk_files_scanned": len(loc_upks),
        "raw_int_occurrences": int_source_count,
        "raw_upk_occurrences": upk_source_count,
        "total_raw_occurrences": int_source_count + upk_source_count,
        "unique_translatable_entries": len(id_map),
        "categories": {}
    }

    for cat in categories:
        cat_dir = batches_dir / cat
        cat_dir.mkdir(parents=True, exist_ok=True)
        # Clear old batch files
        for old_f in cat_dir.glob("*.jsonl"):
            old_f.unlink()

        items = cat_items[cat]
        num_batches = (len(items) + BATCH_SIZE - 1) // BATCH_SIZE
        for b_idx in range(num_batches):
            chunk = items[b_idx * BATCH_SIZE : (b_idx + 1) * BATCH_SIZE]
            b_file = cat_dir / f"batch_{b_idx + 1:03d}.input.jsonl"
            lines = [json.dumps(obj, ensure_ascii=False) for obj in chunk]
            b_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

        stats["categories"][cat] = {
            "unique_entries": len(items),
            "batches": num_batches,
            "total_english_words": sum(len(x["en"].split()) for x in items)
        }

    stats_path = meta_dir / "extraction_stats.json"
    stats_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    run_extraction()
