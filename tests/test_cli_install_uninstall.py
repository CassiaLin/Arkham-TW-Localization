import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import arkham_mod_cli as cli


def _build_synthetic_mod(mod_dir: Path, game: str, files: dict):
    mod_dir.mkdir(parents=True, exist_ok=True)
    all_files = dict(files)
    if not any(k.lower().endswith(".int") for k in all_files):
        all_files["loc.int"] = b"INT_DATA"
    for rel_posix, data in all_files.items():
        p = mod_dir / Path(*rel_posix.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    cli.write_batch_installers(game, str(mod_dir.parent / "Game"))
    cli.write_build_manifest(game, mod_dir)


class TestReleaseHardeningInstallUninstall(unittest.TestCase):
    def test_01_first_install_failure_injection(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root, mod_dir = t / "Game", t / "Mod"
            (game_root / "a.upk").parent.mkdir(parents=True, exist_ok=True)
            (game_root / "a.upk").write_bytes(b"ORIG_A")
            (game_root / "b.upk").write_bytes(b"ORIG_B")
            _build_synthetic_mod(mod_dir, "city", {"a.upk": b"MOD_A", "b.upk": b"MOD_B", "new.int": b"NEW"})

            real_copy = cli.atomic_copy
            calls = {"n": 0}

            def fail_on_b(src, dst):
                calls["n"] += 1
                if src.parent == mod_dir and dst.name == "b.upk" and dst.parent == game_root:
                    raise OSError("Injected failure on b.upk")
                return real_copy(src, dst)

            with mock.patch.object(cli, "atomic_copy", side_effect=fail_on_b):
                with self.assertRaises(OSError):
                    cli.install_game_mod("city", game_root, mod_dir)

            self.assertEqual((game_root / "a.upk").read_bytes(), b"ORIG_A")
            self.assertEqual((game_root / "b.upk").read_bytes(), b"ORIG_B")
            self.assertFalse((game_root / "new.int").exists())
            self.assertFalse((game_root / "_Backup_TW_Mod").exists())

    def test_02_update_v1_to_v2_failure_restores_v1_and_orig_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root, mod_v1, mod_v2 = t / "Game", t / "ModV1", t / "ModV2"
            (game_root / "a.upk").parent.mkdir(parents=True, exist_ok=True)
            (game_root / "a.upk").write_bytes(b"ORIG_A")
            (game_root / "b.upk").write_bytes(b"ORIG_B")

            _build_synthetic_mod(mod_v1, "city", {"a.upk": b"V1_A", "b.upk": b"V1_B", "v1_only.int": b"V1_ONLY"})
            cli.install_game_mod("city", game_root, mod_v1)

            _build_synthetic_mod(mod_v2, "city", {"a.upk": b"V2_A", "b.upk": b"V2_B", "v2_only.int": b"V2_ONLY"})
            real_copy = cli.atomic_copy

            def fail_v2_b(src, dst):
                if src.parent == mod_v2 and dst.name == "b.upk" and dst.parent == game_root:
                    raise OSError("Injected V2 update failure")
                return real_copy(src, dst)

            with mock.patch.object(cli, "atomic_copy", side_effect=fail_v2_b):
                with self.assertRaises(OSError):
                    cli.install_game_mod("city", game_root, mod_v2)

            # V1 state fully restored, Original Backup preserved
            self.assertEqual((game_root / "a.upk").read_bytes(), b"V1_A")
            self.assertEqual((game_root / "b.upk").read_bytes(), b"V1_B")
            self.assertEqual((game_root / "v1_only.int").read_bytes(), b"V1_ONLY")
            self.assertFalse((game_root / "v2_only.int").exists())
            self.assertEqual((game_root / "_Backup_TW_Mod" / "a.upk").read_bytes(), b"ORIG_A")
            manifest = json.loads((game_root / "_Backup_TW_Mod" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["state"], "COMMITTED")

    def test_03_recovery_after_interrupted_applying_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root, mod_v1, mod_v2 = t / "Game", t / "ModV1", t / "ModV2"
            (game_root / "a.upk").parent.mkdir(parents=True, exist_ok=True)
            (game_root / "a.upk").write_bytes(b"ORIG_A")
            _build_synthetic_mod(mod_v1, "city", {"a.upk": b"V1_A"})
            cli.install_game_mod("city", game_root, mod_v1)

            # Simulate crash mid-APPLYING during V2 update with valid snapshot.json
            bkp_dir = game_root / "_Backup_TW_Mod"
            snap_dir = bkp_dir / "_tx_snapshot"
            (snap_dir / "files").mkdir(parents=True, exist_ok=True)
            (snap_dir / "files" / "a.upk").write_bytes(b"V1_A")
            v1_m = json.loads((bkp_dir / "manifest.json").read_text(encoding="utf-8"))
            snap_meta = {
                "version": 1,
                "v1_manifest": v1_m,
                "targets": {"a.upk": {"existed": True, "snap_rel": "files/a.upk", "sha256": cli.sha256_file(snap_dir / "files" / "a.upk")}},
                "ini_targets": [],
            }
            (snap_dir / "snapshot.json").write_text(json.dumps(snap_meta), encoding="utf-8")

            m = json.loads((bkp_dir / "manifest.json").read_text(encoding="utf-8"))
            m["state"] = "APPLYING"
            m["tx_snapshot_dir"] = "_tx_snapshot"
            (bkp_dir / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
            (game_root / "a.upk").write_bytes(b"PARTIAL_V2_A")

            _build_synthetic_mod(mod_v2, "city", {"a.upk": b"V2_A"})
            cli.install_game_mod("city", game_root, mod_v2)
            self.assertEqual((game_root / "a.upk").read_bytes(), b"V2_A")
            self.assertEqual((bkp_dir / "a.upk").read_bytes(), b"ORIG_A")

    def test_04_uninstall_aborts_after_external_steam_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root, mod_dir = t / "Game", t / "Mod"
            (game_root / "a.upk").parent.mkdir(parents=True, exist_ok=True)
            (game_root / "a.upk").write_bytes(b"ORIG_A")
            _build_synthetic_mod(mod_dir, "city", {"a.upk": b"MOD_A"})
            cli.install_game_mod("city", game_root, mod_dir)

            # Steam updates game file externally
            (game_root / "a.upk").write_bytes(b"STEAM_UPDATED_A")
            with self.assertRaises(cli.ExternalModificationError):
                cli.uninstall_game_mod("city", game_root)
            self.assertEqual((game_root / "a.upk").read_bytes(), b"STEAM_UPDATED_A")

    def test_05_reinstall_aborts_when_backup_corrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root, mod_dir = t / "Game", t / "Mod"
            (game_root / "a.upk").parent.mkdir(parents=True, exist_ok=True)
            (game_root / "a.upk").write_bytes(b"ORIG_A")
            _build_synthetic_mod(mod_dir, "city", {"a.upk": b"MOD_A"})
            cli.install_game_mod("city", game_root, mod_dir)

            # Corrupt backup file
            (game_root / "_Backup_TW_Mod" / "a.upk").write_bytes(b"CORRUPT")
            with self.assertRaises(cli.BackupIntegrityError):
                cli.install_game_mod("city", game_root, mod_dir)

    def test_06_ini_backup_corrupted_and_external_ini_edit_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root, mod_dir = t / "Game", t / "Mod"
            (game_root / "a.upk").parent.mkdir(parents=True, exist_ok=True)
            (game_root / "a.upk").write_bytes(b"ORIG_A")
            ini_p = game_root / "BmGame" / "Config" / "BmEngine.ini"
            ini_p.parent.mkdir(parents=True, exist_ok=True)
            ini_bytes = b"[Engine.Engine]\r\n; Caf\xe9\r\nbSubtitlesEnabled=False\r\n"
            ini_p.write_bytes(ini_bytes)

            _build_synthetic_mod(mod_dir, "asylum", {"a.upk": b"MOD_A"})
            cli.install_game_mod("asylum", game_root, mod_dir, ini_targets=[ini_p])

            # Player edits INI externally -> uninstall must refuse without --force
            ini_p.write_bytes(b"[Engine.Engine]\r\nbSubtitlesEnabled=True\r\nPlayerSetting=1\r\n")
            with self.assertRaises(cli.ExternalModificationError):
                cli.uninstall_game_mod("asylum", game_root)

            # Corrupt INI backup -> uninstall with force must still fail preflight
            bkp_ini = next((game_root / "_Backup_TW_Mod" / "_ini_backups").iterdir())
            bkp_ini.write_bytes(b"CORRUPT_INI")
            with self.assertRaises(cli.BackupIntegrityError):
                cli.uninstall_game_mod("asylum", game_root, force=True)

    def test_07_uninstall_refuses_delete_externally_modified_added_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root, mod_dir = t / "Game", t / "Mod"
            game_root.mkdir(parents=True, exist_ok=True)
            _build_synthetic_mod(mod_dir, "city", {"new.int": b"MOD_NEW"})
            cli.install_game_mod("city", game_root, mod_dir)

            (game_root / "new.int").write_bytes(b"USER_CUSTOMIZED_NEW")
            with self.assertRaises(cli.ExternalModificationError):
                cli.uninstall_game_mod("city", game_root)
            self.assertEqual((game_root / "new.int").read_bytes(), b"USER_CUSTOMIZED_NEW")

    def test_08_uninstall_interrupted_resumes_safely(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root, mod_dir = t / "Game", t / "Mod"
            (game_root / "a.upk").parent.mkdir(parents=True, exist_ok=True)
            (game_root / "a.upk").write_bytes(b"ORIG_A")
            _build_synthetic_mod(mod_dir, "city", {"a.upk": b"MOD_A", "new.int": b"MOD_NEW"})
            cli.install_game_mod("city", game_root, mod_dir)

            # Simulate interruption during UNINSTALLING after a.upk was restored
            bkp_dir = game_root / "_Backup_TW_Mod"
            m = json.loads((bkp_dir / "manifest.json").read_text(encoding="utf-8"))
            m["state"] = "UNINSTALLING"
            (bkp_dir / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
            (game_root / "a.upk").write_bytes(b"ORIG_A")

            ok = cli.uninstall_game_mod("city", game_root)
            self.assertTrue(ok)
            self.assertEqual((game_root / "a.upk").read_bytes(), b"ORIG_A")
            self.assertFalse((game_root / "new.int").exists())
            self.assertFalse(bkp_dir.exists())

    def test_11_failed_or_incomplete_build_cannot_be_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root, mod_dir = t / "Game", t / "Mod"
            game_root.mkdir(parents=True, exist_ok=True)
            mod_dir.mkdir(parents=True, exist_ok=True)
            (mod_dir / "partial.int").write_bytes(b"PARTIAL")
            with self.assertRaises(cli.BuildIntegrityError):
                cli.install_game_mod("city", game_root, mod_dir)

    def test_13_cli_and_bat_failure_exit_codes(self):
        # FINAL-09: CLI failure on missing/unbuilt game must strictly return non-zero
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            fake_cfg = t / "game_paths.json"
            fake_cfg.write_text("{}", encoding="utf-8")
            with mock.patch.object(cli, "ROOT_DIR", t):
                # config/game_paths.json missing -> load_or_detect_games or empty cfg
                (t / "config").mkdir()
                (t / "config" / "game_paths.json").write_text("{}", encoding="utf-8")
                rc = cli.cmd_install(mock.Mock(game="city", force=False))
                self.assertNotEqual(rc, 0)

            mod_dir = t / "Mod"
            _build_synthetic_mod(mod_dir, "city", {"a.upk": b"MOD_A", "loc.int": b"INT"})
            (mod_dir / "build_manifest.json").unlink()
            if os.name == "nt":
                env = os.environ.copy()
                env["ARKHAM_CLI_NO_PAUSE"] = "1"
                bat_proc = subprocess.run(
                    ["cmd", "/c", str(mod_dir / "install_mod.bat")],
                    cwd=str(ROOT_DIR),
                    env=env,
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(bat_proc.returncode, 0)

    def test_final_01_commit_before_snapshot_cleanup_and_missing_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root, mod_v1, mod_v2 = t / "Game", t / "ModV1", t / "ModV2"
            (game_root / "a.upk").parent.mkdir(parents=True, exist_ok=True)
            (game_root / "a.upk").write_bytes(b"ORIG_A")
            _build_synthetic_mod(mod_v1, "city", {"a.upk": b"V1_A", "loc.int": b"V1"})
            cli.install_game_mod("city", game_root, mod_v1)

            _build_synthetic_mod(mod_v2, "city", {"a.upk": b"V2_A", "loc.int": b"V2"})
            real_write_json = cli.atomic_write_json

            def fail_on_committed(path, data):
                if path.name == "manifest.json" and data.get("state") == "COMMITTED":
                    raise OSError("Injected failure writing COMMITTED manifest")
                return real_write_json(path, data)

            with mock.patch.object(cli, "atomic_write_json", side_effect=fail_on_committed):
                with self.assertRaises(OSError):
                    cli.install_game_mod("city", game_root, mod_v2)

            # Snapshot MUST still exist because COMMITTED failed
            snap_dir = game_root / "_Backup_TW_Mod" / "_tx_snapshot"
            self.assertTrue(snap_dir.exists())

            # Deleting snapshot must abort recovery (never fall through to first-install)
            import shutil
            shutil.rmtree(snap_dir)
            with self.assertRaises(cli.BackupIntegrityError):
                cli.install_game_mod("city", game_root, mod_v2)

    def test_final_02_subprocess_kill_recovers_lock_and_living_lock_blocks(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root = t / "Game"
            game_root.mkdir(parents=True, exist_ok=True)

            # Spawn a child subprocess that acquires _game_lock and waits
            child_code = (
                "import sys, time\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(ROOT_DIR)!r})\n"
                "import arkham_mod_cli as cli\n"
                f"with cli._game_lock(Path({str(game_root)!r})):\n"
                "    print('LOCKED', flush=True)\n"
                "    time.sleep(30)\n"
            )
            proc = subprocess.Popen(
                [sys.executable, "-c", child_code],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                ready = proc.stdout.readline().strip()
                self.assertEqual(ready, "LOCKED")
                # Living process must block a second lock attempt
                with self.assertRaises(cli.TransactionLockError):
                    with cli._game_lock(game_root):
                        pass
                # Force kill child process
                proc.kill()
                proc.wait(timeout=5)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdout:
                    proc.stdout.close()
                if proc.stderr:
                    proc.stderr.close()

            # After force-kill, OS kernel releases lock -> next process acquires cleanly
            with cli._game_lock(game_root):
                pass

    def test_final_03_ini_destination_validation_blocks_external_and_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root = t / "Game"
            outside = t / "outside.ini"
            outside.write_text("SECRET=1\n", encoding="utf-8")
            bkp_dir = game_root / "_Backup_TW_Mod"
            (bkp_dir / "_ini_backups").mkdir(parents=True, exist_ok=True)
            (bkp_dir / "_ini_backups" / "0_outside.ini").write_bytes(b"EVIL")

            evil_manifest = {
                "version": 3,
                "game": "asylum",
                "state": "COMMITTED",
                "backup_files": {},
                "added_files": {},
                "ini_backups": [{
                    "target": str(outside),
                    "backup_rel": "_ini_backups/0_outside.ini",
                    "sha256_orig": cli.sha256_file(bkp_dir / "_ini_backups" / "0_outside.ini"),
                }],
            }
            (bkp_dir / "manifest.json").write_text(json.dumps(evil_manifest), encoding="utf-8")
            with self.assertRaises(cli.ManifestValidationError):
                cli.uninstall_game_mod("asylum", game_root, force=True)
            self.assertEqual(outside.read_text(encoding="utf-8"), "SECRET=1\n")

            # Non-asylum game carrying ini_backups must be rejected
            evil_manifest["game"] = "city"
            (bkp_dir / "manifest.json").write_text(json.dumps(evil_manifest), encoding="utf-8")
            with self.assertRaises(cli.ManifestValidationError):
                cli.uninstall_game_mod("city", game_root, force=True)

            # Symlink INI inside game_root pointing outside must be rejected (when OS allows symlinks)
            sym_ini = game_root / "BmGame" / "Config" / "link.ini"
            sym_ini.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.symlink(outside, sym_ini)
            except (OSError, NotImplementedError):
                pass
            else:
                with self.assertRaises(cli.ManifestValidationError):
                    cli._validate_ini_target("asylum", game_root, str(sym_ini))

    def test_final_04_complete_snapshot_new_target_new_ini_corrupt_snap_and_uninstall_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root, mod_v1, mod_v2 = t / "Game", t / "ModV1", t / "ModV2"
            (game_root / "a.upk").parent.mkdir(parents=True, exist_ok=True)
            (game_root / "a.upk").write_bytes(b"ORIG_A")
            ini_p = game_root / "BmGame" / "Config" / "BmEngine.ini"
            ini_p.parent.mkdir(parents=True, exist_ok=True)
            # In V1, bSubtitlesEnabled is already True so V1 didn't touch INI
            ini_p.write_bytes(b"[Engine.Engine]\r\nbSubtitlesEnabled=True\r\n")

            _build_synthetic_mod(mod_v1, "asylum", {"a.upk": b"V1_A", "loc.int": b"V1"})
            cli.install_game_mod("asylum", game_root, mod_v1, ini_targets=[ini_p])

            # Before V2, player/game has bSubtitlesEnabled=False and brand_new.int does NOT exist
            ini_v1_bytes = b"[Engine.Engine]\r\nbSubtitlesEnabled=False\r\n"
            ini_p.write_bytes(ini_v1_bytes)
            _build_synthetic_mod(mod_v2, "asylum", {"a.upk": b"V2_A", "loc.int": b"V2", "brand_new.int": b"NEW"})

            real_write_bytes = cli.atomic_write_bytes

            def fail_on_ini(dst, raw):
                if dst == ini_p:
                    raise OSError("Injected failure on V2 first-time INI write")
                return real_write_bytes(dst, raw)

            with mock.patch.object(cli, "atomic_write_bytes", side_effect=fail_on_ini):
                with self.assertRaises(OSError):
                    cli.install_game_mod("asylum", game_root, mod_v2, ini_targets=[ini_p])

            # brand_new.int (which did not exist before V2) must be removed; a.upk & INI restored to pre-V2!
            self.assertFalse((game_root / "brand_new.int").exists())
            self.assertEqual((game_root / "a.upk").read_bytes(), b"V1_A")
            self.assertEqual(ini_p.read_bytes(), ini_v1_bytes)

            # Corrupt snapshot during interrupted APPLYING must fail closed
            bkp_dir = game_root / "_Backup_TW_Mod"
            snap_dir = bkp_dir / "_tx_snapshot"
            (snap_dir / "files").mkdir(parents=True, exist_ok=True)
            (snap_dir / "files" / "a.upk").write_bytes(b"TAMPERED")
            snap_meta = {
                "version": 1,
                "v1_manifest": json.loads((bkp_dir / "manifest.json").read_text(encoding="utf-8")),
                "targets": {"a.upk": {"existed": True, "snap_rel": "files/a.upk", "sha256": "0" * 64}},
                "ini_targets": [],
            }
            (snap_dir / "snapshot.json").write_text(json.dumps(snap_meta), encoding="utf-8")
            m = json.loads((bkp_dir / "manifest.json").read_text(encoding="utf-8"))
            m["state"] = "APPLYING"
            m["tx_snapshot_dir"] = "_tx_snapshot"
            (bkp_dir / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
            with self.assertRaises(cli.BackupIntegrityError):
                cli.uninstall_game_mod("asylum", game_root)
            self.assertEqual((bkp_dir / "a.upk").read_bytes(), b"ORIG_A")

            # Valid snapshot during interrupted APPLYING + uninstall must roll back to V1 AND finish uninstall!
            (snap_dir / "files" / "a.upk").write_bytes(b"V1_A")
            snap_meta["targets"]["a.upk"]["sha256"] = cli.sha256_file(snap_dir / "files" / "a.upk")
            (snap_dir / "snapshot.json").write_text(json.dumps(snap_meta), encoding="utf-8")
            ok = cli.uninstall_game_mod("asylum", game_root)
            self.assertTrue(ok)
            self.assertEqual((game_root / "a.upk").read_bytes(), b"ORIG_A")
            self.assertFalse((game_root / "loc.int").exists())
            self.assertFalse(bkp_dir.exists())

    def test_14_manifest_path_traversal_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            game_root = t / "Game"
            bkp_dir = game_root / "_Backup_TW_Mod"
            bkp_dir.mkdir(parents=True, exist_ok=True)
            evil_manifest = {
                "version": 3,
                "game": "city",
                "state": "COMMITTED",
                "backup_files": {"../../evil.txt": {"sha256_orig": "abc", "sha256_mod": "def"}},
                "added_files": {},
                "ini_backups": [],
            }
            (bkp_dir / "manifest.json").write_text(json.dumps(evil_manifest), encoding="utf-8")
            with self.assertRaises(cli.ManifestValidationError):
                cli.uninstall_game_mod("city", game_root)


if __name__ == "__main__":
    unittest.main()
