"""
Module 4 (M4-QA Linter): Zero-token automated quality assurance linter for translated JSONL batches.
Checks:
1. Exact ID alignment and count against input.jsonl
2. Control tag placeholder preservation ({0}, {1}, ...)
3. Forbidden Mainland China terms / Simplified Chinese character detection
4. Untranslated English check
"""
import re
import sys
import json
from pathlib import Path
from typing import Dict, List, Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
WORK_DIR = ROOT_DIR / "workspace" / "asylum"

CORPUS_INVENTORY = {
    "asylum": {"batches": 63, "entries": 6141},
    "city": {"batches": 135, "entries": 13167},
    "origins": {"batches": 409, "entries": 40651},
    "knight": {"batches": 104, "entries": 10201},
}

FORBIDDEN_TERMS = {
    "信息": "資訊/訊息",
    "視頻": "影片",
    "錄像": "錄影",
    "音頻": "音訊/錄音",
    "激光": "雷射",
    "激活": "啟動/觸發",
    "硬盤": "硬碟",
    "內存": "記憶體",
    "顯卡": "顯示卡",
    "默認": "預設",
    "鼠標": "滑鼠",
    "分辨率": "解析度",
    "幀率": "畫面更新率(FPS)",
    "哥譚": "高譚",
    "阿卡姆": "阿卡漢",
    "戈登": "高登",
    "阿爾弗雷德": "阿福",
    "謎語人": None,  # valid
}

# Common Simplified-only characters that should never appear in Traditional Chinese output
SIMPLIFIED_CHARS = set("们这来吗个们时后会现发经头动进着给点从还并没说对开实样种题尔与产")
# '里' is valid Traditional (公里, 鄰里, transliterated names like 費里斯), but in TW usage
# "inside" must be written '裡'. Flag only the common Simplified-style compounds.
SIMPLIFIED_LI_RE = re.compile(r"(這里|那里|哪里|心里|家里|城里|手里|里面|里頭|夜里|眼里|屋里|嘴里)")

PLACEHOLDER_RE = re.compile(r"\{[0-9]+\}")


def lint_batch(input_path: Path, output_path: Path) -> Dict[str, Any]:
    in_lines = [
        json.loads(l) for l in input_path.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    if not output_path.is_file():
        return {"status": "MISSING", "batch": input_path.name, "errors": ["Output file does not exist"]}

    out_lines = []
    parse_errors = []
    for idx, raw_l in enumerate(output_path.read_text(encoding="utf-8").splitlines()):
        if not raw_l.strip():
            continue
        try:
            out_lines.append(json.loads(raw_l))
        except json.JSONDecodeError as e:
            parse_errors.append(f"Line {idx+1}: Invalid JSON ({e})")

    if parse_errors:
        return {"status": "ERROR", "batch": output_path.name, "errors": parse_errors}

    in_map = {item["id"]: item for item in in_lines}
    out_map = {}
    dup_ids = []
    missing_id_rows = 0
    for item in out_lines:
        if "id" not in item:
            missing_id_rows += 1
            continue
        oid = item["id"]
        if oid in out_map:
            dup_ids.append(oid)
        out_map[oid] = item

    errors = []
    warnings = []

    if missing_id_rows:
        errors.append(f"Rows missing 'id' field: {missing_id_rows}")
    if dup_ids:
        errors.append(f"Duplicate IDs ({len(dup_ids)}): {dup_ids[:10]}")
    if len(out_lines) != len(in_lines):
        errors.append(f"Line count mismatch: input={len(in_lines)}, output={len(out_lines)}")

    missing_ids = [i for i in in_map if i not in out_map]
    extra_ids = [i for i in out_map if i not in in_map]
    if missing_ids:
        errors.append(f"Missing IDs ({len(missing_ids)}): {missing_ids[:10]}")
    if extra_ids:
        errors.append(f"Unexpected IDs ({len(extra_ids)}): {extra_ids[:10]}")

    for eid, in_item in in_map.items():
        if eid not in out_map:
            continue
        zh = out_map[eid].get("zh", "").strip()
        en = in_item.get("en", "").strip()

        if not zh:
            errors.append(f"[{eid}] Empty translation")
            continue

        # 1. Check control placeholders {0}, {1}
        in_tags = sorted(PLACEHOLDER_RE.findall(en))
        out_tags = sorted(PLACEHOLDER_RE.findall(zh))
        if in_tags != out_tags:
            errors.append(f"[{eid}] Placeholder mismatch: expected {in_tags}, got {out_tags} | zh={zh}")

        # 2. Check Simplified characters
        bad_chars = sorted(set(zh) & SIMPLIFIED_CHARS)
        if bad_chars:
            errors.append(f"[{eid}] Simplified Chinese chars detected {bad_chars}: {zh}")
        li_hits = SIMPLIFIED_LI_RE.findall(zh)
        if li_hits:
            errors.append(f"[{eid}] Use '裡' not '里' for 'inside' {li_hits}: {zh}")

        # 3. Check forbidden terms
        for bad_term, suggestion in FORBIDDEN_TERMS.items():
            if suggestion and bad_term in zh:
                warnings.append(f"[{eid}] Non-TW term '{bad_term}' (suggest '{suggestion}'): {zh}")

        # 4. Check untranslated multi-word English text
        if zh == en and len(en.split()) >= 5 and any("a" <= c.lower() <= "z" for c in en):
            warnings.append(f"[{eid}] Potentially untranslated English: {zh[:60]}")

    status = "PASS"
    if errors:
        status = "FAIL"
    elif warnings:
        status = "WARN"

    return {
        "status": status,
        "batch": output_path.name,
        "total": len(in_map),
        "translated": len(out_map),
        "error_count": len(errors),
        "warning_count": len(warnings),
        "errors": errors,
        "warnings": warnings,
    }


def run_qa(
    game: str,
    work_dir: Optional[Path] = None,
    expected_inventory: Optional[Dict[str, int]] = None,
    enforce_release_inventory: bool = True,
) -> int:
    wdir = work_dir or (ROOT_DIR / "workspace" / game)
    batches_root = wdir / "batches"
    trans_root = wdir / "translated"
    summary = {"PASS": 0, "WARN": 0, "FAIL": 0, "MISSING": 0, "ERROR": 0}
    total_warnings = 0

    in_files = sorted(batches_root.rglob("*.input.jsonl")) if batches_root.exists() else []
    out_files = sorted(trans_root.rglob("*.output.jsonl")) if trans_root.exists() else []

    expected_out_rels = {
        f.relative_to(batches_root).with_name(f.name.replace(".input.jsonl", ".output.jsonl"))
        for f in in_files
    }
    orphan_outs = [
        f.relative_to(trans_root).as_posix()
        for f in out_files
        if f.relative_to(trans_root) not in expected_out_rels
    ]

    all_in_ids: List[str] = []
    for in_f in in_files:
        for raw_l in in_f.read_text(encoding="utf-8").splitlines():
            if raw_l.strip():
                all_in_ids.append(json.loads(raw_l)["id"])
        rel = in_f.relative_to(batches_root)
        out_name = in_f.name.replace(".input.jsonl", ".output.jsonl")
        out_f = trans_root / rel.parent / out_name
        res = lint_batch(in_f, out_f)
        summary[res["status"]] = summary.get(res["status"], 0) + 1
        total_warnings += res.get("warning_count", 0)
        if res["status"] not in ("PASS", "WARN"):
            print(f"[{res['status']}] {rel.parent.name}/{out_name}: {res.get('error_count', '-')} errs")

    inventory_errors: List[str] = []
    if orphan_outs:
        inventory_errors.append(f"Orphan output files ({len(orphan_outs)}): {orphan_outs[:5]}")
    if len(all_in_ids) != len(set(all_in_ids)):
        inventory_errors.append(f"Global duplicate input IDs: {len(all_in_ids)} != {len(set(all_in_ids))}")

    inv = expected_inventory if expected_inventory is not None else (
        CORPUS_INVENTORY.get(game) if enforce_release_inventory else None
    )
    if inv is not None:
        if len(in_files) != inv["batches"]:
            inventory_errors.append(f"Batch count mismatch: {len(in_files)} != expected {inv['batches']}")
        if len(all_in_ids) != inv["entries"]:
            inventory_errors.append(f"Entry count mismatch: {len(all_in_ids)} != expected {inv['entries']}")

    id_map_path = wdir / "metadata" / "id_map.json"
    if id_map_path.exists():
        id_map = json.loads(id_map_path.read_text(encoding="utf-8"))
        if set(id_map.keys()) != set(all_in_ids):
            inventory_errors.append(
                f"id_map.json mismatch: {len(id_map)} keys vs {len(set(all_in_ids))} input IDs"
            )
    elif inv is not None:
        inventory_errors.append(f"Missing required metadata/id_map.json at {id_map_path}")

    for err in inventory_errors:
        print(f"[INVENTORY_ERROR] {game}: {err}")

    print(
        f"QA Summary ({game}): {summary} | warnings={total_warnings} | "
        f"batches={len(in_files)} | entries={len(all_in_ids)}"
    )
    if sum(summary.values()) == 0:
        return 2
    blocking = summary["FAIL"] + summary["MISSING"] + summary["ERROR"] + len(inventory_errors)
    return 1 if blocking > 0 else 0


def run_developer_qa(game: str, work_dir: Optional[Path] = None) -> int:
    """Developer QA: validates dynamic local corpus inventory without hardcoded release counts."""
    return run_qa(game, work_dir=work_dir, expected_inventory=None, enforce_release_inventory=False)


def run_player_qa(
    game: str,
    work_dir: Path,
    pack_dir: Path,
) -> int:
    """
    Player QA: validates Translation Pack manifest, local M1 id_map.json,
    match report completeness, tag parity, and translated text quality.
    """
    from translation_pack import verify_translation_pack, load_local_translations

    try:
        manifest = verify_translation_pack(game, pack_dir)
    except Exception as exc:
        print(f"[PLAYER_QA_FAIL] {game}: Pack verification failed: {exc}")
        return 1

    id_map_path = work_dir / "metadata" / "id_map.json"
    report_path = work_dir / "metadata" / "pack_match_report.json"
    if not id_map_path.is_file() or not report_path.is_file():
        print(f"[PLAYER_QA_FAIL] {game}: Missing id_map.json or pack_match_report.json in {work_dir}")
        return 1

    id_map = json.loads(id_map_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    translations = load_local_translations(work_dir)

    errors: List[str] = []
    warnings = 0

    if report.get("unmatched_local_count", 0) > 0:
        errors.append(f"Unmatched local entries: {report['unmatched_local_count']}")
    if report.get("tag_mismatch_count", 0) > 0:
        errors.append(f"Tag mismatches: {report['tag_mismatch_count']}")
    if report.get("matched_count", 0) != len(id_map):
        errors.append(f"Matched count {report.get('matched_count')} != local id_map {len(id_map)}")

    for eid, meta in id_map.items():
        if eid not in translations:
            errors.append(f"Missing translation for {eid}")
            continue
        zh = translations[eid]
        if not zh.strip():
            errors.append(f"Empty translation for {eid}")
            continue
        en_clean = meta.get("en_clean", "")
        in_phs = set(re.findall(r"\{\d+\}", en_clean))
        out_phs = set(re.findall(r"\{\d+\}", zh))
        if in_phs != out_phs:
            errors.append(f"Placeholder mismatch in {eid}: {in_phs} != {out_phs}")
        if zh == en_clean and len(en_clean) > 15 and " " in en_clean:
            warnings += 1

    # Verify auxiliary files declared by pack manifest exist in work_dir
    for aux_name in manifest.get("auxiliary_counts", {}):
        if aux_name == "upk_list.json":
            expected_p = (work_dir / "metadata" / "upk_list.json") if game == "knight" else (work_dir / "upk_list.json")
        else:
            expected_p = work_dir / aux_name
        if not expected_p.is_file():
            errors.append(f"Missing auxiliary file in work_dir: {expected_p}")

    if errors:
        for e in errors[:10]:
            print(f"[PLAYER_QA_FAIL] {game}: {e}")
        print(f"Player QA ({game}): FAIL ({len(errors)} errors, {warnings} warnings)")
        return 1

    print(
        f"Player QA ({game}): PASS | matched={len(translations)}/{len(id_map)} | "
        f"warnings={warnings} | pack_v={manifest.get('pack_format_version')}"
    )
    return 0


if __name__ == "__main__":
    # Usage: python m4_qa_linter.py [asylum|city|origins|knight]
    game = sys.argv[1] if len(sys.argv) > 1 else "asylum"
    sys.exit(run_qa(game))
