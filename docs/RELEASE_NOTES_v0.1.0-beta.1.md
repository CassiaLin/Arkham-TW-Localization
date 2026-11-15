# v0.1.0-beta.1 — 首次公開測試版

《蝙蝠俠：阿卡漢》四部曲台灣繁體中文化，歡迎玩家參與測試與勘誤。

## 支援遊戲

| Steam PC 版 | 主要譯文 |
|---|---:|
| 阿卡漢瘋人院 GOTY | 6,141 |
| 阿卡漢城市 GOTY | 13,167 |
| 阿卡漢始源 | 40,651 |
| 阿卡漢騎士 | 10,201 |
| 合計 | 70,160 |

另包含過場影片字幕及各作所需的輔助翻譯資料。

## 一般玩家安裝

1. 下載本次 Release 的 ZIP 並解壓縮至獨立資料夾。
2. 確認 Steam 正版遊戲已安裝並關閉，Windows 10／11 已安裝 Python 3.10 以上版本。
3. 在解壓縮目錄開啟終端機，執行 `python -m pip install -r requirements.txt`。
4. 雙擊 `Install_TW_Mod.bat`，選擇要安裝的遊戲。
5. 安裝完成後從 Steam 啟動遊戲。還原時使用同一選單的解除安裝功能。

不需要 AI 帳號或 API Key。工具使用玩家本地遊戲資源重建繁中版本，並保存原版備份。

## 開發者

請參閱 [README](../README.md)、[Developer Guide](DEVELOPER_GUIDE.md) 與 [AI Agent Template](AI_AGENT_TEMPLATE.md)。可自行提取文本、建立翻譯批次、匯入 AI／人工譯文、QA、建置、安裝及匯出翻譯包。部分輔助資源仍需獨立腳本或人工處理。

## 已知限制

- 四部遊戲均已完成開發者初步實機測試，完整通關、所有支線及 DLC 尚待玩家回饋。
- 四部曲已完成乾淨目錄本地提取、指紋匹配、QA 及資源建置；這不等同全面實機驗收。
- 尚未驗證其他商店版本、Linux 遊戲執行環境或與其他 Mod 混用。
- 瘋人院會在本機對符合特徵的 EXE 套用 60px 字幕行距補丁。不相容版本可能無法建置。
- Steam 更新或備份衝突時，請勿直接強制覆蓋或刪除備份，先依 README 指引確認狀態。

## 回報問題

請至 [GitHub Issues](https://github.com/CassiaLin/Arkham-TW-Localization/issues)，選擇遊戲問題、翻譯勘誤或實機測試回報。請提供遊戲名稱、版本、場景、問題描述與必要截圖，勿上傳完整遊戲檔案或個人資料。

程式碼授權為 MIT；翻譯資料與第三方權利請參閱 [Translation Data Notice](TRANSLATION_NOTICE.md)。
