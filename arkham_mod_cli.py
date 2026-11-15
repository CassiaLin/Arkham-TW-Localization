"""
Batman: Arkham Series - Taiwan Traditional Chinese Localization Toolkit CLI
Open-Source One-Click Pipeline:
  detect -> extract -> qa -> build -> install / uninstall
"""
import os
import re
import sys
import json
import hashlib
import shutil
import argparse
import subprocess
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

if os.name == "nt":
    import msvcrt
else:
    import fcntl

ROOT_DIR = Path(__file__).resolve().parent
TOOLS_DIR = ROOT_DIR / "tools"
sys.path.insert(0, str(TOOLS_DIR))

from game_detector import detect_all_games
from source_resolver import resolve_game_source, SourceResolutionError
from translation_pack import (
    export_translation_pack,
    import_translation_pack,
    verify_translation_pack,
    TranslationPackError,
)
from m4_qa_linter import run_qa as run_release_qa, run_developer_qa, run_player_qa
from m5_repack_int import repack_game_int
from m5_font_builder import collect_charset, patch_asylum_startup_upk, patch_modern_fonts_upk
from m5_repack_upk import repack_asylum_upks, repack_city_upks, repack_origins_upks, repack_knight_upks
from m5_repack_usm import repack_city_usms, repack_origins_usms, repack_knight_usms

GAME_KEYS = ["asylum", "city", "origins", "knight"]

MOD_DIR_NAMES = {
    "asylum": "Arkham_Asylum_TW_Mod",
    "city": "Arkham_City_TW_Mod",
    "origins": "Arkham_Origins_TW_Mod",
    "knight": "Arkham_Knight_TW_Mod",
}

VALID_MANIFEST_STATES = {
    "PREPARED",
    "APPLYING",
    "COMMITTED",
    "UNINSTALL_PREPARED",
    "UNINSTALLING",
    "UNINSTALLED",
}


class BackupIntegrityError(RuntimeError):
    """Raised when backup files, INIs, or manifest fail integrity/schema validation."""


class ExternalModificationError(RuntimeError):
    """Raised when a target game file was modified externally since installation."""


class ManifestValidationError(BackupIntegrityError):
    """Raised when manifest.json schema, version, or relative paths are invalid."""


class BuildIntegrityError(RuntimeError):
    """Raised when dist/<mod> lacks a valid completed build_manifest.json."""


class TransactionLockError(RuntimeError):
    """Raised when another install/update/uninstall transaction holds the game lock."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_copy(src: Path, dst: Path) -> str:
    """Copy src to dst via a temporary file + os.replace and verify SHA-256."""
    expected = sha256_file(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tw_tmp")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()
    actual = sha256_file(dst)
    if actual != expected:
        raise IOError(f"SHA-256 mismatch after copying {src} -> {dst}: {actual} != {expected}")
    return actual


def atomic_write_bytes(dst: Path, raw: bytes):
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".tw_tmp")
    try:
        tmp.write_bytes(raw)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            tmp.unlink()


def atomic_write_json(path: Path, data: dict):
    atomic_write_bytes(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))


def _safe_rel_path(base_dir: Path, rel_posix: str) -> Path:
    """Validate rel_posix is a safe relative path strictly inside base_dir (blocks path traversal)."""
    if not isinstance(rel_posix, str) or not rel_posix or "\\" in rel_posix:
        raise ManifestValidationError(f"Invalid relative path: {rel_posix!r}")
    p = PurePosixPath(rel_posix)
    if p.is_absolute() or any(part in ("", ".", "..") for part in p.parts) or ":" in rel_posix:
        raise ManifestValidationError(f"Unsafe relative path (traversal/drive): {rel_posix!r}")
    base_resolved = base_dir.resolve()
    target_resolved = (base_resolved / Path(*p.parts)).resolve()
    if not target_resolved.is_relative_to(base_resolved) or target_resolved == base_resolved:
        raise ManifestValidationError(f"Path escapes base directory: {rel_posix!r}")
    return base_dir / Path(*p.parts)


@contextmanager
def _game_lock(game_root: Path):
    """OS-managed per-game exclusive lock (auto-released by kernel on process crash/kill)."""
    game_root.mkdir(parents=True, exist_ok=True)
    lock_path = game_root / ".tw_mod.lock"
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
    try:
        if os.name == "nt":
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        raise TransactionLockError(
            f"Another mod transaction is currently active in {game_root} (lock: {lock_path})."
        )
    try:
        yield
    finally:
        try:
            if os.name == "nt":
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def get_asylum_ini_targets(game_root: Path) -> List[Path]:
    return [
        game_root / "BmGame" / "Config" / "UserEngine.ini",
        game_root / "BmGame" / "Config" / "BmEngine.ini",
        Path.home() / "Documents" / "Square Enix" / "Batman Arkham Asylum GOTY" / "BmGame" / "Config" / "BmEngine.ini",
    ]


def load_or_detect_games() -> dict:
    cfg_path = ROOT_DIR / "config" / "game_paths.json"
    if cfg_path.exists():
        return json.loads(cfg_path.read_text(encoding="utf-8"))
    return detect_all_games(cfg_path)


def write_batch_installers(game: str, default_root: str):
    """Generate thin .bat wrappers that delegate to arkham_mod_cli.py (single install engine)."""
    mod_dir = ROOT_DIR / "dist" / MOD_DIR_NAMES[game]
    mod_dir.mkdir(parents=True, exist_ok=True)
    cli_path = str(ROOT_DIR / "arkham_mod_cli.py").replace("/", "\\")

    install_bat = f"""@echo off
chcp 65001 >nul
setlocal

set "CLI={cli_path}"
if not exist "%CLI%" if exist "%~dp0..\\..\\arkham_mod_cli.py" set "CLI=%~dp0..\\..\\arkham_mod_cli.py"
if not exist "%CLI%" (
    echo [ERROR] Could not locate arkham_mod_cli.py
    if not defined ARKHAM_CLI_NO_PAUSE pause
    exit /b 1
)

python "%CLI%" install {game} %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo [ERROR] Installation failed with exit code %RC%.
    if not defined ARKHAM_CLI_NO_PAUSE pause
    exit /b %RC%
)
if not defined ARKHAM_CLI_NO_PAUSE pause
exit /b 0
"""

    uninstall_bat = f"""@echo off
chcp 65001 >nul
setlocal

set "CLI={cli_path}"
if not exist "%CLI%" if exist "%~dp0..\\..\\arkham_mod_cli.py" set "CLI=%~dp0..\\..\\arkham_mod_cli.py"
if not exist "%CLI%" (
    echo [ERROR] Could not locate arkham_mod_cli.py
    if not defined ARKHAM_CLI_NO_PAUSE pause
    exit /b 1
)

python "%CLI%" uninstall {game} %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo [ERROR] Uninstall failed with exit code %RC%.
    if not defined ARKHAM_CLI_NO_PAUSE pause
    exit /b %RC%
)
if not defined ARKHAM_CLI_NO_PAUSE pause
exit /b 0
"""
    (mod_dir / "install_mod.bat").write_text(install_bat, encoding="utf-8")
    (mod_dir / "uninstall_mod.bat").write_text(uninstall_bat, encoding="utf-8")


def write_build_manifest(game: str, mod_dir: Path):
    """Write build_manifest.json with SHA-256 hashes of all build outputs (rejects empty builds)."""
    files: Dict[str, str] = {}
    for f in sorted(mod_dir.rglob("*")):
        if f.is_file() and f.name not in ("install_mod.bat", "uninstall_mod.bat", "build_manifest.json"):
            files[f.relative_to(mod_dir).as_posix()] = sha256_file(f)
    if not files:
        raise BuildIntegrityError(f"[{game}] Build produced 0 mod files in {mod_dir}.")
    if not any(rel.lower().endswith(".int") for rel in files):
        raise BuildIntegrityError(f"[{game}] Build missing .int localization files in {mod_dir}.")
    atomic_write_json(
        mod_dir / "build_manifest.json",
        {
            "version": 1,
            "game": game,
            "completed": True,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
        },
    )


def verify_build_manifest(game: str, mod_dir: Path) -> Dict[str, str]:
    """Verify build_manifest.json exists, is non-empty, completed, and all files match SHA-256."""
    bm_path = mod_dir / "build_manifest.json"
    if not bm_path.exists():
        raise BuildIntegrityError(
            f"[{game}] Incomplete or unverified build in {mod_dir} (missing build_manifest.json). Run 'build {game}'."
        )
    bm = json.loads(bm_path.read_text(encoding="utf-8"))
    files = bm.get("files")
    if bm.get("completed") is not True or bm.get("game") != game or not isinstance(files, dict) or not files:
        raise BuildIntegrityError(f"[{game}] Invalid or empty build_manifest.json in {mod_dir}.")
    for rel_posix, expected_hash in files.items():
        fpath = _safe_rel_path(mod_dir, rel_posix)
        if not fpath.is_file():
            raise BuildIntegrityError(f"[{game}] Missing built file: {fpath}")
        if sha256_file(fpath) != expected_hash:
            raise BuildIntegrityError(f"[{game}] Corrupted built file (SHA-256 mismatch): {fpath}")
    return files


def build_game(game: str, cfg: dict, ttf_path: str, skip_qa: bool = False):
    """Run QA gate and build mod package atomically (preserving previous valid build on failure)."""
    if not skip_qa:
        print(f"\n=== Pre-build QA Gate: {game} ===")
        subprocess.run([sys.executable, str(TOOLS_DIR / "m4_qa_linter.py"), game], check=True)

    mod_dir = ROOT_DIR / "dist" / MOD_DIR_NAMES[game]
    prev_dir = ROOT_DIR / "dist" / f".{MOD_DIR_NAMES[game]}.prev"
    if prev_dir.exists():
        shutil.rmtree(prev_dir)
    if mod_dir.exists():
        print(f"[{game}] Preserving previous build at {prev_dir} during rebuild...")
        shutil.move(str(mod_dir), str(prev_dir))

    print(f"\n=== Building Mod Package: {game} ===")
    try:
        repack_game_int(game)
        if game == "asylum":
            repack_asylum_upks()
            if game in cfg and "startup_upk" in cfg[game]:
                bkp_startup = Path(cfg[game]["root_dir"]) / "_Backup_TW_Mod" / "BmGame" / "CookedPC" / "Startup_INT.upk"
                src = bkp_startup if bkp_startup.exists() and bkp_startup.stat().st_size > 16 else Path(cfg[game]["startup_upk"])
                dst = mod_dir / "BmGame" / "CookedPC" / "Startup_INT.upk"
                patch_asylum_startup_upk(src, dst, ttf_path, collect_charset(["asylum"]))
        else:
            if game == "city":
                repack_city_upks()
                repack_city_usms()
            elif game == "origins":
                repack_origins_upks()
                repack_origins_usms()
            elif game == "knight":
                repack_knight_upks()
                repack_knight_usms()
            rel_map = {
                "city": "BmGame/CookedPCConsole/fonts_en.upk",
                "origins": "SinglePlayer/BMGame/CookedPCConsole/fonts_en.upk",
                "knight": "BMGame/CookedPCConsole/fonts_en.upk",
            }
            if game in cfg:
                bkp_src = Path(cfg[game]["root_dir"]) / "_Backup_TW_Mod" / rel_map[game]
                src = bkp_src if bkp_src.exists() and bkp_src.stat().st_size > 16 else (Path(cfg[game]["root_dir"]) / rel_map[game])
                dst = mod_dir / rel_map[game]
                if src.exists():
                    patch_modern_fonts_upk(game, src, dst, ttf_path, collect_charset([game]))

        default_root = cfg[game]["root_dir"] if game in cfg else f"D:/SteamLibrary/steamapps/common/{game}"
        write_batch_installers(game, default_root)
        write_build_manifest(game, mod_dir)
    except Exception:
        if mod_dir.exists():
            shutil.rmtree(mod_dir, ignore_errors=True)
        if prev_dir.exists():
            shutil.move(str(prev_dir), str(mod_dir))
        raise
    else:
        if prev_dir.exists():
            shutil.rmtree(prev_dir, ignore_errors=True)


def _detect_text_encoding(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"
    try:
        raw.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def _validate_ini_target(game: str, game_root: Path, target_str: str) -> Path:
    """Restrict INI backup/restore targets to valid Asylum config files (blocks escape & symlinks)."""
    if game != "asylum":
        raise ManifestValidationError(f"[{game}] Non-asylum game cannot carry ini_backups: {target_str!r}")
    p = Path(target_str)
    if p.is_symlink():
        raise ManifestValidationError(f"Symlink INI target rejected: {target_str!r}")
    resolved = p.resolve()
    allowed_exact = {t.resolve() for t in get_asylum_ini_targets(game_root)}
    game_root_res = game_root.resolve()
    if resolved in allowed_exact:
        return p
    if resolved.is_relative_to(game_root_res) and resolved.suffix.lower() == ".ini":
        # Ensure no parent directory inside game_root is a symlink escaping outside
        cur = p
        while cur != game_root and cur.parent != cur:
            if cur.is_symlink():
                raise ManifestValidationError(f"Symlink component in INI path rejected: {target_str!r}")
            cur = cur.parent
        return p
    raise ManifestValidationError(f"Unauthorized external INI target rejected: {target_str!r}")


def _validate_and_migrate_manifest(raw: dict, game: str, game_root: Path, backup_dir: Path) -> dict:
    """Validate manifest schema, game, state, and path safety; migrate v1/v2 cleanly."""
    if not isinstance(raw, dict):
        raise ManifestValidationError("Manifest must be a JSON object.")
    ver = raw.get("version")
    if ver not in (1, 2, 3):
        raise ManifestValidationError(f"Unsupported manifest version: {ver!r}")
    if raw.get("game") != game:
        raise ManifestValidationError(f"Manifest game mismatch: {raw.get('game')!r} != {game!r}")

    state = raw.get("state", "COMMITTED" if ver in (1, 2) else None)
    if state not in VALID_MANIFEST_STATES:
        raise ManifestValidationError(f"Invalid manifest state: {state!r}")

    bkp_files = raw.get("backup_files")
    if not isinstance(bkp_files, dict):
        raise ManifestValidationError("Manifest 'backup_files' must be a dict.")
    for rel_posix, rec in bkp_files.items():
        _safe_rel_path(game_root, rel_posix)
        _safe_rel_path(backup_dir, rel_posix)
        if not isinstance(rec, dict) or not rec.get("sha256_orig"):
            raise ManifestValidationError(f"Manifest backup entry missing sha256_orig: {rel_posix!r}")

    raw_added = raw.get("added_files")
    migrated_added: Dict[str, dict] = {}
    if isinstance(raw_added, list):
        for rel_posix in raw_added:
            _safe_rel_path(game_root, rel_posix)
            migrated_added[rel_posix] = {"sha256_mod": None}
    elif isinstance(raw_added, dict):
        for rel_posix, rec in raw_added.items():
            _safe_rel_path(game_root, rel_posix)
            if not isinstance(rec, dict) or "sha256_mod" not in rec:
                raise ManifestValidationError(f"Invalid added_files entry: {rel_posix!r}")
            migrated_added[rel_posix] = rec
    else:
        raise ManifestValidationError("Manifest 'added_files' must be a list or dict.")

    ini_backups = raw.get("ini_backups")
    if not isinstance(ini_backups, list):
        raise ManifestValidationError("Manifest 'ini_backups' must be a list.")
    for entry in ini_backups:
        if not isinstance(entry, dict):
            raise ManifestValidationError("Invalid INI backup entry.")
        _safe_rel_path(backup_dir, entry.get("backup_rel", ""))
        if not entry.get("sha256_orig") or not isinstance(entry.get("target"), str):
            raise ManifestValidationError("INI backup entry missing target or sha256_orig.")
        _validate_ini_target(game, game_root, entry["target"])

    return {
        "version": 3,
        "game": game,
        "state": state,
        "installed_at": raw.get("installed_at", ""),
        "backup_files": bkp_files,
        "added_files": migrated_added,
        "ini_backups": ini_backups,
        "tx_snapshot_dir": raw.get("tx_snapshot_dir"),
    }


def _verify_backup_preflight(game: str, backup_dir: Path, manifest: dict):
    """Verify all backed-up original files and INIs exist and match sha256_orig."""
    for rel_posix, rec in manifest["backup_files"].items():
        bkp = _safe_rel_path(backup_dir, rel_posix)
        if not bkp.exists():
            raise BackupIntegrityError(
                f"[{game}] Missing original backup file: {bkp}. "
                "Verify game files via Steam instead of uninstalling."
            )
        if sha256_file(bkp) != rec["sha256_orig"]:
            raise BackupIntegrityError(
                f"[{game}] Corrupted original backup (SHA-256 mismatch): {bkp}. "
                "Aborting to protect game directory."
            )

    for entry in manifest["ini_backups"]:
        bkp_ini = _safe_rel_path(backup_dir, entry["backup_rel"])
        if not bkp_ini.exists():
            raise BackupIntegrityError(f"[{game}] Missing INI backup: {bkp_ini}")
        if sha256_file(bkp_ini) != entry["sha256_orig"]:
            raise BackupIntegrityError(f"[{game}] Corrupted INI backup (SHA-256 mismatch): {bkp_ini}")


def _check_targets_not_externally_modified(
    game: str,
    game_root: Path,
    manifest: dict,
    force: bool = False,
):
    """Verify current game files/added_files/INIs match expected orig or mod hashes (R3-P0-01)."""
    if force:
        return
    recovery_hint = (
        f"Run Steam 'Verify integrity of game files' if the game updated, "
        f"or re-run with --force to explicitly overwrite."
    )
    for rel_posix, rec in manifest["backup_files"].items():
        target = _safe_rel_path(game_root, rel_posix)
        if target.exists():
            cur = sha256_file(target)
            allowed = {rec["sha256_orig"]}
            if rec.get("sha256_mod"):
                allowed.add(rec["sha256_mod"])
            if cur not in allowed:
                raise ExternalModificationError(
                    f"[{game}] Target file {rel_posix} was modified externally (e.g. Steam update or another mod). {recovery_hint}"
                )

    for rel_posix, rec in manifest["added_files"].items():
        target = _safe_rel_path(game_root, rel_posix)
        if target.exists():
            expected_mod = rec.get("sha256_mod")
            if not expected_mod or sha256_file(target) != expected_mod:
                raise ExternalModificationError(
                    f"[{game}] Mod-added file {rel_posix} was modified externally or lacks mod hash. "
                    f"Refusing to delete without --force. {recovery_hint}"
                )

    for entry in manifest["ini_backups"]:
        target_ini = Path(entry["target"])
        if target_ini.exists():
            cur = sha256_file(target_ini)
            allowed = {entry["sha256_orig"]}
            if entry.get("sha256_mod"):
                allowed.add(entry["sha256_mod"])
            if cur not in allowed:
                raise ExternalModificationError(
                    f"[{game}] INI file {target_ini} was modified externally after mod install. "
                    f"Refusing to overwrite player settings without --force."
                )


def _delete_added_file(game_root: Path, rel_posix: str) -> int:
    target = _safe_rel_path(game_root, rel_posix)
    if not target.exists():
        return 0
    target.unlink()
    parent = target.parent
    while parent != game_root:
        try:
            parent.rmdir()
            parent = parent.parent
        except OSError:
            break
    return 1


def _verify_and_load_snapshot(game: str, game_root: Path, backup_dir: Path, snap_dir: Path) -> dict:
    """Verify snapshot.json and all snapshot files/INIs match SHA-256 before rollback (fail closed)."""
    snap_meta_path = snap_dir / "snapshot.json"
    if not snap_dir.is_dir() or not snap_meta_path.is_file():
        raise BackupIntegrityError(
            f"[{game}] Update snapshot missing at {snap_dir}; aborting recovery to protect Original Backup."
        )
    snap = json.loads(snap_meta_path.read_text(encoding="utf-8"))
    if snap.get("version") != 1 or not isinstance(snap.get("v1_manifest"), dict) or not isinstance(snap.get("targets"), dict):
        raise BackupIntegrityError(f"[{game}] Corrupted snapshot.json schema at {snap_meta_path}.")

    _validate_and_migrate_manifest(snap["v1_manifest"], game, game_root, backup_dir)

    for rel_posix, info in snap["targets"].items():
        _safe_rel_path(game_root, rel_posix)
        if info.get("existed"):
            sf = _safe_rel_path(snap_dir, info.get("snap_rel", ""))
            if not sf.is_file() or sha256_file(sf) != info.get("sha256"):
                raise BackupIntegrityError(f"[{game}] Corrupted or missing snapshot file: {sf}")

    for ini_info in snap.get("ini_targets", []):
        _validate_ini_target(game, game_root, ini_info.get("target", ""))
        if ini_info.get("existed"):
            si = _safe_rel_path(snap_dir, ini_info.get("snap_rel", ""))
            if not si.is_file() or sha256_file(si) != ini_info.get("sha256"):
                raise BackupIntegrityError(f"[{game}] Corrupted or missing snapshot INI: {si}")

    return snap


def rollback_transaction(game: str, game_root: Path, backup_dir: Path, manifest: dict):
    """Roll back an unfinished PREPARED/APPLYING transaction to V1 (update) or clean orig (first install)."""
    snap_rel = manifest.get("tx_snapshot_dir")
    if snap_rel:
        snap_dir = _safe_rel_path(backup_dir, snap_rel)
        snap = _verify_and_load_snapshot(game, game_root, backup_dir, snap_dir)
        v1_manifest = _validate_and_migrate_manifest(snap["v1_manifest"], game, game_root, backup_dir)

        for rel_posix, info in snap["targets"].items():
            target = _safe_rel_path(game_root, rel_posix)
            if info.get("existed"):
                atomic_copy(_safe_rel_path(snap_dir, info["snap_rel"]), target)
            else:
                _delete_added_file(game_root, rel_posix)

        for ini_info in snap.get("ini_targets", []):
            ini_target = _validate_ini_target(game, game_root, ini_info["target"])
            if ini_info.get("existed"):
                atomic_copy(_safe_rel_path(snap_dir, ini_info["snap_rel"]), ini_target)

        v1_manifest["state"] = "COMMITTED"
        v1_manifest["tx_snapshot_dir"] = None
        atomic_write_json(backup_dir / "manifest.json", v1_manifest)
        shutil.rmtree(snap_dir, ignore_errors=True)
        print(f"[{game}] Rolled back failed update to previous V1 mod state.")
        return

    # First-install rollback: restore originals from Original Backup & remove added files
    _verify_backup_preflight(game, backup_dir, manifest)
    for rel_posix in manifest["backup_files"]:
        atomic_copy(_safe_rel_path(backup_dir, rel_posix), _safe_rel_path(game_root, rel_posix))
    for rel_posix in manifest["added_files"]:
        _delete_added_file(game_root, rel_posix)
    for entry in manifest["ini_backups"]:
        atomic_copy(
            _safe_rel_path(backup_dir, entry["backup_rel"]),
            _validate_ini_target(game, game_root, entry["target"]),
        )
    shutil.rmtree(backup_dir, ignore_errors=True)
    print(f"[{game}] Rolled back failed first-time installation to clean original state.")


def install_game_mod(
    game: str,
    game_root: Path,
    mod_dir: Path,
    ini_targets: Optional[List[Path]] = None,
    force: bool = False,
):
    """Transactionally install or update mod_dir into game_root with separated Original Backup & Snapshot."""
    mod_file_hashes = verify_build_manifest(game, mod_dir)

    with _game_lock(game_root):
        backup_dir = game_root / "_Backup_TW_Mod"
        manifest_path = backup_dir / "manifest.json"
        existing_manifest: Optional[dict] = None

        if backup_dir.exists() and not manifest_path.exists():
            raise BackupIntegrityError(
                f"[{game}] Found legacy backup directory {backup_dir} without manifest.json. "
                "Cannot verify integrity; restore or remove _Backup_TW_Mod manually before installing."
            )

        if manifest_path.exists():
            existing_manifest = _validate_and_migrate_manifest(
                json.loads(manifest_path.read_text(encoding="utf-8")),
                game,
                game_root,
                backup_dir,
            )
            st = existing_manifest["state"]
            if st == "UNINSTALLED":
                shutil.rmtree(backup_dir)
                existing_manifest = None
            elif st in ("UNINSTALL_PREPARED", "UNINSTALLING"):
                raise BackupIntegrityError(
                    f"[{game}] Previous uninstall was interrupted ({st}). Run 'uninstall {game}' to finish restoring first."
                )
            elif st in ("PREPARED", "APPLYING"):
                print(f"[{game}] Recovering unfinished install/update transaction ({st})...")
                rollback_transaction(game, game_root, backup_dir, existing_manifest)
                if manifest_path.exists():
                    existing_manifest = _validate_and_migrate_manifest(
                        json.loads(manifest_path.read_text(encoding="utf-8")),
                        game,
                        game_root,
                        backup_dir,
                    )
                else:
                    existing_manifest = None

        is_update = existing_manifest is not None
        if is_update:
            _verify_backup_preflight(game, backup_dir, existing_manifest)
            _check_targets_not_externally_modified(game, game_root, existing_manifest, force=force)
            v1_manifest = json.loads(json.dumps(existing_manifest))
            new_manifest = json.loads(json.dumps(existing_manifest))
        else:
            v1_manifest = None
            new_manifest = {
                "version": 3,
                "game": game,
                "state": "PREPARED",
                "installed_at": "",
                "backup_files": {},
                "added_files": {},
                "ini_backups": [],
                "tx_snapshot_dir": None,
            }

        # Prepare complete Transaction Snapshot if updating V1 -> V2
        snap_rel = "_tx_snapshot" if is_update else None
        snap_dir = (backup_dir / snap_rel) if snap_rel else None
        snap_targets: Dict[str, dict] = {}
        snap_inis: List[dict] = []
        if is_update and snap_dir is not None:
            if snap_dir.exists():
                shutil.rmtree(snap_dir)
            snap_dir.mkdir(parents=True, exist_ok=True)
            new_manifest["tx_snapshot_dir"] = snap_rel

        backup_files = new_manifest["backup_files"]
        added_files = new_manifest["added_files"]
        ini_backups = new_manifest["ini_backups"]

        # Detect files present in V1 but removed in V2
        v2_rels = set(mod_file_hashes.keys())
        removed_from_v1_added = [r for r in list(added_files.keys()) if r not in v2_rels]
        removed_from_v1_backup = [r for r in list(backup_files.keys()) if r not in v2_rels]

        staged_copies: List[Tuple[Path, Path]] = []
        for rel_posix, mod_hash in mod_file_hashes.items():
            src = _safe_rel_path(mod_dir, rel_posix)
            target = _safe_rel_path(game_root, rel_posix)
            bkp = _safe_rel_path(backup_dir, rel_posix)

            if is_update and snap_dir is not None:
                if target.exists():
                    s_rel = f"files/{rel_posix}"
                    s_hash = atomic_copy(target, _safe_rel_path(snap_dir, s_rel))
                    snap_targets[rel_posix] = {"existed": True, "snap_rel": s_rel, "sha256": s_hash}
                else:
                    snap_targets[rel_posix] = {"existed": False, "snap_rel": None, "sha256": None}

            if rel_posix not in backup_files and rel_posix not in added_files:
                if target.exists():
                    orig_hash = atomic_copy(target, bkp)
                    backup_files[rel_posix] = {"sha256_orig": orig_hash, "sha256_mod": mod_hash}
                else:
                    added_files[rel_posix] = {"sha256_mod": mod_hash}
            elif rel_posix in backup_files:
                backup_files[rel_posix]["sha256_mod"] = mod_hash
            elif rel_posix in added_files:
                added_files[rel_posix]["sha256_mod"] = mod_hash

            staged_copies.append((src, target))

        if is_update and snap_dir is not None:
            for rel_posix in removed_from_v1_added + removed_from_v1_backup:
                target = _safe_rel_path(game_root, rel_posix)
                if target.exists():
                    s_rel = f"files/{rel_posix}"
                    s_hash = atomic_copy(target, _safe_rel_path(snap_dir, s_rel))
                    snap_targets[rel_posix] = {"existed": True, "snap_rel": s_rel, "sha256": s_hash}
                else:
                    snap_targets[rel_posix] = {"existed": False, "snap_rel": None, "sha256": None}

        pending_ini_writes: List[Tuple[Path, bytes, dict]] = []
        if game == "asylum":
            targets_ini = ini_targets if ini_targets is not None else get_asylum_ini_targets(game_root)
            ini_by_target = {entry["target"]: entry for entry in ini_backups}
            for idx, ini_p in enumerate(targets_ini):
                if not ini_p.exists():
                    continue
                _validate_ini_target(game, game_root, str(ini_p))
                raw_bytes = ini_p.read_bytes()
                enc = _detect_text_encoding(raw_bytes)
                txt = raw_bytes.decode(enc)
                new_txt = re.sub(r"(?i)bSubtitlesEnabled\s*=\s*False", "bSubtitlesEnabled=True", txt)
                if new_txt != txt:
                    if is_update and snap_dir is not None:
                        s_ini_rel = f"ini/{idx}_{ini_p.name}"
                        s_ini_hash = atomic_copy(ini_p, _safe_rel_path(snap_dir, s_ini_rel))
                        snap_inis.append({
                            "target": str(ini_p),
                            "existed": True,
                            "snap_rel": s_ini_rel,
                            "sha256": s_ini_hash,
                        })
                    encoded_new = new_txt.encode(enc)
                    new_hash = hashlib.sha256(encoded_new).hexdigest()
                    if str(ini_p) not in ini_by_target:
                        bkp_rel = f"_ini_backups/{idx}_{ini_p.name}"
                        orig_ini_hash = atomic_copy(ini_p, backup_dir / bkp_rel)
                        entry = {
                            "target": str(ini_p),
                            "backup_rel": bkp_rel,
                            "sha256_orig": orig_ini_hash,
                            "sha256_mod": new_hash,
                        }
                        ini_backups.append(entry)
                        ini_by_target[str(ini_p)] = entry
                    else:
                        ini_by_target[str(ini_p)]["sha256_mod"] = new_hash
                    pending_ini_writes.append((ini_p, encoded_new, ini_by_target[str(ini_p)]))

        if is_update and snap_dir is not None:
            atomic_write_json(
                snap_dir / "snapshot.json",
                {
                    "version": 1,
                    "v1_manifest": v1_manifest,
                    "targets": snap_targets,
                    "ini_targets": snap_inis,
                },
            )

        # Phase 1: PREPARED
        new_manifest["state"] = "PREPARED"
        atomic_write_json(manifest_path, new_manifest)

        # Phase 2: APPLYING
        new_manifest["state"] = "APPLYING"
        atomic_write_json(manifest_path, new_manifest)

        try:
            for src, dst in staged_copies:
                atomic_copy(src, dst)
            for rel_posix in removed_from_v1_added:
                _delete_added_file(game_root, rel_posix)
                added_files.pop(rel_posix, None)
            for rel_posix in removed_from_v1_backup:
                atomic_copy(_safe_rel_path(backup_dir, rel_posix), _safe_rel_path(game_root, rel_posix))
                backup_files.pop(rel_posix, None)
            for ini_p, encoded_bytes, _entry in pending_ini_writes:
                atomic_write_bytes(ini_p, encoded_bytes)
                print(f"[{game}] Enabled bSubtitlesEnabled=True in {ini_p} (backup saved)")
        except Exception:
            print(f"[{game}] [ERROR] Transaction failed during APPLYING; rolling back...")
            rollback_transaction(game, game_root, backup_dir, new_manifest)
            raise

        # Phase 3: COMMITTED FIRST, then clean up snapshot (FINAL-01)
        new_manifest["tx_snapshot_dir"] = None
        new_manifest["state"] = "COMMITTED"
        new_manifest["installed_at"] = datetime.now(timezone.utc).isoformat()
        atomic_write_json(manifest_path, new_manifest)
        if snap_dir is not None and snap_dir.exists():
            shutil.rmtree(snap_dir, ignore_errors=True)
        print(f"[{game}] Installed {len(staged_copies)} files into {game_root} (manifest COMMITTED)")


def uninstall_game_mod(game: str, game_root: Path, force: bool = False) -> bool:
    """Transactionally verify backups and target files, then restore originals and remove added files."""
    with _game_lock(game_root):
        backup_dir = game_root / "_Backup_TW_Mod"
        if not backup_dir.exists():
            print(f"[{game}] No backup found at {backup_dir}")
            return False

        manifest_path = backup_dir / "manifest.json"
        if not manifest_path.exists():
            raise BackupIntegrityError(
                f"[{game}] Legacy backup at {backup_dir} has no manifest.json; cannot verify integrity."
            )

        manifest = _validate_and_migrate_manifest(
            json.loads(manifest_path.read_text(encoding="utf-8")),
            game,
            game_root,
            backup_dir,
        )
        st = manifest["state"]
        if st == "UNINSTALLED":
            shutil.rmtree(backup_dir)
            print(f"[{game}] Completed cleanup of previously UNINSTALLED backup directory.")
            return True

        if st in ("PREPARED", "APPLYING"):
            rollback_transaction(game, game_root, backup_dir, manifest)
            if not manifest_path.exists():
                return True
            manifest = _validate_and_migrate_manifest(
                json.loads(manifest_path.read_text(encoding="utf-8")),
                game,
                game_root,
                backup_dir,
            )
            st = manifest["state"]

        _verify_backup_preflight(game, backup_dir, manifest)
        if st == "COMMITTED":
            _check_targets_not_externally_modified(game, game_root, manifest, force=force)
            manifest["state"] = "UNINSTALL_PREPARED"
            atomic_write_json(manifest_path, manifest)

        manifest["state"] = "UNINSTALLING"
        atomic_write_json(manifest_path, manifest)

        restored, ini_restored, removed = 0, 0, 0
        for rel_posix in manifest["backup_files"]:
            atomic_copy(_safe_rel_path(backup_dir, rel_posix), _safe_rel_path(game_root, rel_posix))
            restored += 1
        for rel_posix in list(manifest["added_files"].keys()):
            removed += _delete_added_file(game_root, rel_posix)
        for entry in manifest["ini_backups"]:
            atomic_copy(
                _safe_rel_path(backup_dir, entry["backup_rel"]),
                _validate_ini_target(game, game_root, entry["target"]),
            )
            ini_restored += 1

        manifest["state"] = "UNINSTALLED"
        atomic_write_json(manifest_path, manifest)
        shutil.rmtree(backup_dir)

        print(
            f"[{game}] Restored {restored} files, {ini_restored} INI(s), "
            f"removed {removed} mod-added files in {game_root} (UNINSTALLED)"
        )
        return True


def cmd_detect(_args) -> int:
    cfg_path = ROOT_DIR / "config" / "game_paths.json"
    games = detect_all_games(cfg_path)
    print(f"Saved {len(games)} detected games to {cfg_path}")
    return 0


def cmd_qa(args) -> int:
    targets = GAME_KEYS if args.game == "all" else [args.game]
    rc = 0
    for g in targets:
        print(f"\n=== QA Linter: {g} ===")
        proc = subprocess.run([sys.executable, str(TOOLS_DIR / "m4_qa_linter.py"), g])
        if proc.returncode != 0:
            rc = proc.returncode
    return rc


def cmd_build(args) -> int:
    cfg = load_or_detect_games()
    targets = GAME_KEYS if args.game == "all" else [args.game]
    failed = []
    for g in targets:
        try:
            build_game(g, cfg, args.ttf, skip_qa=getattr(args, "skip_qa", False))
        except Exception as e:
            print(f"[{g}] [BUILD ERROR] {e}")
            failed.append(g)
    if failed:
        print(f"\n[BUILD FAILED] Games failed: {failed}")
        return 1
    return 0


def cmd_install(args) -> int:
    cfg = load_or_detect_games()
    targets = GAME_KEYS if args.game == "all" else [args.game]
    failed = []
    for g in targets:
        if g not in cfg:
            print(f"[{g}] Not detected in config/game_paths.json.")
            failed.append(g)
            continue
        game_root = Path(cfg[g]["root_dir"])
        mod_dir = ROOT_DIR / "dist" / MOD_DIR_NAMES[g]
        if not mod_dir.exists():
            print(f"[{g}] Mod dir {mod_dir} not built yet. Run 'build {g}' first.")
            failed.append(g)
            continue
        try:
            install_game_mod(g, game_root, mod_dir, force=getattr(args, "force", False))
        except Exception as e:
            print(f"[{g}] [INSTALL ERROR] {e}")
            failed.append(g)
    if failed:
        print(f"\n[INSTALL FAILED] Games failed: {failed}")
        return 1
    return 0


def cmd_uninstall(args) -> int:
    cfg = load_or_detect_games()
    targets = GAME_KEYS if args.game == "all" else [args.game]
    failed = []
    for g in targets:
        if g not in cfg:
            print(f"[{g}] Not detected in config/game_paths.json.")
            failed.append(g)
            continue
        try:
            ok = uninstall_game_mod(g, Path(cfg[g]["root_dir"]), force=getattr(args, "force", False))
            if not ok:
                failed.append(g)
        except Exception as e:
            print(f"[{g}] [UNINSTALL ERROR] {e}")
            failed.append(g)
    if failed:
        print(f"\n[UNINSTALL FAILED] Games failed: {failed}")
        return 1
    return 0


def _sync_tool_roots() -> None:
    """Propagate ROOT_DIR to tool modules (useful for isolated clean-room tests)."""
    import m5_repack_int
    import m5_repack_upk
    import m5_repack_usm
    import m5_font_builder
    import m2_inject_glossary

    m5_repack_int.ROOT_DIR = ROOT_DIR
    m5_repack_upk.ROOT_DIR = ROOT_DIR
    m5_repack_usm.ROOT_DIR = ROOT_DIR
    m5_font_builder.ROOT_DIR = ROOT_DIR
    m2_inject_glossary.ROOT_DIR = ROOT_DIR
    m2_inject_glossary.GLOSSARY_PATH = ROOT_DIR / "glossary" / "dc_tw_glossary.json"


def _run_m1_extractor(game: str, allow_legacy_backup: bool = False) -> None:
    """Run the M1 extractor for `game` using OriginalSourceResolver."""
    _sync_tool_roots()
    if game == "asylum":
        import m1_extract as mod
        fn = mod.run_extraction
    elif game == "city":
        import m1_extract_city as mod
        fn = mod.run_city_extraction
    elif game == "origins":
        import m1_extract_origins as mod
        fn = mod.run_origins_extraction
    elif game == "knight":
        import m1_extract_knight as mod
        fn = mod.run_knight_extraction
    else:
        raise ValueError(f"Unsupported game: {game}")

    mod.ROOT_DIR = ROOT_DIR
    mod.CONFIG_PATH = ROOT_DIR / "config" / "game_paths.json"
    mod.WORK_DIR = ROOT_DIR / "workspace" / game
    fn(allow_legacy_backup=allow_legacy_backup)


def _get_pack_dir(args, game: str) -> Path:
    base = Path(args.pack_dir) if getattr(args, "pack_dir", None) else (ROOT_DIR / "translations")
    return base / game


def cmd_player(args) -> int:
    subcmd = args.player_cmd
    cfg = load_or_detect_games()

    if subcmd == "status":
        targets = GAME_KEYS if args.game == "all" else [args.game]
        for g in targets:
            pack_dir = _get_pack_dir(args, g)
            pack_ok = (pack_dir / "manifest.json").is_file()
            if g not in cfg:
                print(f"[{g}] steam=NOT_DETECTED | pack={'READY' if pack_ok else 'MISSING'}")
                continue
            game_root = Path(cfg[g]["root_dir"])
            bkp_manifest = game_root / "_Backup_TW_Mod" / "manifest.json"
            if bkp_manifest.is_file():
                try:
                    raw = json.loads(bkp_manifest.read_text(encoding="utf-8"))
                    state = raw.get("state", f"LEGACY_v{raw.get('version')}")
                except Exception:
                    state = "CORRUPTED_MANIFEST"
            elif (game_root / "_Backup_TW_Mod").exists():
                state = "LEGACY_BACKUP_NO_MANIFEST"
            else:
                state = "VANILLA_NOT_INSTALLED"
            print(f"[{g}] steam={game_root} | mod_state={state} | pack={'READY' if pack_ok else 'MISSING'}")
        return 0

    if subcmd in ("install", "update-pack"):
        targets = [g for g in GAME_KEYS if g in cfg] if args.game == "all" else [args.game]
        if not targets:
            print("[PLAYER] No supported Steam Arkham games detected.")
            return 1
        allow_legacy = getattr(args, "allow_legacy_backup", False)
        failed = []
        for g in targets:
            if g not in cfg:
                print(f"[{g}] Not detected in Steam library.")
                failed.append(g)
                continue
            game_root = Path(cfg[g]["root_dir"])
            pack_dir = _get_pack_dir(args, g)
            work_dir = ROOT_DIR / "workspace" / g
            mod_dir = ROOT_DIR / "dist" / MOD_DIR_NAMES[g]
            try:
                print(f"\n=== Player Mode ({subcmd}): {g} ===")
                # 1. Validate original source & pack before touching anything
                resolve_game_source(g, game_root, allow_legacy_backup=allow_legacy)
                verify_translation_pack(g, pack_dir)
                # 2. Local M1 extraction (from verified original source)
                _run_m1_extractor(g, allow_legacy_backup=allow_legacy)
                # 3. Match Translation Pack by (category, source_hash)
                import_translation_pack(g, pack_dir, work_dir, strict=True)
                # 4. Player QA (mandatory, never skipped)
                qa_rc = run_player_qa(g, work_dir, pack_dir)
                if qa_rc != 0:
                    raise RuntimeError(f"Player QA failed with code {qa_rc}")
                # 5. Build + Verify build manifest
                _sync_tool_roots()
                build_game(g, cfg, getattr(args, "ttf", r"C:\Windows\Fonts\msjh.ttc"), skip_qa=True)
                verify_build_manifest(g, mod_dir)
                # 6. Transactional install / update
                install_game_mod(g, game_root, mod_dir, force=getattr(args, "force", False))
            except (SourceResolutionError, TranslationPackError, Exception) as exc:
                print(f"[{g}] [PLAYER ERROR] {exc}")
                failed.append(g)
        return 1 if failed else 0

    if subcmd == "uninstall":
        return cmd_uninstall(args)

    return 1


def _dev_prepare_game(game: str) -> int:
    _sync_tool_roots()
    import m2_inject_glossary

    work_dir = ROOT_DIR / "workspace" / game
    batches_dir = work_dir / "batches"
    if not batches_dir.exists():
        print(f"[{game}] No batches/ found. Run `dev extract {game}` first.")
        return 1

    prep_dir = work_dir / "prepared"
    prep_dir.mkdir(parents=True, exist_ok=True)
    in_files = sorted(batches_dir.rglob("*.input.jsonl"))
    for in_f in in_files:
        rel = in_f.relative_to(batches_dir)
        out_prompt = prep_dir / rel.with_name(in_f.name.replace(".input.jsonl", ".prompt.txt"))
        out_prompt.parent.mkdir(parents=True, exist_ok=True)
        out_prompt.write_text(m2_inject_glossary.build_batch_prompt_header(in_f), encoding="utf-8")

    print(f"[{game}] Prepared {len(in_files)} batch prompt headers in {prep_dir}")
    return 0


def _dev_import_game(game: str, from_dir: Path) -> int:
    work_dir = ROOT_DIR / "workspace" / game
    batches_dir = work_dir / "batches"
    trans_dir = work_dir / "translated"
    if not from_dir.is_dir():
        print(f"[{game}] Import source directory does not exist: {from_dir}")
        return 1

    imported = 0
    for src_p in sorted(from_dir.rglob("*.output.jsonl")):
        rel = src_p.relative_to(from_dir)
        dst_p = trans_dir / rel
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        # Validate JSONL lines before importing
        for ln_no, line in enumerate(src_p.read_text(encoding="utf-8").splitlines(), start=1):
            s = line.strip()
            if not s:
                continue
            obj = json.loads(s)
            if "id" not in obj or not isinstance(obj.get("zh"), str):
                raise ValueError(f"Invalid output JSONL record at {src_p}:{ln_no}")
        shutil.copy2(src_p, dst_p)
        imported += 1

    # Report missing batches / entries
    in_files = sorted(batches_dir.rglob("*.input.jsonl")) if batches_dir.exists() else []
    missing_batches = []
    missing_entries = 0
    for in_f in in_files:
        rel = in_f.relative_to(batches_dir)
        out_f = trans_dir / rel.with_name(in_f.name.replace(".input.jsonl", ".output.jsonl"))
        if not out_f.is_file():
            missing_batches.append(rel.as_posix())
            continue
        in_ids = {json.loads(l)["id"] for l in in_f.read_text(encoding="utf-8").splitlines() if l.strip()}
        out_ids = {json.loads(l)["id"] for l in out_f.read_text(encoding="utf-8").splitlines() if l.strip()}
        missing_entries += len(in_ids - out_ids)

    print(
        f"[{game}] Imported {imported} output files. "
        f"Missing batches: {len(missing_batches)}, missing entries: {missing_entries}"
    )
    return 0


def cmd_dev(args) -> int:
    subcmd = args.dev_cmd
    targets = GAME_KEYS if args.game == "all" else [args.game]

    if subcmd == "extract":
        allow_legacy = getattr(args, "allow_legacy_backup", False)
        for g in targets:
            _run_m1_extractor(g, allow_legacy_backup=allow_legacy)
        return 0

    if subcmd == "prepare":
        for g in targets:
            rc = _dev_prepare_game(g)
            if rc != 0:
                return rc
        return 0

    if subcmd == "import":
        if len(targets) != 1:
            print("[DEV] `dev import` requires a single game.")
            return 1
        return _dev_import_game(targets[0], Path(args.from_dir))

    if subcmd == "qa":
        overall = 0
        for g in targets:
            rc = run_developer_qa(g, work_dir=ROOT_DIR / "workspace" / g)
            if rc != 0:
                overall = rc
        return overall

    if subcmd == "build":
        _sync_tool_roots()
        cfg = load_or_detect_games()
        if not getattr(args, "skip_qa", False):
            for g in targets:
                if run_developer_qa(g, work_dir=ROOT_DIR / "workspace" / g) != 0:
                    print(f"[{g}] Developer QA failed; aborting build.")
                    return 1
        for g in targets:
            build_game(g, cfg, getattr(args, "ttf", r"C:\Windows\Fonts\msjh.ttc"), skip_qa=True)
            verify_build_manifest(g, ROOT_DIR / "dist" / MOD_DIR_NAMES[g])
        return 0

    if subcmd == "install":
        return cmd_install(args)

    if subcmd == "export":
        out_base = Path(args.out_dir) if getattr(args, "out_dir", None) else (ROOT_DIR / "translations")
        for g in targets:
            manifest = export_translation_pack(g, ROOT_DIR / "workspace" / g, out_base / g)
            print(f"[{g}] Exported Translation Pack v{manifest['pack_format_version']}: {manifest['entry_count']} entries -> {out_base / g}")
        return 0

    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Batman: Arkham Series TW Localization CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("detect", help="Auto-detect Steam Arkham games")

    p_qa = sub.add_parser("qa", help="Run M4 QA Linter (Release Baseline)")
    p_qa.add_argument("game", choices=GAME_KEYS + ["all"])

    p_build = sub.add_parser("build", help="Build .int, font UPK, and Mod packages in dist/")
    p_build.add_argument("game", choices=GAME_KEYS + ["all"])
    p_build.add_argument("--ttf", default=r"C:\Windows\Fonts\msjh.ttc")
    p_build.add_argument("--skip-qa", action="store_true", help="Skip pre-build M4 QA Linter check")

    p_inst = sub.add_parser("install", help="Backup and install built Mod into Steam directory")
    p_inst.add_argument("game", choices=GAME_KEYS + ["all"])
    p_inst.add_argument("--force", action="store_true", help="Override external modification protection")

    p_uninst = sub.add_parser("uninstall", help="Restore backed-up original English files")
    p_uninst.add_argument("game", choices=GAME_KEYS + ["all"])
    p_uninst.add_argument("--force", action="store_true", help="Override external modification protection")

    # Phase 3: Player Mode
    p_player = sub.add_parser("player", help="Player Mode (install/update/status/uninstall using Translation Pack)")
    p_player_sub = p_player.add_subparsers(dest="player_cmd", required=True)

    pl_status = p_player_sub.add_parser("status", help="Check Steam detection, Mod state, and Translation Pack status")
    pl_status.add_argument("game", nargs="?", default="all", choices=GAME_KEYS + ["all"])
    pl_status.add_argument("--pack-dir", default=None)

    for pl_name in ("install", "update-pack"):
        pl_p = p_player_sub.add_parser(pl_name, help=f"Player {pl_name}: Detect -> Extract -> Match -> QA -> Build -> Install")
        pl_p.add_argument("game", nargs="?", default="all", choices=GAME_KEYS + ["all"])
        pl_p.add_argument("--ttf", default=r"C:\Windows\Fonts\msjh.ttc")
        pl_p.add_argument("--pack-dir", default=None)
        pl_p.add_argument("--force", action="store_true")
        pl_p.add_argument("--allow-legacy-backup", action="store_true")

    pl_uninst = p_player_sub.add_parser("uninstall", help="Restore original English files")
    pl_uninst.add_argument("game", choices=GAME_KEYS + ["all"])
    pl_uninst.add_argument("--force", action="store_true")

    # Phase 4: Developer Mode
    p_dev = sub.add_parser("dev", help="Developer Mode (M1-M5 workflow + Translation Pack export)")
    p_dev_sub = p_dev.add_subparsers(dest="dev_cmd", required=True)

    d_ext = p_dev_sub.add_parser("extract", help="Run M1 extraction using OriginalSourceResolver")
    d_ext.add_argument("game", choices=GAME_KEYS + ["all"])
    d_ext.add_argument("--allow-legacy-backup", action="store_true")

    d_prep = p_dev_sub.add_parser("prepare", help="Prepare M2 glossary/style prompt headers for batches")
    d_prep.add_argument("game", choices=GAME_KEYS + ["all"])

    d_imp = p_dev_sub.add_parser("import", help="Import translated *.output.jsonl files and report gaps")
    d_imp.add_argument("game", choices=GAME_KEYS)
    d_imp.add_argument("--from-dir", required=True)

    d_qa = p_dev_sub.add_parser("qa", help="Run Developer QA (dynamic local corpus inventory)")
    d_qa.add_argument("game", choices=GAME_KEYS + ["all"])

    d_bld = p_dev_sub.add_parser("build", help="Run Developer QA + M5 build + verify manifest")
    d_bld.add_argument("game", choices=GAME_KEYS + ["all"])
    d_bld.add_argument("--ttf", default=r"C:\Windows\Fonts\msjh.ttc")
    d_bld.add_argument("--skip-qa", action="store_true")

    d_inst = p_dev_sub.add_parser("install", help="Transactional install of built Mod")
    d_inst.add_argument("game", choices=GAME_KEYS + ["all"])
    d_inst.add_argument("--force", action="store_true")

    d_exp = p_dev_sub.add_parser("export", help="Export Player-compatible pure-Chinese Translation Pack")
    d_exp.add_argument("game", choices=GAME_KEYS + ["all"])
    d_exp.add_argument("--out-dir", default=None)

    args = parser.parse_args()
    if args.cmd == "detect":
        return cmd_detect(args)
    if args.cmd == "qa":
        return cmd_qa(args)
    if args.cmd == "build":
        return cmd_build(args)
    if args.cmd == "install":
        return cmd_install(args)
    if args.cmd == "uninstall":
        return cmd_uninstall(args)
    if args.cmd == "player":
        return cmd_player(args)
    if args.cmd == "dev":
        return cmd_dev(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
