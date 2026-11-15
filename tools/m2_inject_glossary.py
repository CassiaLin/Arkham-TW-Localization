"""
Module 2 (M2-Glossary Injector): Dynamically scans a given batch_XXX.input.jsonl
and extracts ONLY the glossary terms and speaker voice guidelines that appear in that batch,
saving 80%+ prompt tokens for collaborating AI translators.
"""
import re
import sys
import json
from pathlib import Path
from typing import Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
GLOSSARY_PATH = ROOT_DIR / "glossary" / "dc_tw_glossary.json"

SPEAKER_HINTS = {
    "Batman": "蝙蝠俠：冷靜、簡短果斷、威嚴。",
    "Joker": "小丑：瘋狂戲謔、黑色幽默，稱呼蝙蝠俠為「小蝙(Bats)」，夏普為「老夏(Sharpie)」。",
    "Harley": "哈莉·奎茵：活潑俏皮帶狠勁，稱小丑「J先生(Mistah J)/布丁(Puddin')」，稱蝙蝠俠「蝙蝠兄(B-man)」。",
    "Oracle": "神諭：專業俐落的情報支援語氣。",
    "Gordon": "高登局長：沉穩正派的老警官。",
    "Riddler": "謎語人：極度自負高傲、愛用雙關與押韻機智問答。",
    "Amadeus": "阿卡漢之魂：古典深沉、歌德悲劇獨白語氣。",
    "Scarecrow": "稻草人：陰森冰冷、剖析恐懼。",
    "PoisonIvy": "毒藤女：魅惑高傲、視植物為孩子。",
    "KillerCroc": "殺手鱷：粗暴野獸般、嗜血簡短。",
    "Zsasz": "薩斯：病態偏執的連環殺手。",
    "Bane": "班恩：狂暴強悍的戰士口吻。",
    "WardenSharp": "夏普院長：官腔威權、色厲內荏。",
    "DrYoung": "楊格博士：焦急後悔的醫療學者。",
    "AaronCash": "亞倫·凱許：堅毅果敢的警衛隊長。",
    "FrankBoles": "法蘭克·波爾斯：粗俗貪婪的叛徒警衛。",
    "Thug": "黑門暴徒：道地台灣黑幫/街頭混混口語（自然接地氣，如：蝙蝠仔、條子、扁他）。",
    "Lunatic": "瘋人院狂徒：胡言亂語、歇斯底里。",
    "Guard": "瘋人院警衛：緊張或敬業的獄警口吻。",
}


def build_batch_prompt_header(batch_path: Path) -> str:
    glossary_data = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    flat_terms: Dict[str, str] = {}
    for group in glossary_data.values():
        flat_terms.update(group)

    lines = [
        json.loads(line)
        for line in batch_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    combined_en = "\n".join(item.get("en", "") + " " + item.get("ctx", "") for item in lines)
    speakers = sorted({item.get("sp", "") for item in lines if item.get("sp")})

    matched_terms: Dict[str, str] = {}
    # Sort longer terms first
    for en_term, zh_term in sorted(flat_terms.items(), key=lambda x: -len(x[0])):
        pattern = re.compile(r"\b" + re.escape(en_term) + r"\b", re.IGNORECASE)
        if pattern.search(combined_en):
            matched_terms[en_term] = zh_term

    out = [
        "【任務說明】請將以下 JSONL 翻譯為道地「台灣繁體中文」。",
        "【輸出契約】每行僅輸出 `{\"id\":\"...\",\"zh\":\"...\"}`，嚴禁遺漏或修改 `{0}`, `{1}` 等控制碼佔位符。",
    ]
    if matched_terms:
        terms_str = " | ".join(f"{k} -> {v}" for k, v in matched_terms.items())
        out.append(f"【本批次命中專有名詞 ({len(matched_terms)} 項)】{terms_str}")
    if speakers:
        sp_str = " ； ".join(SPEAKER_HINTS.get(s, s) for s in speakers)
        out.append(f"【本批次說話者語氣】{sp_str}")

    return "\n".join(out)


if __name__ == "__main__":
    sample = ROOT_DIR / "workspace" / "asylum" / "batches" / "02_story_dialogue" / "batch_001.input.jsonl"
    if len(sys.argv) > 1:
        sample = Path(sys.argv[1])
    print(build_batch_prompt_header(sample))
