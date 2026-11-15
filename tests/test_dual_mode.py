import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "tools"))

import arkham_mod_cli
from translation_pack import (
    TranslationPackError,
    export_translation_pack,
    import_translation_pack,
    load_aux_lookup,
    sha256_file,
    verify_translation_pack,
)
from source_resolver import OriginalSourceResolver, SourceResolutionError
from m4_qa_linter import run_developer_qa, run_player_qa
from package_public import PublicAuditError, audit_public_release, build_public_release


def _make_sample_workspace(work_dir: Path, game: str = "asylum", include_extras: bool = False):
    (work_dir / "metadata").mkdir(parents=True, exist_ok=True)
    (work_dir / "translated").mkdir(parents=True, exist_ok=True)
    (work_dir / "batches" / "01_ui_system").mkdir(parents=True, exist_ok=True)
    (work_dir / "batches" / "02_story_dialogue").mkdir(parents=True, exist_ok=True)

    id_map = {
        "UI_0001": {
            "id": "UI_0001",
            "category": "01_ui_system",
            "sp": "",
            "en_raw": "Close",
            "en_clean": "Close",
            "tags": [],
            "ctx": "",
            "sources": [],
        },
        "ST_0001": {
            "id": "ST_0001",
            "category": "02_story_dialogue",
            "sp": "Batman",
            "en_raw": "Close",
            "en_clean": "Close",
            "tags": [],
            "ctx": "",
            "sources": [],
        },
        "ST_0002": {
            "id": "ST_0002",
            "category": "02_story_dialogue",
            "sp": "Oracle",
            "en_raw": "Press <font>A</font> now.",
            "en_clean": "Press {0}A{1} now.",
            "tags": ["<font>", "</font>"],
            "ctx": "",
            "sources": [],
        },
    }
    (work_dir / "metadata" / "id_map.json").write_text(json.dumps(id_map, indent=2), encoding="utf-8")

    (work_dir / "batches" / "01_ui_system" / "batch_001.input.jsonl").write_text(
        json.dumps({"id": "UI_0001", "en": "Close"}) + "\n", encoding="utf-8"
    )
    (work_dir / "batches" / "02_story_dialogue" / "batch_001.input.jsonl").write_text(
        "\n".join([
            json.dumps({"id": "ST_0001", "en": "Close"}),
            json.dumps({"id": "ST_0002", "en": "Press {0}A{1} now."}),
        ]) + "\n",
        encoding="utf-8",
    )

    (work_dir / "translated" / "01_ui_system.output.jsonl").write_text(
        json.dumps({"id": "UI_0001", "zh": "關閉"}, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (work_dir / "translated" / "02_story_dialogue.output.jsonl").write_text(
        "\n".join([
            json.dumps({"id": "ST_0001", "zh": "靠近"}, ensure_ascii=False),
            json.dumps({"id": "ST_0002", "zh": "立即按下 {0}A{1}。"}, ensure_ascii=False),
        ]) + "\n",
        encoding="utf-8",
    )

    if include_extras:
        (work_dir / "usm_zh.json").write_text(
            json.dumps({"Intro line": "開場白"}, ensure_ascii=False), encoding="utf-8"
        )
        (work_dir / "upk_extra_zh.json").write_text(
            json.dumps({"Extra cue": "額外字幕"}, ensure_ascii=False), encoding="utf-8"
        )
        (work_dir / "ak_en_zh.json").write_text(
            json.dumps({"Knight line": "騎士台詞"}, ensure_ascii=False), encoding="utf-8"
        )
        (work_dir / "ak_events_zh.json").write_text(
            json.dumps({"Ace.upk|100": "事件台詞"}, ensure_ascii=False), encoding="utf-8"
        )
        (work_dir / "metadata" / "upk_list.json").write_text(
            json.dumps(["Ace.upk"]), encoding="utf-8"
        )


class TestDualModeIntegration(unittest.TestCase):
    def test_translation_pack_round_trip_and_aux_lookups(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            src_work = tmp_path / "src_work"
            _make_sample_workspace(src_work, game="knight", include_extras=True)
            pack_dir = tmp_path / "pack"

            manifest = export_translation_pack("knight", src_work, pack_dir)
            self.assertEqual(manifest["entry_count"], 3)
            self.assertEqual(manifest["auxiliary_counts"]["usm_sha256_zh.json"], 1)
            self.assertEqual(manifest["auxiliary_counts"]["ak_en_sha256_zh.json"], 1)
            self.assertEqual(manifest["auxiliary_counts"]["ak_events_zh.json"], 1)
            self.assertEqual(manifest["auxiliary_counts"]["upk_list.json"], 1)

            dst_work = tmp_path / "dst_work"
            (dst_work / "metadata").mkdir(parents=True)
            local_id_map = {
                "K_UI_0999": {
                    "id": "K_UI_0999",
                    "category": "01_ui_system",
                    "en_raw": "Close",
                    "en_clean": "Close",
                    "tags": [],
                },
                "K_ST_0500": {
                    "id": "K_ST_0500",
                    "category": "02_story_dialogue",
                    "en_raw": "Close",
                    "en_clean": "Close",
                    "tags": [],
                },
                "K_ST_0501": {
                    "id": "K_ST_0501",
                    "category": "02_story_dialogue",
                    "en_raw": "Press <font>A</font> now.",
                    "en_clean": "Press {0}A{1} now.",
                    "tags": ["<font>", "</font>"],
                },
            }
            (dst_work / "metadata" / "id_map.json").write_text(json.dumps(local_id_map), encoding="utf-8")

            report = import_translation_pack("knight", pack_dir, dst_work, strict=True)
            self.assertEqual(report["matched_count"], 3)
            self.assertEqual(report["unmatched_local_count"], 0)
            self.assertEqual(report["tag_mismatch_count"], 0)

            out_lines = [
                json.loads(l)
                for l in (dst_work / "translated" / "pack_matched.output.jsonl").read_text(encoding="utf-8").splitlines()
                if l.strip()
            ]
            by_id = {x["id"]: x["zh"] for x in out_lines}
            self.assertEqual(by_id["K_UI_0999"], "關閉")
            self.assertEqual(by_id["K_ST_0500"], "靠近")
            self.assertEqual(by_id["K_ST_0501"], "立即按下 {0}A{1}。")

            usm_lookup = load_aux_lookup(dst_work, "usm_zh.json", "usm_sha256_zh.json")
            self.assertIsNotNone(usm_lookup)
            self.assertEqual(usm_lookup.get("Intro line"), "開場白")
            ak_lookup = load_aux_lookup(dst_work, "ak_en_zh.json", "ak_en_sha256_zh.json")
            self.assertIsNotNone(ak_lookup)
            self.assertEqual(ak_lookup.get("Knight line"), "騎士台詞")

            self.assertEqual(run_player_qa("knight", dst_work, pack_dir), 0)

    def test_four_game_exported_packs_verify_if_present(self):
        expected = {"asylum": 6141, "city": 13167, "origins": 40651, "knight": 10201}
        for game, count in expected.items():
            pack_dir = ROOT_DIR / "translations" / game
            if not (pack_dir / "manifest.json").is_file():
                self.skipTest(f"Exported pack not present for {game}")
            manifest = verify_translation_pack(game, pack_dir)
            self.assertEqual(manifest["entry_count"], count)

    def test_pack_rejects_modified_source_corrupted_hash_and_tag_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            src_work = tmp_path / "src"
            _make_sample_workspace(src_work)
            pack_dir = tmp_path / "pack"
            export_translation_pack("asylum", src_work, pack_dir)

            dst_work = tmp_path / "dst1"
            (dst_work / "metadata").mkdir(parents=True)
            (dst_work / "metadata" / "id_map.json").write_text(
                json.dumps({
                    "UI_0001": {
                        "id": "UI_0001",
                        "category": "01_ui_system",
                        "en_raw": "Close Window",
                        "en_clean": "Close Window",
                        "tags": [],
                    }
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(TranslationPackError, "matching failed"):
                import_translation_pack("asylum", pack_dir, dst_work, strict=True)

            dst_work2 = tmp_path / "dst2"
            (dst_work2 / "metadata").mkdir(parents=True)
            pack_bad_tag = tmp_path / "pack_bad_tag"
            export_translation_pack("asylum", src_work, pack_bad_tag)
            zh_p = pack_bad_tag / "zh_tw.jsonl"
            recs = [json.loads(l) for l in zh_p.read_text(encoding="utf-8").splitlines() if l.strip()]
            recs[2]["zh"] = "缺少佔位符"
            recs[2]["tag_count"] = 0
            zh_p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
            man_p = pack_bad_tag / "manifest.json"
            man = json.loads(man_p.read_text(encoding="utf-8"))
            man["files"]["zh_tw.jsonl"]["sha256"] = sha256_file(zh_p)
            man_p.write_text(json.dumps(man, indent=2), encoding="utf-8")

            (dst_work2 / "metadata" / "id_map.json").write_text(
                (src_work / "metadata" / "id_map.json").read_text(encoding="utf-8"), encoding="utf-8"
            )
            with self.assertRaisesRegex(TranslationPackError, "matching failed|Tag"):
                import_translation_pack("asylum", pack_bad_tag, dst_work2, strict=True)

            (pack_dir / "zh_tw.jsonl").write_text("corrupted\n", encoding="utf-8")
            with self.assertRaisesRegex(TranslationPackError, "corrupted"):
                verify_translation_pack("asylum", pack_dir)

    def test_developer_qa_dynamic_inventory(self):
        with tempfile.TemporaryDirectory() as td:
            work_dir = Path(td) / "dev_work"
            _make_sample_workspace(work_dir)
            (work_dir / "translated" / "01_ui_system.output.jsonl").unlink()
            (work_dir / "translated" / "02_story_dialogue.output.jsonl").unlink()
            (work_dir / "translated" / "01_ui_system").mkdir(parents=True)
            (work_dir / "translated" / "02_story_dialogue").mkdir(parents=True)
            (work_dir / "translated" / "01_ui_system" / "batch_001.output.jsonl").write_text(
                json.dumps({"id": "UI_0001", "zh": "關閉"}, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            (work_dir / "translated" / "02_story_dialogue" / "batch_001.output.jsonl").write_text(
                "\n".join([
                    json.dumps({"id": "ST_0001", "zh": "靠近"}, ensure_ascii=False),
                    json.dumps({"id": "ST_0002", "zh": "立即按下 {0}A{1}。"}, ensure_ascii=False),
                ]) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(run_developer_qa("asylum", work_dir=work_dir), 0)

    def test_source_resolver_installed_mod_corrupted_and_external_mod(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            game_root = tmp_path / "Game"
            (game_root / "BmGame" / "Localization" / "INT").mkdir(parents=True)
            (game_root / "BmGame" / "CookedPC").mkdir(parents=True)
            live_int = game_root / "BmGame" / "Localization" / "INT" / "GFxUI.int"
            live_upk = game_root / "BmGame" / "CookedPC" / "Unmodified.upk"
            added_file = game_root / "BmGame" / "AddedByMod.txt"

            live_int.write_text("MODIFIED_ZH", encoding="utf-8")
            live_upk.write_bytes(b"ORIG_UPK")
            added_file.write_text("MOD_ADDED", encoding="utf-8")

            bkp_dir = game_root / "_Backup_TW_Mod"
            bkp_int = bkp_dir / "BmGame" / "Localization" / "INT" / "GFxUI.int"
            bkp_int.parent.mkdir(parents=True, exist_ok=True)
            bkp_int.write_text("ORIG_EN", encoding="utf-8")

            manifest = {
                "version": 3,
                "game": "asylum",
                "state": "COMMITTED",
                "backup_files": {
                    "BmGame/Localization/INT/GFxUI.int": {
                        "sha256_orig": arkham_mod_cli.sha256_file(bkp_int),
                        "sha256_mod": arkham_mod_cli.sha256_file(live_int),
                    }
                },
                "added_files": {
                    "BmGame/AddedByMod.txt": {
                        "sha256_mod": arkham_mod_cli.sha256_file(added_file),
                    }
                },
                "ini_backups": [],
            }
            (bkp_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            resolver = OriginalSourceResolver("asylum", game_root)
            self.assertEqual(resolver.mode, "backup_overlay")
            self.assertEqual(resolver.resolve_file("BmGame/Localization/INT/GFxUI.int"), bkp_int)
            self.assertEqual(resolver.resolve_file("BmGame/CookedPC/Unmodified.upk"), live_upk)
            self.assertIsNone(resolver.resolve_file("BmGame/AddedByMod.txt"))

            live_int.write_text("STEAM_UPDATED_EN", encoding="utf-8")
            with self.assertRaisesRegex(SourceResolutionError, "modified externally|integrity check failed"):
                OriginalSourceResolver("asylum", game_root)

            live_int.write_text("MODIFIED_ZH", encoding="utf-8")
            bkp_int.write_text("CORRUPTED_BKP", encoding="utf-8")
            with self.assertRaisesRegex(SourceResolutionError, "Corrupted original backup"):
                OriginalSourceResolver("asylum", game_root)

            bkp_int.write_text("ORIG_EN", encoding="utf-8")
            manifest["state"] = "APPLYING"
            (bkp_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(SourceResolutionError, "Unfinished mod transaction"):
                OriginalSourceResolver("asylum", game_root)

    def test_public_packaging_and_audit(self):
        with tempfile.TemporaryDirectory() as td:
            pub_dir = Path(td) / "public_release"
            build_public_release(pub_dir)
            audit = audit_public_release(pub_dir)
            self.assertEqual(audit["status"], "PASS")
            self.assertFalse((pub_dir / "workspace").exists())
            self.assertFalse((pub_dir / "config" / "game_paths.json").exists())

            zh_sample = pub_dir / "translations" / "asylum" / "zh_tw.jsonl"
            if zh_sample.is_file():
                with zh_sample.open("a", encoding="utf-8") as f:
                    f.write(json.dumps({"cat": "01_ui_system", "source_hash": "0" * 64, "en_raw": "LEAK", "zh": "測"}) + "\n")
                with self.assertRaisesRegex(PublicAuditError, "forbidden fields"):
                    audit_public_release(pub_dir)

    def test_player_mode_and_developer_mode_cli_flows(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            fake_repo = tmp_path / "repo"
            (fake_repo / "config").mkdir(parents=True)
            (fake_repo / "glossary").mkdir(parents=True)
            (fake_repo / "glossary" / "dc_tw_glossary.json").write_text(
                json.dumps({"characters": {"Batman": "蝙蝠俠"}}, ensure_ascii=False), encoding="utf-8"
            )
            (fake_repo / "glossary" / "style_guide.md").write_text("# Style\n", encoding="utf-8")

            steam_game = tmp_path / "SteamAsylum"
            int_dir = steam_game / "BmGame" / "Localization" / "INT"
            cooked_dir = steam_game / "BmGame" / "CookedPC"
            int_dir.mkdir(parents=True)
            cooked_dir.mkdir(parents=True)
            (int_dir / "GFxUI.int").write_text(
                "[Errors]\nError_Ok=OK\nError_Batman=Batman is ready.\n",
                encoding="utf-16",
            )

            cfg = {
                "asylum": {
                    "root_dir": str(steam_game),
                    "int_dir": str(int_dir),
                    "cooked_dir": str(cooked_dir),
                }
            }
            (fake_repo / "config" / "game_paths.json").write_text(json.dumps(cfg), encoding="utf-8")

            with patch.object(arkham_mod_cli, "ROOT_DIR", fake_repo):
                arkham_mod_cli._sync_tool_roots()

                ns = type("Args", (), {})
                a_ext = ns()
                a_ext.dev_cmd = "extract"
                a_ext.game = "asylum"
                a_ext.allow_legacy_backup = False
                self.assertEqual(arkham_mod_cli.cmd_dev(a_ext), 0)

                a_prep = ns()
                a_prep.dev_cmd = "prepare"
                a_prep.game = "asylum"
                self.assertEqual(arkham_mod_cli.cmd_dev(a_prep), 0)
                self.assertTrue((fake_repo / "workspace" / "asylum" / "prepared" / "01_ui_system" / "batch_001.prompt.txt").is_file())

                import_src = tmp_path / "ai_outputs" / "01_ui_system"
                import_src.mkdir(parents=True)
                in_lines = [
                    json.loads(l)
                    for l in (fake_repo / "workspace" / "asylum" / "batches" / "01_ui_system" / "batch_001.input.jsonl").read_text(encoding="utf-8").splitlines()
                    if l.strip()
                ]
                (import_src / "batch_001.output.jsonl").write_text(
                    "\n".join(json.dumps({"id": x["id"], "zh": f"繁中_{x['en']}"}, ensure_ascii=False) for x in in_lines) + "\n",
                    encoding="utf-8",
                )

                a_imp = ns()
                a_imp.dev_cmd = "import"
                a_imp.game = "asylum"
                a_imp.from_dir = str(tmp_path / "ai_outputs")
                self.assertEqual(arkham_mod_cli.cmd_dev(a_imp), 0)

                a_qa = ns()
                a_qa.dev_cmd = "qa"
                a_qa.game = "asylum"
                self.assertEqual(arkham_mod_cli.cmd_dev(a_qa), 0)

                a_exp = ns()
                a_exp.dev_cmd = "export"
                a_exp.game = "asylum"
                a_exp.out_dir = None
                self.assertEqual(arkham_mod_cli.cmd_dev(a_exp), 0)
                self.assertTrue((fake_repo / "translations" / "asylum" / "manifest.json").is_file())

                shutil.rmtree(fake_repo / "workspace")

                a_pl_inst = ns()
                a_pl_inst.player_cmd = "install"
                a_pl_inst.game = "asylum"
                a_pl_inst.pack_dir = None
                a_pl_inst.ttf = r"C:\Windows\Fonts\msjh.ttc"
                a_pl_inst.force = False
                a_pl_inst.allow_legacy_backup = False
                self.assertEqual(arkham_mod_cli.cmd_player(a_pl_inst), 0)
                self.assertTrue((steam_game / "_Backup_TW_Mod" / "manifest.json").is_file())
                self.assertIn("繁中_", (int_dir / "GFxUI.int").read_text(encoding="utf-16"))

                a_pl_stat = ns()
                a_pl_stat.player_cmd = "status"
                a_pl_stat.game = "asylum"
                a_pl_stat.pack_dir = None
                self.assertEqual(arkham_mod_cli.cmd_player(a_pl_stat), 0)

                a_pl_un = ns()
                a_pl_un.player_cmd = "uninstall"
                a_pl_un.game = "asylum"
                a_pl_un.force = False
                self.assertEqual(arkham_mod_cli.cmd_player(a_pl_un), 0)
                self.assertIn("Batman is ready.", (int_dir / "GFxUI.int").read_text(encoding="utf-16"))

    def test_player_install_v1_update_pack_v2_uninstall_and_os_lock(self):
        """PUB-02: leftover .tw_mod.lock does not block update-pack; active OS lock blocks resolver."""
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            fake_repo = tmp_path / "repo"
            (fake_repo / "config").mkdir(parents=True)
            (fake_repo / "glossary").mkdir(parents=True)

            steam_game = tmp_path / "SteamAsylum"
            int_dir = steam_game / "BmGame" / "Localization" / "INT"
            cooked_dir = steam_game / "BmGame" / "CookedPC"
            int_dir.mkdir(parents=True)
            cooked_dir.mkdir(parents=True)
            (int_dir / "GFxUI.int").write_text("[Errors]\nError_Ok=OK\n", encoding="utf-16")

            cfg = {"asylum": {"root_dir": str(steam_game), "int_dir": str(int_dir), "cooked_dir": str(cooked_dir)}}
            (fake_repo / "config" / "game_paths.json").write_text(json.dumps(cfg), encoding="utf-8")

            # Export V1 pack
            v1_work = tmp_path / "v1_work"
            (v1_work / "metadata").mkdir(parents=True)
            (v1_work / "translated").mkdir(parents=True)
            from translation_pack import sha256_text
            (v1_work / "metadata" / "id_map.json").write_text(
                json.dumps({"UI_0001": {"id": "UI_0001", "category": "01_ui_system", "en_raw": "OK", "en_clean": "OK", "tags": []}}),
                encoding="utf-8",
            )
            (v1_work / "translated" / "01.output.jsonl").write_text(
                json.dumps({"id": "UI_0001", "zh": "確定V1"}, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            export_translation_pack("asylum", v1_work, fake_repo / "translations" / "asylum")

            with patch.object(arkham_mod_cli, "ROOT_DIR", fake_repo):
                arkham_mod_cli._sync_tool_roots()
                ns = type("Args", (), {})

                a_inst = ns()
                a_inst.player_cmd = "install"
                a_inst.game = "asylum"
                a_inst.pack_dir = None
                a_inst.ttf = r"C:\Windows\Fonts\msjh.ttc"
                a_inst.force = False
                a_inst.allow_legacy_backup = False
                self.assertEqual(arkham_mod_cli.cmd_player(a_inst), 0)
                self.assertTrue((steam_game / ".tw_mod.lock").exists())
                self.assertIn("確定V1", (int_dir / "GFxUI.int").read_text(encoding="utf-16"))

                # Export V2 pack and run update-pack WITH leftover .tw_mod.lock on disk
                (v1_work / "translated" / "01.output.jsonl").write_text(
                    json.dumps({"id": "UI_0001", "zh": "確定V2"}, ensure_ascii=False) + "\n", encoding="utf-8"
                )
                export_translation_pack("asylum", v1_work, fake_repo / "translations" / "asylum")

                a_upd = ns()
                a_upd.player_cmd = "update-pack"
                a_upd.game = "asylum"
                a_upd.pack_dir = None
                a_upd.ttf = r"C:\Windows\Fonts\msjh.ttc"
                a_upd.force = False
                a_upd.allow_legacy_backup = False
                self.assertEqual(arkham_mod_cli.cmd_player(a_upd), 0)
                self.assertIn("確定V2", (int_dir / "GFxUI.int").read_text(encoding="utf-16"))

                # Hold active OS lock in a child process -> OriginalSourceResolver must block & NOT delete lock
                locker_code = "\n".join([
                    "import sys, time",
                    "from pathlib import Path",
                    f"sys.path.insert(0, {str(ROOT_DIR)!r})",
                    "import arkham_mod_cli",
                    f"with arkham_mod_cli._game_lock(Path({str(steam_game)!r})):",
                    "    print('LOCKED', flush=True)",
                    "    time.sleep(10)",
                ])
                proc = subprocess.Popen(
                    [sys.executable, "-c", locker_code],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                try:
                    ready = proc.stdout.readline().strip()
                    self.assertEqual(ready, "LOCKED")
                    with self.assertRaisesRegex(SourceResolutionError, "Another install/uninstall transaction is active"):
                        OriginalSourceResolver("asylum", steam_game)
                    self.assertTrue((steam_game / ".tw_mod.lock").exists())
                finally:
                    proc.terminate()
                    proc.wait(timeout=5)

                # Uninstall after lock released
                a_un = ns()
                a_un.player_cmd = "uninstall"
                a_un.game = "asylum"
                a_un.force = False
                self.assertEqual(arkham_mod_cli.cmd_player(a_un), 0)
                self.assertIn("Error_Ok=OK", (int_dir / "GFxUI.int").read_text(encoding="utf-16"))

    def test_public_packaging_safety_and_required_files(self):
        """PUB-03, PUB-04 & Section B: refuse unsafe paths and verify required public files."""
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            with self.assertRaisesRegex(PublicAuditError, "source_root"):
                build_public_release(ROOT_DIR)
            with self.assertRaisesRegex(PublicAuditError, "ancestor"):
                build_public_release(ROOT_DIR.parent)
            with self.assertRaisesRegex(PublicAuditError, "inside source_root"):
                build_public_release(ROOT_DIR / "dist" / "bad_pub")

            pub_dir = tmp_path / "pub_ok"
            res = build_public_release(pub_dir)
            self.assertEqual(res["audit"]["status"], "PASS")
            for req in (
                ".gitattributes",
                ".gitignore",
                "README.md",
                "LICENSE",
                "CHANGELOG.md",
                "CONTRIBUTING.md",
                "SECURITY.md",
                "Install_TW_Mod.bat",
            ):
                self.assertTrue((pub_dir / req).is_file(), f"Missing {req}")

            # Non-empty out_dir without overwrite=True must be rejected
            with self.assertRaisesRegex(PublicAuditError, "not empty"):
                build_public_release(pub_dir, overwrite=False)


if __name__ == "__main__":
    unittest.main()
