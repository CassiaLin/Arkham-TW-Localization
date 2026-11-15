# Arkham-TW-Localization Developer Guide

本專案支援 **Dual-Mode** 架構：
- **Player Mode**：僅需純中文 Translation Pack（無英文原文、無遊戲資源），在玩家本機從 Steam 原版遊戲提取、驗證指紋並建置安裝。
- **Developer Mode**：整合 M1～M5 工作流，供開發者或 AI Agent 進行文本提取、術語注入、翻譯匯入、QA 驗證、建置與匯出公開 Translation Pack。

## Developer CLI 工作流程 (`dev`)

```bash
# 1. M1: 從本地 Steam 原版遊戲（或已驗證的原版備份）提取文本與 id_map.json
python arkham_mod_cli.py dev extract asylum

# 2. M2: 生成含術語表與風格規範的批次翻譯提示檔 (workspace/<game>/prepared/)
python arkham_mod_cli.py dev prepare asylum

# 3. 人工或 AI Agent 產出 *.output.jsonl 後匯入並檢查缺漏
python arkham_mod_cli.py dev import asylum --from-dir path/to/outputs

# 4. M4: 執行 Developer QA（動態使用本地 corpus inventory，不鎖死正式版筆數）
python arkham_mod_cli.py dev qa asylum

# 5. M5: 建置 dist/ 產物並驗證 build manifest
python arkham_mod_cli.py dev build asylum

# 6. 交易式安裝至 Steam 目錄（含 SHA-256 備份與還原保護）
python arkham_mod_cli.py dev install asylum

# 7. 匯出純中文 Translation Pack 至 translations/<game>/
python arkham_mod_cli.py dev export all
```

## 資料格式說明

- **M1 輸出**：
  - `workspace/<game>/metadata/id_map.json`：含 `id`, `category`, `sp`, `en_raw`, `en_clean`, `tags`, `ctx`, `sources`（**私有開發資料，不得公開**）。
  - `workspace/<game>/batches/<cat>/batch_XXX.input.jsonl`：雙層結構，將 `<tag>`、`%s`、`{0}` 等引擎控制碼替換為 `{0}`, `{1}`。
- **M3 譯文輸入 (`*.output.jsonl`)**：
  - 每行一筆 JSON：`{"id": "UI_0001", "zh": "繁體中文譯文"}`。
- **Shared Translation Pack (`translations/<game>/`)**：
  - `zh_tw.jsonl`：`{"cat", "source_hash", "tag_count", "tags_hash", "zh", "legacy_id"}`，以 `(cat, SHA256(en_raw))` 匹配，完全不含遊戲英文原文。
  - `manifest.json`：記錄 `pack_format_version` (`1.0`)、`extractor_version` (`m1-v1`)、筆數與所有檔案 SHA-256。

## 輔助資源從零翻譯流程與限制

除主要 `.int` 與 UPK 對話外，三代作品另包含輔助對照表，目前尚未整合進 `dev prepare` 自動批次切分，需依以下格式人工或獨立腳本處理後置於 `workspace/<game>/`（`dev export` 會自動將英文鍵轉為 `*_sha256_zh.json`）：
- **City / Origins / Knight USM 過場影片字幕**：`usm_zh.json`（格式 `{"英文台詞": "繁中譯文"}`）或 `usm_sha256_zh.json`。
- **Origins 額外 UPK 字幕**：`upk_extra_zh.json`（格式 `{"英文台詞": "繁中譯文"}`）或 `upk_extra_sha256_zh.json`。
- **Knight 壓縮對話事件**：`ak_en_zh.json`（或 `ak_en_sha256_zh.json`）與 `ak_events_zh.json`（鍵值為 `event_file|event_id`），搭配 `metadata/upk_list.json`。
