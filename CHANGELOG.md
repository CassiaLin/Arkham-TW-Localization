# Changelog

All notable changes to **Arkham-TW-Localization** will be documented in this file.

## [v0.1.0-beta.1] - 2026-11-15 (First Public Beta Candidate)

### Added
- **Dual-Mode Architecture**:
  - **Player Mode** (`arkham_mod_cli.py player ...` & `Install_TW_Mod.bat`): local extraction, SHA-256 Translation Pack matching, mandatory Player QA, M5 resource rebuild, and transactional install/update/uninstall without any AI API key or English source distribution.
  - **Developer Mode** (`arkham_mod_cli.py dev ...`): integrated `extract`, `prepare`, `import`, `qa`, `build`, `install`, and `export` commands.
- **Shared Translation Pack v1.0** (`translations/<game>/`):
  - Pure Traditional Chinese (`zh-TW`) packs for all 4 Arkham titles (**70,160** main entries + hashed auxiliary lookups for USM, Origins `upk_extra`, and Knight `ak_en`/`ak_events`), verified by `manifest.json` SHA-256 and LF line-ending rules (`.gitattributes`).
- **Original Source Resolver**:
  - Safe read-only overlay resolving clean vanilla Steam files and verified `_Backup_TW_Mod` backups with OS-managed `.tw_mod.lock` compatibility and external modification protection.
- **Public Packaging & Audit (`tools/package_public.py`)**:
  - Explicit allowlist packaging with staging verification, symlink/path safety checks, and schema/content audit.
- **Asylum Subtitle Rendering Fixes**:
  - Dynamic `Startup_INT.upk` / `CommonGame_LOC_INT.upk` Chinese font atlas injection and `ShippingPC-BmGame.exe` 60px line-height patch for 4K/high-DPI multi-line subtitles.

### Known Limitations
- Full end-to-end in-game playthrough playtesting across all 4 titles is still in progress.
- Auxiliary dictionaries (`usm_zh`, `upk_extra_zh`, `ak_en_zh`, `ak_events_zh`) are not yet integrated into `dev prepare` automated batch splitting.
