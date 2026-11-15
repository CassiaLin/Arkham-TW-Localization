# AI Agent Translation Batch Template

使用 Developer Mode 時，請將 `workspace/<game>/prepared/<cat>/batch_XXX.prompt.txt` 與對應的 `workspace/<game>/batches/<cat>/batch_XXX.input.jsonl` 交由 AI Agent（或人工譯者）處理。

## 強制規則

1. **格式**：輸出必須為純 JSONL（每行一筆 JSON），檔名對應 `batch_XXX.output.jsonl`：
   ```json
   {"id": "ST_0001", "zh": "高譚市需要你，布魯斯。"}
   ```
2. **控制碼保留**：輸入中的 `{0}`、`{1}` 等佔位符必須原樣保留且數量一致，不可遺漏、改名或新增未定義的 `{N}`。
3. **台灣繁體中文與術語**：嚴格遵守 `glossary/dc_tw_glossary.json` 與 `glossary/style_guide.md`（例如 Batman → 蝙蝠俠、Joker → 小丑、Gotham → 高譚市）。
4. **Provider 中立**：本專案核心工具鏈不包含任何付費 API Key 或特定模型依賴；產出的 `*.output.jsonl` 透過 `python arkham_mod_cli.py dev import <game> --from-dir <dir>` 匯入後，由 `dev qa` 進行自動化驗證。
