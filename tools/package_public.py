"""
Phase 8 & PUB-03/PUB-04 — Public Distribution Packager & Deep Content/Schema Auditor.
Builds a clean, self-contained public release directory using an explicit allowlist
via a temporary staging directory, audits both file paths and file contents/schemas,
and refuses unsafe output paths or symlinks.
"""
import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "tools"))

from translation_pack import SUPPORTED_GAMES, verify_translation_pack

ALLOWLIST_ROOT_FILES = [
    "arkham_mod_cli.py",
    "Install_TW_Mod.bat",
    "README.md",
    "LICENSE",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "requirements.txt",
    ".gitattributes",
]

ALLOWLIST_DIRS = [
    "tools",
    "translations",
    "glossary",
    "docs",
    "tests",
    ".github",
]

PUBLIC_GITIGNORE_CONTENT = """# Python
__pycache__/
*.py[cod]
*$py.class
*.log

# Local player/developer runtime workspaces, configs, backups, and builds
workspace/
config/
dist/
scratch/
_Backup_TW_Mod/

# Copyrighted game binaries & assets
*.exe
*.upk
*.usm
*.u
*.bik
*.dll
*.pak
*.bak

# Credentials & local environment
.env
.env.*
*.key
*.pem
"""

FORBIDDEN_EXTENSIONS = {".exe", ".upk", ".usm", ".u", ".bik", ".dll", ".pak"}
FORBIDDEN_FILENAMES = {"id_map.json", "game_paths.json", "extraction_stats.json"}
FORBIDDEN_ENGLISH_FIELDS = {"en", "en_raw", "en_clean", "ctx", "sources"}


class PublicAuditError(RuntimeError):
    """Raised when a public release directory contains excluded or unsafe content."""


def _validate_safe_out_dir(out_dir: Path, source_root: Path, overwrite: bool = False) -> Path:
    out_res = out_dir.resolve()
    src_res = source_root.resolve()

    if out_res == out_res.parent:
        raise PublicAuditError(f"Refusing filesystem root as output path: {out_res}")
    if out_res == src_res:
        raise PublicAuditError(f"Refusing source_root itself as output path: {out_res}")
    if src_res.is_relative_to(out_res):
        raise PublicAuditError(f"Refusing ancestor of source_root as output path: {out_res}")
    if out_res.is_relative_to(src_res):
        raise PublicAuditError(f"Refusing output path inside source_root: {out_res}")
    if out_res.exists():
        if not out_res.is_dir():
            raise PublicAuditError(f"Output path exists and is not a directory: {out_res}")
        if any(out_res.iterdir()) and not overwrite:
            raise PublicAuditError(
                f"Output directory is not empty: {out_res}. Pass --overwrite to replace."
            )
    return out_res


def build_public_release(
    out_dir: Path,
    source_root: Path = ROOT_DIR,
    overwrite: bool = False,
) -> Dict[str, Any]:
    """
    Copy ONLY explicitly allowlisted public files into a fresh staging directory,
    audit it, and then publish to `out_dir`.
    """
    out_res = _validate_safe_out_dir(out_dir, source_root, overwrite=overwrite)

    with tempfile.TemporaryDirectory(prefix="arkham_pub_stage_") as stage_tmp:
        stage_dir = Path(stage_tmp) / "public_stage"
        stage_dir.mkdir(parents=True, exist_ok=True)
        copied_files: List[str] = []

        for fname in ALLOWLIST_ROOT_FILES:
            src = source_root / fname
            if src.is_symlink():
                raise PublicAuditError(f"Refusing symlink in allowlist: {src}")
            if src.is_file():
                shutil.copy2(src, stage_dir / fname)
                copied_files.append(fname)

        # Write dedicated Public .gitignore (excludes workspace/, config/, dist/, game binaries)
        with open(stage_dir / ".gitignore", "w", encoding="utf-8", newline="\n") as f:
            f.write(PUBLIC_GITIGNORE_CONTENT)
        copied_files.append(".gitignore")

        for dname in ALLOWLIST_DIRS:
            src_dir = source_root / dname
            if not src_dir.is_dir():
                continue
            if src_dir.is_symlink():
                raise PublicAuditError(f"Refusing symlink directory: {src_dir}")
            for p in sorted(src_dir.rglob("*")):
                if p.is_symlink():
                    raise PublicAuditError(f"Refusing symlink in allowlist: {p}")
                if not p.is_file():
                    continue
                if "__pycache__" in p.parts or p.suffix == ".pyc":
                    continue
                rel = p.relative_to(source_root)
                dst = stage_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, dst)
                copied_files.append(rel.as_posix())

        # Audit staging directory BEFORE publishing to out_dir
        audit = audit_public_release(stage_dir)

        if out_res.exists():
            shutil.rmtree(out_res)
        shutil.copytree(stage_dir, out_res, symlinks=False)

    return {
        "out_dir": str(out_res),
        "file_count": len(copied_files),
        "files": copied_files,
        "audit": audit,
    }


def audit_public_release(pub_dir: Path) -> Dict[str, Any]:
    """
    Deep content + schema + allowlist + symlink audit of `pub_dir`.
    Raises PublicAuditError if any forbidden file, English source field, or game binary is found.
    """
    violations: List[str] = []
    total_files = 0
    total_bytes = 0
    allowed_top = set(ALLOWLIST_ROOT_FILES) | set(ALLOWLIST_DIRS) | {".gitignore"}

    for p in sorted(pub_dir.rglob("*")):
        if p.is_symlink():
            violations.append(f"Forbidden symlink: {p.relative_to(pub_dir).as_posix()}")
            continue
        if not p.is_file():
            continue
        total_files += 1
        total_bytes += p.stat().st_size
        rel = p.relative_to(pub_dir).as_posix()
        top_part = Path(rel).parts[0]

        if top_part not in allowed_top:
            violations.append(f"Non-allowlisted top-level path: {rel}")
        if p.suffix.lower() in FORBIDDEN_EXTENSIONS:
            violations.append(f"Forbidden binary extension: {rel}")
        if p.name in FORBIDDEN_FILENAMES:
            violations.append(f"Forbidden private metadata file: {rel}")
        if p.name.endswith(".input.jsonl") or p.name.endswith(".output.jsonl"):
            violations.append(f"Forbidden batch JSONL file: {rel}")
        if "workspace/" in rel or rel.startswith("workspace/") or "raw_backup/" in rel:
            violations.append(f"Forbidden private workspace path: {rel}")

        if rel.startswith("translations/") and p.name == "zh_tw.jsonl":
            for ln_no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                obj = json.loads(line)
                bad_keys = FORBIDDEN_ENGLISH_FIELDS.intersection(obj.keys())
                if bad_keys:
                    violations.append(f"{rel}:{ln_no} contains forbidden fields {sorted(bad_keys)}")
                if "source_hash" not in obj or len(obj["source_hash"]) != 64:
                    violations.append(f"{rel}:{ln_no} invalid source_hash")

        if rel.startswith("translations/") and p.name.endswith("_sha256_zh.json"):
            data = json.loads(p.read_text(encoding="utf-8"))
            for k in data.keys():
                if len(k) != 64 or any(c not in "0123456789abcdef" for c in k):
                    violations.append(f"{rel} contains non-SHA256 key: {k[:40]!r}")
                    break

    # Verify required root config files
    gitattr = pub_dir / ".gitattributes"
    if not gitattr.is_file() or "translations/** text eol=lf" not in gitattr.read_text(encoding="utf-8"):
        violations.append("Missing or invalid .gitattributes (translations/** text eol=lf required)")

    gitignore = pub_dir / ".gitignore"
    if not gitignore.is_file():
        violations.append("Missing public .gitignore")
    else:
        gi_text = gitignore.read_text(encoding="utf-8")
        for req_rule in ("workspace/", "config/", "dist/"):
            if req_rule not in gi_text:
                violations.append(f"Public .gitignore missing rule: {req_rule}")

    # Verify all 4 Translation Packs when translations/ is present
    if (pub_dir / "translations").is_dir():
        for g in SUPPORTED_GAMES:
            try:
                verify_translation_pack(g, pub_dir / "translations" / g)
            except Exception as exc:
                violations.append(f"Translation Pack verification failed for {g}: {exc}")

    if violations:
        raise PublicAuditError(
            f"Public release audit failed with {len(violations)} violations:\n"
            + "\n".join(violations[:20])
        )

    return {
        "status": "PASS",
        "total_files": total_files,
        "total_bytes": total_bytes,
        "violations": [],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Package and audit clean public release directory")
    parser.add_argument("out_dir", help="Target directory for clean public release")
    parser.add_argument("--overwrite", action="store_true", help="Replace non-empty out_dir")
    args = parser.parse_args()
    out = Path(args.out_dir)
    res = build_public_release(out, overwrite=args.overwrite)
    print(json.dumps({
        "out_dir": res["out_dir"],
        "file_count": res["file_count"],
        "audit": res["audit"],
    }, indent=2, ensure_ascii=False))
