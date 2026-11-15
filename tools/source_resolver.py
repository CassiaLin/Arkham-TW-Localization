"""
Phase 2 — Original Source Resolver:
Provides a verified, read-only view of original game files across:
  1. Unmodified Steam vanilla game directory.
  2. Mod-installed game directory with a verified Original Backup (_Backup_TW_Mod/manifest.json).
  3. Read-only overlay combining verified Original Backup (for modified files)
     and unmodified live files, while excluding Mod added_files and _Backup_TW_Mod.
"""
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


class SourceResolutionError(RuntimeError):
    """Raised when original game sources cannot be safely resolved."""


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


class OriginalSourceResolver:
    """
    Read-only resolver for original game files.
    Never mutates the live game directory or _Backup_TW_Mod.
    """

    def __init__(
        self,
        game: str,
        game_root: Path,
        allow_legacy_backup: bool = False,
    ):
        self.game = game
        self.game_root = Path(game_root)
        self.backup_dir = self.game_root / "_Backup_TW_Mod"
        self.manifest_path = self.backup_dir / "manifest.json"
        self.lock_path = self.game_root / ".tw_mod.lock"
        self.allow_legacy_backup = allow_legacy_backup

        self.mode: str = "vanilla"  # vanilla | backup_overlay | legacy_backup_overlay
        self.backup_rels: Set[str] = set()
        self.added_rels: Set[str] = set()
        self.manifest: Optional[Dict[str, Any]] = None

        self._validate_and_init()

    def _validate_and_init(self) -> None:
        if not self.game_root.is_dir():
            raise SourceResolutionError(
                f"[{self.game}] Game directory does not exist: {self.game_root}"
            )

        # Check active OS-managed transaction lock (do not parse as JSON or delete existing lock file)
        if self.lock_path.exists():
            import arkham_mod_cli
            try:
                with arkham_mod_cli._game_lock(self.game_root):
                    pass
            except arkham_mod_cli.TransactionLockError as exc:
                raise SourceResolutionError(
                    f"[{self.game}] Another install/uninstall transaction is active in {self.game_root}. "
                    "Wait for it to finish before extracting or building."
                ) from exc

        if not self.backup_dir.exists():
            self.mode = "vanilla"
            return

        if not self.manifest_path.is_file():
            # Check if _Backup_TW_Mod is empty (e.g. after clean uninstall)
            has_files = any(p.is_file() for p in self.backup_dir.rglob("*"))
            if not has_files:
                self.mode = "vanilla"
                return
            if not self.allow_legacy_backup:
                raise SourceResolutionError(
                    f"[{self.game}] Found _Backup_TW_Mod without manifest.json at {self.backup_dir}. "
                    "Cannot verify original backup integrity. Run Steam 'Verify integrity of game files' "
                    "to restore clean vanilla files before running Player Mode."
                )
            self.mode = "legacy_backup_overlay"
            for p in self.backup_dir.rglob("*"):
                if p.is_file():
                    self.backup_rels.add(p.relative_to(self.backup_dir).as_posix().lower())
            return

        # Full manifest validation reusing arkham_mod_cli helpers
        import arkham_mod_cli

        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            manifest = arkham_mod_cli._validate_and_migrate_manifest(
                raw, self.game, self.game_root, self.backup_dir
            )
        except Exception as exc:
            raise SourceResolutionError(
                f"[{self.game}] Backup manifest validation failed: {exc}. "
                "Verify game files via Steam or recover the mod installation."
            ) from exc

        state = manifest.get("state")
        if state == "UNINSTALLED":
            self.mode = "vanilla"
            return
        if state != "COMMITTED":
            raise SourceResolutionError(
                f"[{self.game}] Unfinished mod transaction detected (state={state!r}). "
                f"Run `python arkham_mod_cli.py uninstall {self.game}` or re-run `install {self.game}` "
                "to recover before extracting original sources."
            )

        try:
            arkham_mod_cli._verify_backup_preflight(self.game, self.backup_dir, manifest)
            arkham_mod_cli._check_targets_not_externally_modified(
                self.game, self.game_root, manifest, force=False
            )
        except Exception as exc:
            raise SourceResolutionError(
                f"[{self.game}] Source integrity check failed: {exc}"
            ) from exc

        self.manifest = manifest
        self.mode = "backup_overlay"
        self.backup_rels = {rel.lower() for rel in manifest["backup_files"].keys()}
        self.added_rels = {rel.lower() for rel in manifest["added_files"].keys()}

    def is_added_mod_file(self, rel_posix: str) -> bool:
        return rel_posix.replace("\\", "/").lstrip("/").lower() in self.added_rels

    def resolve_file(self, rel_path: str) -> Optional[Path]:
        """
        Resolve a relative path (from game_root) to its verified original source file.
        Returns None if the path is a Mod-added file or does not exist.
        """
        rel_posix = rel_path.replace("\\", "/").lstrip("/")
        rel_low = rel_posix.lower()
        if rel_low in self.added_rels:
            return None
        if "_backup_tw_mod" in Path(rel_posix).parts or "_Backup_TW_Mod" in Path(rel_posix).parts:
            return None

        bkp_candidate = self.backup_dir / rel_posix
        if self.mode in ("backup_overlay", "legacy_backup_overlay"):
            if rel_low in self.backup_rels and bkp_candidate.is_file():
                return bkp_candidate

        live_candidate = self.game_root / rel_posix
        if live_candidate.is_file():
            return live_candidate
        if bkp_candidate.is_file() and self.mode in ("backup_overlay", "legacy_backup_overlay"):
            return bkp_candidate
        return None

    def iter_dir_files(
        self,
        rel_dir: str,
        pattern: str = "*",
        recursive: bool = False,
    ) -> List[Tuple[str, Path]]:
        """
        Return sorted list of (rel_posix_from_game_root, resolved_source_path)
        for files matching `pattern` under `rel_dir`, preferring verified backups
        and excluding Mod added_files and _Backup_TW_Mod.
        """
        rel_dir_clean = rel_dir.replace("\\", "/").strip("/")
        seen: Dict[str, Tuple[str, Path]] = {}

        bases: List[Tuple[Path, bool]] = [(self.game_root / rel_dir_clean, False)]
        if self.mode in ("backup_overlay", "legacy_backup_overlay"):
            bases.append((self.backup_dir / rel_dir_clean, True))

        for base_dir, is_bkp in bases:
            if not base_dir.is_dir():
                continue
            it = base_dir.rglob(pattern) if recursive else base_dir.glob(pattern)
            for p in it:
                if not p.is_file():
                    continue
                if not is_bkp and "_Backup_TW_Mod" in p.parts:
                    continue
                rel_from_root = (
                    p.relative_to(self.backup_dir if is_bkp else self.game_root)
                    .as_posix()
                )
                if self.is_added_mod_file(rel_from_root):
                    continue
                key = rel_from_root.lower()
                # Prefer backup over live when both exist in overlay mode
                if is_bkp or key not in seen:
                    seen[key] = (rel_from_root, p)

        return sorted(seen.values(), key=lambda kv: kv[0])


def resolve_game_source(
    game: str,
    game_root: Path,
    allow_legacy_backup: bool = False,
) -> OriginalSourceResolver:
    """Create and validate an OriginalSourceResolver for `game`."""
    return OriginalSourceResolver(game, game_root, allow_legacy_backup=allow_legacy_backup)
