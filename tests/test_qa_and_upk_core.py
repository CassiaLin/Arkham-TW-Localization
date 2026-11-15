import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "tools"))

import arkham_mod_cli as cli
import m4_qa_linter as qa
from m5_font_builder import patch_asylum_exe_line_height


import os

class TestReleaseHardeningQaAndBuild(unittest.TestCase):
    @unittest.skipIf(
        os.environ.get("ARKHAM_PUBLIC_CI") == "1"
        or not (ROOT_DIR / "workspace" / "asylum" / "batches").is_dir(),
        "Private release corpus not present in Public CI",
    )
    def test_m4_qa_linter_all_games_and_corpus_inventory(self):
        for game in ("asylum", "city", "origins", "knight"):
            res = subprocess.run(
                [sys.executable, str(ROOT_DIR / "tools" / "m4_qa_linter.py"), game],
                cwd=str(ROOT_DIR),
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0, f"QA failed for {game}:\n{res.stdout}\n{res.stderr}")

    def test_09_missing_corpus_batch_fails_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            wdir = Path(tmp)
            (wdir / "batches" / "01").mkdir(parents=True)
            (wdir / "translated" / "01").mkdir(parents=True)
            (wdir / "batches" / "01" / "batch_001.input.jsonl").write_text(
                json.dumps({"id": "ID1", "en": "Hello"}) + "\n", encoding="utf-8"
            )
            (wdir / "translated" / "01" / "batch_001.output.jsonl").write_text(
                json.dumps({"id": "ID1", "zh": "你好"}) + "\n", encoding="utf-8"
            )
            # Expecting 2 batches when only 1 exists must fail
            rc = qa.run_qa("asylum", work_dir=wdir, expected_inventory={"batches": 2, "entries": 1})
            self.assertNotEqual(rc, 0)

            # FINAL-05: Missing metadata/id_map.json when inventory is checked must fail
            rc_no_idmap = qa.run_qa("asylum", work_dir=wdir, expected_inventory={"batches": 1, "entries": 1})
            self.assertNotEqual(rc_no_idmap, 0)

    def test_10_qa_failure_blocks_build_and_preserves_previous_dist(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            fake_mod = t / "dist" / cli.MOD_DIR_NAMES["city"]
            fake_mod.mkdir(parents=True, exist_ok=True)
            (fake_mod / "prev.int").write_text("PREV_VALID", encoding="utf-8")

            def fail_qa(*_args, **_kwargs):
                raise subprocess.CalledProcessError(1, ["m4_qa_linter.py", "city"])

            with mock.patch.object(cli, "ROOT_DIR", t), \
                 mock.patch.object(cli.subprocess, "run", side_effect=fail_qa):
                with self.assertRaises(subprocess.CalledProcessError):
                    cli.build_game("city", {}, r"C:\Windows\Fonts\msjh.ttc", skip_qa=False)
            # FINAL-06: Previous valid dist preserved on failed build
            self.assertEqual((fake_mod / "prev.int").read_text(encoding="utf-8"), "PREV_VALID")

    def test_final_06_07_preserve_prev_build_on_mid_failure_and_reject_empty_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            mod_dir = t / "dist" / cli.MOD_DIR_NAMES["city"]
            mod_dir.mkdir(parents=True, exist_ok=True)
            (mod_dir / "old.int").write_text("OLD_VALID", encoding="utf-8")

            # Empty build manifest must be rejected (FINAL-07)
            (mod_dir / "build_manifest.json").write_text(
                json.dumps({"version": 1, "game": "city", "completed": True, "files": {}}),
                encoding="utf-8",
            )
            with self.assertRaises(cli.BuildIntegrityError):
                cli.verify_build_manifest("city", mod_dir)

            # Mid-build failure restores previous valid mod_dir (FINAL-06)
            with mock.patch.object(cli, "ROOT_DIR", t), \
                 mock.patch.object(cli, "repack_game_int", side_effect=RuntimeError("Repack crashed")):
                with self.assertRaises(RuntimeError):
                    cli.build_game("city", {}, r"C:\Windows\Fonts\msjh.ttc", skip_qa=True)
            self.assertEqual((mod_dir / "old.int").read_text(encoding="utf-8"), "OLD_VALID")

    def test_12_asylum_build_exe_patch_integration_and_unsupported_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            t = Path(tmp)
            fake_root = t / "AsylumGame"
            fake_dist = t / "dist"
            mod_dir = fake_dist / cli.MOD_DIR_NAMES["asylum"]
            exe_src = fake_root / "Binaries" / "ShippingPC-BmGame.exe"
            exe_src.parent.mkdir(parents=True, exist_ok=True)

            buf = bytearray(0x00945900)
            ctx1 = bytes.fromhex("33c0817c2404200300000f9dc0")
            ctx2 = bytes.fromhex("33c081fa200300000f9dc0685c575d0257")
            old_instr = bytes.fromhex("8d04c512000000")
            buf[0x009457ED - len(ctx1):0x009457ED] = ctx1
            buf[0x009457ED:0x009457ED + 7] = old_instr
            buf[0x00945891 - len(ctx2):0x00945891] = ctx2
            buf[0x00945891:0x00945891 + 7] = old_instr
            exe_src.write_bytes(bytes(buf))

            startup_src = fake_root / "BmGame" / "CookedPC" / "Startup_INT.upk"
            startup_src.parent.mkdir(parents=True, exist_ok=True)
            startup_src.write_bytes(b"FAKE_STARTUP")

            cfg = {"asylum": {"root_dir": str(fake_root), "startup_upk": str(startup_src)}}

            def fake_repack_int(_g):
                int_p = mod_dir / "BmGame" / "Localization" / "INT" / "Frontend.int"
                int_p.parent.mkdir(parents=True, exist_ok=True)
                int_p.write_bytes(b"FAKE_INT")

            def fake_patch_startup(src, dst, ttf_path, cps):
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(b"PATCHED_STARTUP")
                exe_dst = dst.parent.parent.parent / "Binaries" / "ShippingPC-BmGame.exe"
                patch_asylum_exe_line_height(exe_src, exe_dst, line_height=60)

            with mock.patch.object(cli, "ROOT_DIR", t), \
                 mock.patch.object(cli, "repack_game_int", side_effect=fake_repack_int), \
                 mock.patch.object(cli, "repack_asylum_upks", return_value=None), \
                 mock.patch.object(cli, "collect_charset", return_value=[]), \
                 mock.patch.object(cli, "patch_asylum_startup_upk", side_effect=fake_patch_startup):
                cli.build_game("asylum", cfg, r"C:\Windows\Fonts\msjh.ttc", skip_qa=True)

            bm = json.loads((mod_dir / "build_manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(bm["completed"])
            self.assertIn("Binaries/ShippingPC-BmGame.exe", bm["files"])
            self.assertEqual(
                (mod_dir / "Binaries" / "ShippingPC-BmGame.exe").read_bytes()[0x009457ED:0x009457ED + 7],
                bytes.fromhex("6bc02a83c01290"),
            )

            # Unsupported EXE version on fresh build must fail and leave NO partial mod_dir
            import shutil
            shutil.rmtree(mod_dir)
            exe_src.write_bytes(b"\x00" * 0x00945900)
            with mock.patch.object(cli, "ROOT_DIR", t), \
                 mock.patch.object(cli, "repack_game_int", side_effect=fake_repack_int), \
                 mock.patch.object(cli, "repack_asylum_upks", return_value=None), \
                 mock.patch.object(cli, "collect_charset", return_value=[]), \
                 mock.patch.object(cli, "patch_asylum_startup_upk", side_effect=fake_patch_startup):
                with self.assertRaises(RuntimeError):
                    cli.build_game("asylum", cfg, r"C:\Windows\Fonts\msjh.ttc", skip_qa=True)
            self.assertFalse(mod_dir.exists())


if __name__ == "__main__":
    unittest.main()
