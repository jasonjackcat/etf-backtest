# etf-backtest

台股 ETF 跌深加碼與個股 OSC 波段回測網站。

- 網站：https://jasonjackcat.github.io/etf-backtest/
- 資料：Yahoo 財經（yfinance）日 K、配息、分割／配股；股名來自證交所與櫃買中心開放資料。
- 更新：GitHub Actions 每個交易日台灣時間 18:00 自動執行 `build_site.py`，產生 `_site/` 後發佈到 GitHub Pages。

## 檔案

| 檔案 | 用途 |
|---|---|
| `template.html` | 回測網頁（版面與計算程式） |
| `build_site.py` | 抓全部上市櫃股票與 ETF 的資料，產生網站 |
| `.github/workflows/update.yml` | 每天自動執行的排程設定 |

## 手動更新

到 **Actions** → **更新股價資料並發佈網站** → **Run workflow**。
