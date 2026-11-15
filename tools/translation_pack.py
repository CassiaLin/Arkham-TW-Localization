"""
Shared Translation Pack (Phase 1):
Versioned, English-free translation pack exporter, verifier, and importer/matcher.

Schema v1.0:
  - zh_tw.jsonl: one JSON object per line with:
      {"cat": str, "source_hash": sha256(en_raw), "tag_count": int, "tags_hash": sha256, "zh": str, "legacy_id": str}
  - Auxiliary hashed lookups (when applicable):
      - usm_sha256_zh.json (city, origins, knight)
      - upk_extra_sha256_zh.json (origins)
      - ak_en_sha256_zh.json (knight)
      - ak_events_zh.json (knight; keyed by event_file|event_id, no English text)
      - upk_list.json (knight/origins; list of .upk package names/paths, no English text)
  - manifest.json: pack_format_version, extractor_version, game, hash_algorithm,
      encoding, normalization, entry_count, auxiliary_counts, files {name: {sha256, bytes}}
"""
import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PACK_FORMAT_VERSION = "1.0"
EXTRACTOR_VERSION = "m1-v1"
HASH_ALGORITHM = "sha256"
STRING_ENCODING = "utf-8"
NORMALIZATION_RULE = "raw_exact"

SUPPORTED_GAMES = ("asylum", "city", "origins", "knight")
PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")


class TranslationPackError(RuntimeError):
    """Raised when a translation pack fails schema, hash, or matching checks."""


def _write_utf8_lf(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def sha256_text(text: str) -> str:
    """Return lowercase hex SHA-256 of UTF-8 encoded text."""
    return hashlib.sha256(text.encode(STRING_ENCODING)).hexdigest()


def sha256_file(path: Path) -> str:
    """Return lowercase hex SHA-256 of a file on disk."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def tags_signature(tags: List[str]) -> str:
    """Hash an ordered list of engine control tags for parity verification."""
    return sha256_text("\x00".join(tags))


def load_local_translations(work_dir: Path) -> Dict[str, str]:
    """Load all *.output.jsonl translations from work_dir/translated."""
    trans_dir = work_dir / "translated"
    translations: Dict[str, str] = {}
    if not trans_dir.exists():
        return translations
    for jp in sorted(trans_dir.rglob("*.output.jsonl")):
        with open(jp, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except json.JSONDecodeError as exc:
                    raise TranslationPackError(
                        f"Invalid JSON in {jp}:{line_no}: {exc}"
                    ) from exc
                eid = obj.get("id")
                zh = obj.get("zh")
                if eid and isinstance(zh, str):
                    translations[eid] = zh
    return translations


def _convert_en_dict_to_sha256(
    src_path: Path,
    dst_path: Path,
    include_stripped: bool = False,
) -> Tuple[int, int]:
    """
    Convert an English-keyed JSON dict {en: zh} into {sha256(en): zh}.
    Returns (output_entry_count, collision_count).
    Raises TranslationPackError on conflicting duplicate hashes.
    """
    raw_dict = json.loads(src_path.read_text(encoding="utf-8"))
    if not isinstance(raw_dict, dict):
        raise TranslationPackError(f"Expected JSON object in {src_path}")

    hashed: Dict[str, str] = {}
    collisions = 0
    for en_key, zh_val in raw_dict.items():
        if not isinstance(en_key, str) or not isinstance(zh_val, str):
            raise TranslationPackError(f"Invalid key/value type in {src_path}")
        candidates = [en_key]
        if include_stripped and en_key.strip() != en_key:
            candidates.append(en_key.strip())
        for cand in candidates:
            hk = sha256_text(cand)
            if hk in hashed and hashed[hk] != zh_val:
                collisions += 1
                raise TranslationPackError(
                    f"SHA-256 collision with differing zh in {src_path.name}: {hk}"
                )
            hashed[hk] = zh_val

    _write_utf8_lf(
        dst_path,
        json.dumps(hashed, ensure_ascii=False, indent=2) + "\n",
    )
    return len(hashed), collisions


def export_translation_pack(
    game: str,
    work_dir: Path,
    out_dir: Path,
) -> Dict[str, Any]:
    """
    Export a pure-Chinese Translation Pack for `game` from `work_dir` into `out_dir`.
    Verifies completeness and zero hash collisions.
    """
    if game not in SUPPORTED_GAMES:
        raise TranslationPackError(f"Unsupported game: {game}")

    id_map_path = work_dir / "metadata" / "id_map.json"
    if not id_map_path.is_file():
        raise TranslationPackError(f"Missing id_map.json: {id_map_path}")

    id_map = json.loads(id_map_path.read_text(encoding="utf-8"))
    translations = load_local_translations(work_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    zh_jsonl_path = out_dir / "zh_tw.jsonl"

    seen_keys: Dict[Tuple[str, str], str] = {}
    lines: List[str] = []
    missing_ids: List[str] = []

    for eid, meta in id_map.items():
        if eid not in translations:
            missing_ids.append(eid)
            continue
        cat = meta["category"]
        en_raw = meta["en_raw"]
        tags = meta.get("tags", [])
        zh = translations[eid]
        src_hash = sha256_text(en_raw)

        key = (cat, src_hash)
        if key in seen_keys and seen_keys[key] != zh:
            raise TranslationPackError(
                f"[{game}] Duplicate (cat, source_hash) with differing zh: {key} ({eid})"
            )
        seen_keys[key] = zh

        rec = {
            "cat": cat,
            "source_hash": src_hash,
            "tag_count": len(tags),
            "tags_hash": tags_signature(tags),
            "zh": zh,
            "legacy_id": eid,
        }
        lines.append(json.dumps(rec, ensure_ascii=False))

    if missing_ids:
        raise TranslationPackError(
            f"[{game}] Cannot export pack: {len(missing_ids)} entries missing translations "
            f"(first 5: {missing_ids[:5]})"
        )

    _write_utf8_lf(zh_jsonl_path, "\n".join(lines) + ("\n" if lines else ""))

    aux_counts: Dict[str, int] = {}
    pack_files: List[Path] = [zh_jsonl_path]

    # Auxiliary English-keyed dicts -> SHA-256 keyed JSONs
    aux_specs = [
        ("usm_zh.json", "usm_sha256_zh.json", False),
        ("upk_extra_zh.json", "upk_extra_sha256_zh.json", True),
        ("ak_en_zh.json", "ak_en_sha256_zh.json", False),
    ]
    for src_name, dst_name, strip_flag in aux_specs:
        src_p = work_dir / src_name
        if src_p.is_file():
            dst_p = out_dir / dst_name
            cnt, _ = _convert_en_dict_to_sha256(src_p, dst_p, include_stripped=strip_flag)
            aux_counts[dst_name] = cnt
            pack_files.append(dst_p)

    # Knight ak_events_zh.json (keyed by event_file|event_id, no English text)
    ak_ev_src = work_dir / "ak_events_zh.json"
    if ak_ev_src.is_file():
        ak_ev_dst = out_dir / "ak_events_zh.json"
        ev_data = json.loads(ak_ev_src.read_text(encoding="utf-8"))
        _write_utf8_lf(
            ak_ev_dst,
            json.dumps(ev_data, ensure_ascii=False, indent=2) + "\n",
        )
        aux_counts["ak_events_zh.json"] = len(ev_data)
        pack_files.append(ak_ev_dst)

    # Package list files (contain only .upk filenames / relative paths, no English text)
    for rel_list in (work_dir / "metadata" / "upk_list.json", work_dir / "upk_list.json"):
        if rel_list.is_file():
            dst_list = out_dir / "upk_list.json"
            list_data = json.loads(rel_list.read_text(encoding="utf-8"))
            _write_utf8_lf(
                dst_list,
                json.dumps(list_data, ensure_ascii=False, indent=2) + "\n",
            )
            aux_counts["upk_list.json"] = len(list_data)
            pack_files.append(dst_list)
            break

    file_hashes: Dict[str, Dict[str, Any]] = {}
    for fp in pack_files:
        file_hashes[fp.name] = {
            "sha256": sha256_file(fp),
            "bytes": fp.stat().st_size,
        }

    manifest = {
        "pack_format_version": PACK_FORMAT_VERSION,
        "extractor_version": EXTRACTOR_VERSION,
        "game": game,
        "hash_algorithm": HASH_ALGORITHM,
        "encoding": STRING_ENCODING,
        "normalization": NORMALIZATION_RULE,
        "entry_count": len(lines),
        "auxiliary_counts": aux_counts,
        "files": file_hashes,
    }
    manifest_path = out_dir / "manifest.json"
    _write_utf8_lf(
        manifest_path,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )
    return manifest


def verify_translation_pack(game: str, pack_dir: Path) -> Dict[str, Any]:
    """
    Verify Translation Pack manifest schema, format version, and SHA-256 of all listed files.
    Returns parsed manifest on success; raises TranslationPackError on any corruption or mismatch.
    """
    manifest_path = pack_dir / "manifest.json"
    if not manifest_path.is_file():
        raise TranslationPackError(f"[{game}] Missing Translation Pack manifest: {manifest_path}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TranslationPackError(f"[{game}] Corrupted manifest.json: {exc}") from exc

    if manifest.get("pack_format_version") != PACK_FORMAT_VERSION:
        raise TranslationPackError(
            f"[{game}] Unsupported pack_format_version: {manifest.get('pack_format_version')!r} "
            f"(expected {PACK_FORMAT_VERSION!r})"
        )
    if manifest.get("extractor_version") != EXTRACTOR_VERSION:
        raise TranslationPackError(
            f"[{game}] Incompatible extractor_version: {manifest.get('extractor_version')!r} "
            f"(expected {EXTRACTOR_VERSION!r})"
        )
    if manifest.get("game") != game:
        raise TranslationPackError(
            f"[{game}] Pack game mismatch: manifest says {manifest.get('game')!r}"
        )
    if manifest.get("hash_algorithm") != HASH_ALGORITHM:
        raise TranslationPackError(
            f"[{game}] Unsupported hash_algorithm: {manifest.get('hash_algorithm')!r}"
        )

    files = manifest.get("files")
    if not isinstance(files, dict) or "zh_tw.jsonl" not in files:
        raise TranslationPackError(f"[{game}] Manifest missing 'files' or 'zh_tw.jsonl'")

    for fname, meta in files.items():
        fp = pack_dir / fname
        if not fp.is_file():
            raise TranslationPackError(f"[{game}] Missing pack file: {fp}")
        actual_hash = sha256_file(fp)
        expected_hash = meta.get("sha256")
        if actual_hash != expected_hash:
            raise TranslationPackError(
                f"[{game}] Pack file corrupted ({fname}): expected {expected_hash}, got {actual_hash}"
            )

    # Verify entry count & required fields in zh_tw.jsonl
    zh_lines = [
        ln for ln in (pack_dir / "zh_tw.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    if len(zh_lines) != manifest.get("entry_count"):
        raise TranslationPackError(
            f"[{game}] zh_tw.jsonl count mismatch: manifest={manifest.get('entry_count')}, actual={len(zh_lines)}"
        )

    return manifest


def import_translation_pack(
    game: str,
    pack_dir: Path,
    work_dir: Path,
    strict: bool = True,
) -> Dict[str, Any]:
    """
    Match a verified Translation Pack against local M1 id_map.json using (category, source_hash).
    Does NOT rely on sequential IDs across extraction environments.
    Writes reconstructed work_dir/translated/pack_matched.output.jsonl and copies auxiliary files.
    """
    manifest = verify_translation_pack(game, pack_dir)

    id_map_path = work_dir / "metadata" / "id_map.json"
    if not id_map_path.is_file():
        raise TranslationPackError(f"[{game}] Missing local id_map.json: {id_map_path}")

    id_map = json.loads(id_map_path.read_text(encoding="utf-8"))

    # Load pack entries keyed by (cat, source_hash)
    pack_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for line_no, line in enumerate((pack_dir / "zh_tw.jsonl").read_text(encoding="utf-8").splitlines(), start=1):
        s = line.strip()
        if not s:
            continue
        rec = json.loads(s)
        for req_key in ("cat", "source_hash", "tag_count", "zh"):
            if req_key not in rec:
                raise TranslationPackError(
                    f"[{game}] zh_tw.jsonl line {line_no} missing field {req_key!r}"
                )
        key = (rec["cat"], rec["source_hash"])
        if key in pack_lookup:
            raise TranslationPackError(f"[{game}] Duplicate (cat, source_hash) in pack: {key}")
        pack_lookup[key] = rec

    matched_records: List[Dict[str, str]] = []
    used_keys = set()
    unmatched_local: List[str] = []
    tag_mismatches: List[Dict[str, Any]] = []

    for local_eid, meta in id_map.items():
        cat = meta["category"]
        src_hash = sha256_text(meta["en_raw"])
        local_tags = meta.get("tags", [])
        key = (cat, src_hash)

        rec = pack_lookup.get(key)
        if rec is None:
            unmatched_local.append(local_eid)
            continue

        used_keys.add(key)

        # Verify control-code / placeholder consistency
        if rec["tag_count"] != len(local_tags):
            tag_mismatches.append({
                "id": local_eid,
                "reason": "tag_count_mismatch",
                "pack_tag_count": rec["tag_count"],
                "local_tag_count": len(local_tags),
            })
            continue
        if "tags_hash" in rec and rec["tags_hash"] != tags_signature(local_tags):
            tag_mismatches.append({
                "id": local_eid,
                "reason": "tags_hash_mismatch",
            })
            continue
        ph_indices = [int(x) for x in PLACEHOLDER_RE.findall(rec["zh"])]
        if any(idx >= len(local_tags) for idx in ph_indices):
            tag_mismatches.append({
                "id": local_eid,
                "reason": "placeholder_out_of_range",
                "placeholders": ph_indices,
                "local_tag_count": len(local_tags),
            })
            continue

        matched_records.append({"id": local_eid, "zh": rec["zh"]})

    unused_pack = [
        rec.get("legacy_id") or f"{k[0]}:{k[1][:12]}"
        for k, rec in pack_lookup.items()
        if k not in used_keys
    ]

    report = {
        "game": game,
        "pack_format_version": manifest["pack_format_version"],
        "extractor_version": manifest["extractor_version"],
        "local_entries": len(id_map),
        "pack_entries": len(pack_lookup),
        "matched_count": len(matched_records),
        "unmatched_local_count": len(unmatched_local),
        "unmatched_local_sample": unmatched_local[:10],
        "unused_pack_count": len(unused_pack),
        "unused_pack_sample": unused_pack[:10],
        "tag_mismatch_count": len(tag_mismatches),
        "tag_mismatches": tag_mismatches[:10],
    }

    if strict and (unmatched_local or tag_mismatches):
        raise TranslationPackError(
            f"[{game}] Translation Pack matching failed: "
            f"unmatched_local={len(unmatched_local)}, tag_mismatches={len(tag_mismatches)} "
            f"(details: {json.dumps(report, ensure_ascii=False)})"
        )
    if tag_mismatches:
        raise TranslationPackError(
            f"[{game}] Tag/control-code mismatch detected: {tag_mismatches[:5]}"
        )

    # Write reconstructed translated output JSONL (clear stale files first)
    trans_dir = work_dir / "translated"
    if trans_dir.exists():
        for old_p in trans_dir.rglob("*.output.jsonl"):
            old_p.unlink()
    trans_dir.mkdir(parents=True, exist_ok=True)
    out_jsonl = trans_dir / "pack_matched.output.jsonl"
    out_jsonl.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in matched_records) + ("\n" if matched_records else ""),
        encoding="utf-8",
    )

    # Copy auxiliary files into work_dir (and metadata/upk_list.json for knight)
    for aux_name in (
        "usm_sha256_zh.json",
        "upk_extra_sha256_zh.json",
        "ak_en_sha256_zh.json",
        "ak_events_zh.json",
    ):
        aux_src = pack_dir / aux_name
        if aux_src.is_file():
            shutil.copy2(aux_src, work_dir / aux_name)

    upk_list_src = pack_dir / "upk_list.json"
    if upk_list_src.is_file():
        if game == "knight":
            shutil.copy2(upk_list_src, work_dir / "metadata" / "upk_list.json")
        else:
            shutil.copy2(upk_list_src, work_dir / "upk_list.json")

    report_path = work_dir / "metadata" / "pack_match_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


class HashedLookupDict:
    """Dict-like lookup supporting both plain English keys and SHA-256 hashed keys."""

    def __init__(
        self,
        plain_map: Optional[Dict[str, str]] = None,
        sha256_map: Optional[Dict[str, str]] = None,
    ):
        self.plain_map = plain_map or {}
        self.sha256_map = sha256_map or {}

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        if key in self.plain_map:
            return self.plain_map[key]
        if self.sha256_map:
            hk = sha256_text(key)
            if hk in self.sha256_map:
                return self.sha256_map[hk]
        return default

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return self.get(key) is not None

    def __getitem__(self, key: str) -> str:
        val = self.get(key)
        if val is None:
            raise KeyError(key)
        return val

    def __bool__(self) -> bool:
        return bool(self.plain_map or self.sha256_map)


def load_aux_lookup(
    work_dir: Path,
    plain_name: str,
    sha256_name: str,
) -> Optional[HashedLookupDict]:
    """
    Load an auxiliary translation lookup from `work_dir`:
    prefers plain English-keyed JSON if present (Private workspace),
    otherwise loads SHA-256 keyed JSON (Public/Player workspace).
    Returns None if neither exists.
    """
    plain_p = work_dir / plain_name
    sha_p = work_dir / sha256_name
    plain_map = json.loads(plain_p.read_text(encoding="utf-8")) if plain_p.is_file() else None
    sha_map = json.loads(sha_p.read_text(encoding="utf-8")) if sha_p.is_file() else None
    if plain_map is None and sha_map is None:
        return None
    return HashedLookupDict(plain_map=plain_map, sha256_map=sha_map)
