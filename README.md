# Fund and HSBC Rate Scraper

這個專案會抓取：

- 八檔基金的日期、淨值、每單位配息金額
- 匯豐銀行台灣外匯牌告匯率：銀行買價、銀行賣價
- 匯率幣別：美元、南非幣、澳幣、歐元、加幣、日幣

資料會寫入 `data/scraper.sqlite3`，並可匯出 CSV 到 `exports/`。

## 第一次安裝

第一次拿到專案時，先執行：

```powershell
.\setup.ps1
```

這會檢查 Python 版本、安裝 `requirements.txt`、建立必要資料夾、確認本機 Chart.js 檔案，並在資料庫存在時自動執行健康檢查。

## 啟動 Dashboard

安裝依賴（第一次執行前）：

```powershell
pip install -r requirements.txt
```

啟動本機 Dashboard：

```powershell
python app.py
```

或直接執行：

```powershell
.\start_dashboard.bat
```

開啟瀏覽器前往 [http://localhost:5000](http://localhost:5000)，可以看到：

- 最後更新時間
- 八檔基金的淨值與配息資料範圍和筆數
- 匯豐六種幣別最新即期／現金買賣價
- 基金淨值趨勢圖可切換 `1M / 1Y / 3Y / 5Y / 10Y / 全部`
- 歷史匯率趨勢圖可切換 `1D / 5D / 1M / 1Y / 3Y / 5Y / 10Y / 全部`
- 下載原始 CSV 和品質報表

Dashboard 的 Chart.js 已放在 `static/vendor/chart.umd.min.js`，不需要依賴 CDN。
Dashboard 介面樣式在 `static/css/dashboard.css`，圖表互動在 `static/js/dashboard.js`。

爬蟲與 Dashboard 可以同時執行，互不干擾。

## Zeabur 部署

此專案已包含 Zeabur/Nixpacks 可用的 `Procfile`：

```text
web: python scraper.py --once --fx-history --fx-history-years 10 --export --report && gunicorn app:app --bind 0.0.0.0:$PORT
```

部署流程：

1. 將專案 push 到 GitHub。
2. 在 Zeabur 建立新專案並連接該 GitHub repo。
3. Zeabur 會安裝 `requirements.txt`。
4. 啟動時會先抓一次基金、匯率與 10 年歷史匯率，再啟動 dashboard。

注意：Zeabur 的檔案系統通常不是長期資料庫方案。若要在 Zeabur 上每小時持續爬蟲並保存資料，建議再加 persistent volume 或改用外部資料庫。

## 健康檢查

檢查目前資料是否足夠驗收：

```powershell
python health_check.py
```

健康檢查會確認：

- Python 與 Flask 依賴
- 必要檔案與資料夾
- SQLite 資料庫與資料表
- 八檔基金是否都有淨值資料
- 六種匯豐匯率是否齊全
- 六種歷史匯率是否有 10 年左右資料範圍
- CSV 與品質報表是否存在

結果會以 `OK / WARNING / ERROR` 顯示；只有 `ERROR` 代表需要先修正。

## 快速執行

跑一次、匯出 CSV、產生驗收報表：

```powershell
python scraper.py --once --export --report
```

補 10 年歷史匯率資料，供趨勢圖使用：

```powershell
python scraper.py --once --fx-history --fx-history-years 10 --export --report
```

或直接執行：

```powershell
.\run_fx_history.ps1
```

持續更新，每 60 分鐘跑一次：

```powershell
python scraper.py --interval-minutes 60 --history-years 10 --fx-history-days 14 --export --report
```

或直接執行：

```powershell
.\run_realtime.ps1
```

即時更新腳本會每小時更新基金、匯豐即時匯率，並順手刷新最近 14 天歷史匯率，讓匯率趨勢圖尾端保持更新。

## 輸出檔案

原始資料：

- `exports/fund_nav.csv`
- `exports/fund_dividend.csv`
- `exports/exchange_rate.csv`
- `exports/historical_fx_rate.csv`

驗收報表：

- `exports/data_quality_report.md`
- `exports/fund_quality_report.csv`
- `exports/exchange_rate_latest.csv`
- `exports/historical_fx_quality_report.csv`

Log：

- `logs/scraper.log`

## 目前驗證輸出

最近一次執行結果：

- 匯豐匯率：6 筆
- 基金淨值：13,459 筆
- 基金配息：246 筆

目前基金淨值資料範圍：

- `ACDD04` 安聯台灣科技基金：2016-06-13 ~ 2026-06-10
- `ACPS10` 統一奔騰基金：2016-06-13 ~ 2026-06-10
- `ACPS02` 統一黑馬基金：2016-06-13 ~ 2026-06-10
- `PIZC5` 東方匯理策略收益債券 A 南非幣避險：2019-06-06 ~ 2026-06-09
- `PIZD7` 東方匯理新興市場債券 A 南非幣避險：2019-06-06 ~ 2026-06-09
- `JFZN3` JPM 多重收益美元對沖 A 穩定月配：2021-10-15 ~ 2026-06-09
- `ALBT8` 聯博美國成長 AP 美元：2024-07-24 ~ 2026-06-09
- `76959044C` 元大台灣高股息優質龍頭基金-新台幣I類型累積級別：2020-09-21 ~ 2026-06-10

## 八檔基金代碼

基金清單在 `config/funds.json`。

- `ACDD04` 安聯台灣科技基金
- `ACPS10` 統一奔騰基金
- `76959044C` 元大台灣高股息優質龍頭基金-新台幣I類型累積級別
- `ACPS02` 統一黑馬基金
- `PIZC5` 東方匯理基金策略收益債券A南非幣避險(穩定月配息)
- `PIZD7` 東方匯理基金新興市場債券A南非幣避險(穩定月配息)
- `JFZN3` 摩根投資基金-多重收益基金JPM多重收益(美元對沖)-A股(穩定月配)
- `ALBT8` 聯博-美國成長基金AP(總報酬月配)級別美元

## 資料表欄位

`exchange_rate`

- `currency`
- `rate_timestamp`
- `spot_buy`
- `spot_sell`
- `cash_buy`
- `cash_sell`
- `source`
- `fetched_at`

`historical_fx_rate`

- `currency`
- `rate_date`
- `twd_per_unit`
- `base_currency`
- `quote_currency`
- `source`
- `fetched_at`

`fund_nav`

- `fund_id`
- `fund_name`
- `nav_date`
- `nav`
- `source`
- `fetched_at`

`fund_dividend`

- `fund_id`
- `fund_name`
- `dividend_date`
- `per_unit_dividend`
- `source`
- `fetched_at`
- `raw_row`

## 十年歷史資料

基金淨值已接入 FundClear 基金資訊觀測站的歷史淨值 API。只要 `config/funds.json` 裡有 `fundclear` 代碼，就會優先用 FundClear 回補近 10 年到現在；若該基金成立或公開資料較晚，會從可查到的最早日期開始。

`76959044C` 是元大台灣高股息優質龍頭基金-新台幣I類型累積級別。這檔是累積級別，目前沒有每單位配息資料列。

基金配息部分，境外月配基金使用 FundClear 歷史配息 API；境內基金配息目前保留 MoneyDJ 頁面資料。

## 匯率更新與趨勢圖建議

目前匯豐匯率抓的是即時牌告匯率，適合累積「從系統開始運作後」的匯率變化。

長期趨勢圖使用 Frankfurter 歷史匯率補源。資料會寫入 `historical_fx_rate`，欄位 `twd_per_unit` 代表「1 單位外幣約等於多少台幣」，例如 USD 的 `twd_per_unit` 就是 1 美元兌台幣的參考匯率。

歷史匯率來源：

- Frankfurter API：`https://frankfurter.dev/`
- API endpoint：`https://api.frankfurter.dev/v2/rates`

趨勢圖建議分兩層處理：

- 長期趨勢：用 `historical_fx_rate` 的每日一筆歷史參考匯率，時間範圍可抓 10 年。
- 即時監控：用匯豐牌告匯率每 60 分鐘更新一次。

匯豐即時牌告與 Frankfurter 歷史匯率不是同一種價格。匯豐是銀行買價/賣價，Frankfurter 是市場參考匯率；兩者分開存放，避免趨勢圖和銀行牌告價混用。

不建議直接用即時爬蟲硬補 10 年匯豐牌告歷史，除非匯豐有可公開查詢的歷史資料來源。對趨勢圖來說，每日參考匯率通常已足夠；每 5 或 10 分鐘抓一次會讓資料量變大，但對長期趨勢幫助有限。

建議間隔：

- 驗收與一般趨勢圖：每 60 分鐘
- 想看日內波動：每 30 分鐘
- 不建議低於 10 分鐘，避免對來源網站造成壓力

## Windows 工作排程建議

若要讓爬蟲長期自動執行，可以用 Windows 工作排程器建立工作：

- 程式：`powershell.exe`
- 引數：`-ExecutionPolicy Bypass -File "C:\Users\User\Desktop\Claude Code\Yungshiu\run_realtime.ps1"`
- 起始位置：`C:\Users\User\Desktop\Claude Code\Yungshiu`

也可以不使用工作排程器，直接開 PowerShell 執行：

```powershell
.\run_realtime.ps1
```

## 驗收方式

執行：

```powershell
python scraper.py --once --export --report
```

然後檢查：

- `exports/data_quality_report.md` 是否列出八檔基金
- `exports/fund_quality_report.csv` 是否有每檔基金的淨值起訖日與筆數
- `exports/exchange_rate_latest.csv` 是否有六種幣別
- `exports/historical_fx_quality_report.csv` 是否有六種幣別的歷史匯率範圍
- `logs/scraper.log` 是否有本次執行紀錄
