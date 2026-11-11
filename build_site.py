# -*- coding: utf-8 -*-
"""
產生 GitHub Pages 網站：全部上市櫃股票與 ETF 的日 K 資料檔 + 回測網頁。

輸出到 _site/：
  index.html            回測網頁（內含 7 檔 ETF 的資料，個股資料需要時才下載）
  data/index.json       代號清單 {代號: [名稱, 市場]}
  data/<前 3 碼>.json    該組股票的日 K、配息、分割／配股
在 GitHub Actions 每個交易日 18:00 執行（見 .github/workflows/update.yml），
也可以在本機執行：python build_site.py   （測試時可設環境變數 LIMIT=50 只抓前 50 檔）
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_site")
TEMPLATE = os.path.join(HERE, "template.html")
DATA_START = "2019-06-01"
ETFS = ["0050", "00685L", "0056", "00878", "00918", "00919", "009816"]
# yfinance 沒有記錄、但價格已經換算過的分割
MANUAL_SPLITS = {"0050": [["2025-06-18", 4]], "00685L": [["2026-07-07", 24]]}
# 上一版網站：這次抓不到的股票沿用上一版的資料
PREV_URL = os.environ.get("PREV_URL", "https://jasonjackcat.github.io/etf-backtest/data/")
LIMIT = int(os.environ.get("LIMIT", "0"))
CHUNK = 150
TZ = dt.timezone(dt.timedelta(hours=8))


def get_json(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8"))


def universe():
    """{代號: (名稱, 市場, yfinance 後綴)}，只留普通股（4 碼數字）與 ETF（00 開頭）。"""
    ok = re.compile(r"^(\d{4}|00\d{2,4}[A-Z]?)$")
    out = {}
    srcs = [("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", "Code", "Name", "上市", ".TW"),
            ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes", "SecuritiesCompanyCode", "CompanyName", "上櫃", ".TWO")]
    for url, kc, kn, mkt, suf in srcs:
        for attempt in range(3):
            try:
                rows = get_json(url)
                break
            except Exception as e:
                print(f"清單抓取失敗（{mkt}，第 {attempt + 1} 次）：{e}")
                time.sleep(5)
        else:
            rows = []
        for r in rows:
            code = str(r.get(kc, "")).strip()
            if ok.match(code) and code not in out:
                out[code] = (str(r.get(kn, "")).strip(), mkt, suf)
    for e in ETFS:   # 確保 7 檔 ETF 一定在
        out.setdefault(e, (e, "上市", ".TW"))
    return out


def frame_to_data(df, code):
    import pandas as pd
    if df is None or df.empty or "Close" not in df:
        return None
    df = df.dropna(subset=["Open", "Close"])
    if "Volume" in df:
        df = df[df["Volume"].fillna(0) > 0]
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx
    now = dt.datetime.now(TZ)
    if now.time() < dt.time(14, 30):   # 收盤前不放今天還在變動的 K 棒
        df = df[df.index.date < now.date()]
    if len(df) < 20:
        return None
    d = {"px": [[i.strftime("%Y-%m-%d"), round(float(o), 4), round(float(c), 4)]
                for i, o, c in zip(df.index, df["Open"], df["Close"])]}
    # K 線用：最近 750 個交易日（約 3 年）的 [最高, 最低, 成交量(張)]，和 px 的最後幾筆對齊
    if "High" in df and "Low" in df:
        tail = df.iloc[-750:]
        vol = tail["Volume"].fillna(0) if "Volume" in tail else None
        d["hlv"] = [[round(float(h), 4), round(float(l), 4), int(round(float(v) / 1000)) if vol is not None else 0]
                    for h, l, v in zip(tail["High"], tail["Low"], vol if vol is not None else [0] * len(tail))]
    div = df["Dividends"] if "Dividends" in df else None
    d["div"] = {i.strftime("%Y-%m-%d"): round(float(v), 4) for i, v in div.items() if v and v > 0} if div is not None else {}
    # 最近 15 個交易日的成交量（張），只給掃描用（算 5 日、10 日均量），不寫進個股資料檔
    if "Volume" in df:
        tail = df["Volume"].fillna(0).iloc[-15:]
        d["vol15"] = {i.strftime("%Y-%m-%d"): int(round(float(v) / 1000)) for i, v in tail.items()}
    sp = df["Stock Splits"] if "Stock Splits" in df else None
    splits = [[i.strftime("%Y-%m-%d"), round(float(v), 6)] for i, v in sp.items() if v and v > 0] if sp is not None else []
    if code in MANUAL_SPLITS:
        splits = MANUAL_SPLITS[code]
    if splits:
        d["splits"] = splits
    return d


def download(codes, uni):
    import yfinance as yf
    result = {}
    tickers = {f"{c}{uni[c][2]}": c for c in codes}
    names = list(tickers)
    end = (dt.datetime.now(TZ).date() + dt.timedelta(days=1)).isoformat()
    for k in range(0, len(names), CHUNK):
        part = names[k:k + CHUNK]
        for attempt in range(3):
            try:
                df = yf.download(part, start=DATA_START, end=end, auto_adjust=False, actions=True,
                                 group_by="ticker", threads=True, progress=False)
                break
            except Exception as e:
                print(f"下載失敗（第 {attempt + 1} 次）：{e}")
                time.sleep(20)
        else:
            continue
        for t in part:
            try:
                sub = df[t] if len(part) > 1 else df
                d = frame_to_data(sub.copy(), tickers[t])
                if d:
                    result[tickers[t]] = d
            except Exception:
                pass
        print(f"已下載 {min(k + CHUNK, len(names))}/{len(names)}，成功 {len(result)} 檔")
        time.sleep(2)
    return result


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    uni = universe()
    codes = sorted(uni)
    if LIMIT:
        codes = [c for c in codes if c in ETFS] + [c for c in codes if c not in ETFS][:LIMIT]
    print(f"代號共 {len(codes)} 檔")
    data = download(codes, uni)

    # 抓不到的，沿用上一版網站的資料
    missing = [c for c in codes if c not in data]
    prev_groups = {}
    for c in missing:
        g = c[:3]
        if g not in prev_groups:
            try:
                prev_groups[g] = get_json(PREV_URL + g + ".json", timeout=30)
            except Exception:
                prev_groups[g] = {}
        if c in prev_groups[g]:
            data[c] = prev_groups[g][c]
    print(f"有資料 {len(data)} 檔；這次沒抓到 {len(missing)} 檔，其中沿用上一版 {sum(1 for c in missing if c in data)} 檔")

    for c, d in data.items():
        nm, mkt, _ = uni.get(c, (c, "", ""))
        d["name"] = nm or c
        d["market"] = mkt

    if any(e not in data for e in ETFS):
        print("ETF 資料不完整，停止產生網站")
        return 1

    os.makedirs(os.path.join(OUT, "data"), exist_ok=True)
    groups = {}
    for c, d in data.items():
        groups.setdefault(c[:3], {})[c] = {k: v for k, v in d.items() if k != "vol15"}
    for g, items in groups.items():
        with open(os.path.join(OUT, "data", g + ".json"), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, separators=(",", ":"))
    index = {c: [d["name"], d["market"]] for c, d in sorted(data.items())}

    # 掃描用小檔：每檔最近 15 個交易日的 [日期, 收盤, OSC, 成交量(張)]（OSC 用完整歷史計算，和網頁一樣是 12/26/9）
    scan = {}
    for c, d in data.items():
        e12 = e26 = sig = None
        rows = []
        for day, _o, cl in d["px"]:
            if e12 is None:
                e12 = e26 = cl
                sig = 0.0
            else:
                e12 += 2 / 13 * (cl - e12)
                e26 += 2 / 27 * (cl - e26)
            dif = e12 - e26
            sig = dif if len(rows) == 0 and sig == 0.0 and day == d["px"][0][0] else sig + 2 / 10 * (dif - sig)
            rows.append([day, cl, round(dif - sig, 5)])
        vol = d.get("vol15") or {}
        scan[c] = [r + [vol[r[0]]] if r[0] in vol else r for r in rows[-15:]]
    # 資料日期取「至少一半股票都有資料」的最新一天；Yahoo 有時只先回少數股票當天的資料
    lasts = sorted((r[-1][0] for r in scan.values() if r), reverse=True)
    asof_scan = lasts[len(lasts) // 2] if lasts else ""
    scan = {c: [x for x in r if x[0] <= asof_scan] for c, r in scan.items()}
    with open(os.path.join(OUT, "data", "scan.json"), "w", encoding="utf-8") as f:
        json.dump({"asof": asof_scan, "rows": scan}, f, separators=(",", ":"))
    with open(os.path.join(OUT, "data", "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, separators=(",", ":"))

    # 個股詳細資料：三大法人、融資融券、月營收、財報（失敗不影響網站其他部分；SKIP_FUND=1 可略過）
    fund = {}
    if os.environ.get("SKIP_FUND") != "1":
        try:
            import fundamentals
            days = [r[0] for r in data[ETFS[0]]["px"]]
            fund = fundamentals.build(OUT, days, sorted(data), dt.datetime.now(TZ).date().isoformat()) or {}
        except Exception as e:
            print(f"籌碼／基本面資料失敗（網站其他部分照常）：{e}")

    asof = max(d["px"][-1][0] for d in data.values())
    embed = {e: {k: v for k, v in data[e].items() if k != "vol15"} for e in ETFS}
    embed["_fund"] = fund
    embed["_remote"] = True
    embed["_asof"] = asof
    embed["_stocks"] = ["2330"]
    with open(TEMPLATE, encoding="utf-8") as f:
        tpl = f.read()
    page = tpl.replace("__DATA__", json.dumps(embed, ensure_ascii=False, separators=(",", ":")))
    head = ('<!doctype html>\n<html lang="zh-Hant-TW">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n')
    with open(os.path.join(OUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(head + page + "\n</html>\n")
    open(os.path.join(OUT, ".nojekyll"), "w").close()
    total = sum(os.path.getsize(os.path.join(OUT, "data", x)) for x in os.listdir(os.path.join(OUT, "data")))
    print(f"完成：{len(groups)} 個資料檔，共 {total / 1e6:.1f} MB，資料至 {asof}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
