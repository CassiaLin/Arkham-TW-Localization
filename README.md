# Batman: Arkham Series — Taiwan Traditional Chinese Localization (台灣繁體中文化)

本專案提供 **Batman: Arkham** 四部曲（瘋人院 / 城市 / 起源 / 騎士）的台灣繁體中文化工具鏈與純中文譯文包。
公開發布版本**不包含任何遊戲英文原文或原版／修改後遊戲二進位檔案**，所有中文化資源均於玩家本機端由合法 Steam 原版遊戲即時提取、比對指紋並重建安裝。

> [!NOTE]
> 本專案採用 **Dual-Mode（雙模式）** 設計：**Player Mode** 供一般玩家一鍵提取、驗證與安裝；**Developer Mode** 供譯者與開發者進行本地提取、批次翻譯、QA 驗證與匯出譯文包。

---

## Player Mode（一般玩家使用方式）

### 系統需求
- **作業系統**：Windows 10 / 11（64 位元）
- **遊戲版本**：合法 Steam 正版遊戲（支援 `asylum`、`city`、`origins`、`knight`）
- **Python 環境**：Python 3.10 以上，並安裝相依套件：
  ```bash
  pip install -r requirements.txt
  ```
- **不需要任何 AI API Key 或私人開發資料**。

### 安裝與操作

1. **圖形選單入口（推薦）**：直接雙擊執行 [Install_TW_Mod.bat](Install_TW_Mod.bat)，依中文選單選擇安裝單一遊戲、全部已偵測遊戲、更新譯文包或解除安裝。
2. **CLI 指令入口**：
   ```bash
   # 查看 Steam 遊戲偵測、Mod 狀態與 Translation Pack 狀態
   python arkham_mod_cli.py player status all

   # 安裝單一遊戲或全部已偵測遊戲（自動執行：偵測 -> 原版驗證 -> 提取 -> 譯文匹配 -> Player QA -> 建置 -> 驗證 -> 交易式安裝）
   python arkham_mod_cli.py player install asylum
   python arkham_mod_cli.py player install all

   # 取得新版 Translation Pack 後安全更新（V1 -> V2 交易式快照更新）
   python arkham_mod_cli.py player update-pack all

   # 解除安裝並還原原始英文檔案
   python arkham_mod_cli.py player uninstall asylum
   ```

### 瘋人院 (Asylum) 字幕與行距補丁說明
- `player install asylum` 建置時會自動對 `ShippingPC-BmGame.exe` 套用 **60px 字幕行距補丁**（修正 4K/高解析度下雙行字幕上下重疊問題），並在安裝時備份原版 EXE 與 INI 設定。

### Steam 更新、備份衝突與完整性驗證指引
- **Steam 遊戲更新後**：若遊戲執行檔或資源曾被 Steam 更新，安裝／還原引擎會偵測到外部修改並**安全中止**，不會以舊版備份覆蓋新版檔案。請先在 Steam 執行「驗證遊戲檔案的完整性」，再重新執行 `player install`。
- **備份損毀或未完成交易**：若 `_Backup_TW_Mod/manifest.json` 遺失、雜湊不符或前次安裝遭強制中斷，程式會安全中止並提示復原指令（`uninstall` 或重新 `install` 復原快照）。

---

## Developer Mode（開發者與譯者工作流程）

Developer Mode 整合 `M1 ~ M5` 流程，且所有遊戲英文原文與 `id_map.json` **僅在本機生成，不會進入公開 Git 追蹤**：

- 完整工作流程與資料格式請參閱：[DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)
- AI Agent 批次翻譯範本請參閱：[AI_AGENT_TEMPLATE.md](docs/AI_AGENT_TEMPLATE.md)

核心指令：
```bash
python arkham_mod_cli.py dev extract <game>     # M1：從原版/已驗證備份提取本地文本與 id_map.json
python arkham_mod_cli.py dev prepare <game>     # M2：注入術語表產生批次提示檔
python arkham_mod_cli.py dev import <game> --from-dir <dir>  # 匯入譯文並檢查缺漏
python arkham_mod_cli.py dev qa <game>          # M4：Developer QA（動態本地 corpus inventory）
python arkham_mod_cli.py dev build <game>       # M5：建置 dist/ 與 build_manifest.json
python arkham_mod_cli.py dev install <game>     # 交易式安裝
python arkham_mod_cli.py dev export <game|all>  # 匯出純中文 Translation Pack 至 translations/<game>/
```

### 輔助資源從零翻譯與現有功能限制
- **主要文本（`.int` 與 UPK 字幕）**：可完全透過 `dev extract` → `dev prepare` → `dev import` 自動化流程從零翻譯。
- **輔助對照表（USM 影片字幕、Origins `upk_extra_zh.json`、Knight `ak_en_zh.json` / `ak_events_zh.json`）**：目前尚未納入 `dev prepare` 批次自動產生，若需從零重譯輔助資源，須依 [DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md) 規範產出對應 JSON 或 `*_sha256_zh.json` 後再執行 `dev build` / `dev export`。

---

## 實機畫面與驗收狀態

> [!IMPORTANT]
> 目前已完成四部曲（70,160 筆主譯文與全部輔助資源）之乾淨公開目錄本地提取、指紋匹配、Player QA 與 M5 建置驗證；四部曲完整遊戲內通關實機驗收仍待補充。實機截圖將於完成通關驗收後補齊（本文件保留截圖位置，不使用其他畫面代替）。

<!-- 實機截圖預留位置：
![Asylum 實機截圖](docs/screenshots/asylum_placeholder.png)
![City 實機截圖](docs/screenshots/city_placeholder.png)
![Origins 實機截圖](docs/screenshots/origins_placeholder.png)
![Knight 實機截圖](docs/screenshots/knight_placeholder.png)
-->

---

## 授權與免責聲明

- 本儲存庫之程式碼與原創繁體中文譯文授權條款請見 [LICENSE](LICENSE)。
- *Batman: Arkham Asylum*、*Batman: Arkham City*、*Batman: Arkham Origins*、*Batman: Arkham Knight* 之遊戲名稱、原始資源與商標權均屬 Warner Bros. Entertainment Inc.、DC Comics 及原開發商所有。本專案僅提供非營利社群中文化工具與純中文譯文包。
