# Contributing to Arkham-TW-Localization

感謝您協助完善《蝙蝠俠：阿卡漢》系列台灣繁體中文化！

## 回報問題與翻譯建議
- **程式或安裝錯誤**：請使用 GitHub Issues 中的 **Bug Report** 範本。
- **翻譯用字或語氣建議**：請使用 **Translation Feedback** 範本，並附上遊戲名稱與原文／譯文上下文。
- **實機遊玩驗收回報**：請使用 **Playtest Report** 範本回報各章節字幕、字型與影片顯示狀況。

## 開發者貢獻規範
1. **嚴禁提交受版權保護的遊戲資料**：不得將任何遊戲原版 `.exe`、`.upk`、`.usm`、完整英文原文語料（`id_map.json`、`*.input.jsonl`）或本機路徑設定（`config/game_paths.json`）提交至公開儲存庫。
2. **術語與風格一致**：請遵守 [dc_tw_glossary.json](glossary/dc_tw_glossary.json) 與 [style_guide.md](glossary/style_guide.md)。
3. **執行測試**：提交 PR 前請確認：
   ```bash
   python -m unittest discover -s tests -v
   ```
   並確保 `python tools/package_public.py <temp_dir>` 稽核通過。
