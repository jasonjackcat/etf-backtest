# -*- coding: utf-8 -*-
"""
個股籌碼與基本面資料（給網頁「點個股」的詳細資料用），由 build_site.py 每天呼叫。

來源（都是公開的盤後資料）：
  三大法人買賣超：證交所 T86、櫃買中心 3itrade_hedge_result
  融資融券餘額：  證交所 MI_MARGN、櫃買中心 margin_bal_result
  月營收：        公開資訊觀測站 t21sc03（mopsov 舊版站）
  綜合損益（EPS、毛利率…）：公開資訊觀測站 ajax_t163sb04

為了少抓、也避免單一來源失敗就沒資料：每次先下載上一版網站發佈的 data/fund_cache.json，
只補抓新的日期／月份／季別，再存回去。任何來源失敗都只印訊息，不影響網站其他部分。

輸出：
  data/fund_cache.json   原始累積資料（下次建置用）
  data/f<代號前3碼>.json  每檔整理好的：inst（近 20 日法人）、mg（近 20 日融資融券）、rev（近 13 個月營收）、q（近 8 季）
"""
import html.parser
import json
import os
import re
import time
import urllib.parse
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (etf-backtest site builder)"}
PAGES_URL = os.environ.get("PAGES_URL", "https://jasonjackcat.github.io/etf-backtest/")


def log(msg):
    print("[籌碼／基本面] " + msg, flush=True)


def fetch(url, data=None, timeout=60, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, data=data, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:   # 網路不穩就重試
            last = e
            time.sleep(3 * (i + 1))
    raise last


def text(b):
    for enc in ("utf-8", "cp950", "big5"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode("utf-8", "ignore")


def num(s):
    s = str(s).replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "--", "N/A", "不適用"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def roc(day):
    """2026-11-11 → 115/11/11"""
    y, m, d = day.split("-")
    return f"{int(y) - 1911}/{m}/{d}"


class Tables(html.parser.HTMLParser):
    """很簡單的 HTML 表格解析：回傳 [表格[列[儲存格文字]]]。"""

    def __init__(self):
        super().__init__()
        self.tables, self.row, self.cell, self.depth = [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
            self.depth += 1
        elif tag == "tr" and self.depth:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None and self.depth:
            if self.row:
                self.tables[-1].append(self.row)
            self.row = None
        elif tag == "table" and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def parse_tables(html_text):
    p = Tables()
    p.feed(html_text)
    return p.tables


# ───────── 三大法人 ─────────

def inst_twse(day):
    d = json.loads(text(fetch(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={day.replace('-', '')}&selectType=ALLBUT0999&response=json")))
    if d.get("stat") != "OK" or not d.get("data"):
        return None
    f = d["fields"]

    def col(*names):
        for n in names:
            if n in f:
                return f.index(n)
        return None
    fi = col("外陸資買賣超股數(不含外資自營商)")
    fd = col("外資自營商買賣超股數")
    it = col("投信買賣超股數")
    dl = col("自營商買賣超股數")
    out = {}
    for r in d["data"]:
        code = r[0].strip()
        fo = (num(r[fi]) or 0) + ((num(r[fd]) or 0) if fd is not None else 0)
        out[code] = [round(fo / 1000), round((num(r[it]) or 0) / 1000), round((num(r[dl]) or 0) / 1000)]
    return out


def inst_tpex(day):
    d = json.loads(text(fetch(f"https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&o=json&se=EW&t=D&d={roc(day)}")))
    rows = (d.get("tables") or [{}])[0].get("data") or []
    if not rows:
        return None
    out = {}
    for r in rows:   # 10＝外資及陸資合計淨買、13＝投信淨買、22＝自營商合計淨買（股）
        out[r[0].strip()] = [round((num(r[10]) or 0) / 1000), round((num(r[13]) or 0) / 1000), round((num(r[22]) or 0) / 1000)]
    return out


# ───────── 融資融券 ─────────

def margin_twse(day):
    d = json.loads(text(fetch(f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date={day.replace('-', '')}&selectType=ALL&response=json")))
    if d.get("stat") != "OK":
        return None
    for t in d.get("tables") or []:
        f = t.get("fields") or []
        if t.get("data") and len(f) >= 13 and f[0] == "代號":   # 個股表：6＝融資今日餘額、12＝融券今日餘額（張）
            return {r[0].strip(): [int(num(r[6]) or 0), int(num(r[12]) or 0)] for r in t["data"]}
    return None


def margin_tpex(day):
    d = json.loads(text(fetch(f"https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php?l=zh-tw&o=json&d={roc(day)}")))
    rows = (d.get("tables") or [{}])[0].get("data") or []
    if not rows:
        return None
    return {r[0].strip(): [int(num(r[6]) or 0), int(num(r[14]) or 0)] for r in rows}   # 6＝資餘額、14＝券餘額（張）


# ───────── 月營收 ─────────

def revenue(ym):
    """ym＝'2026-10' → {代號: [當月營收(千元), 去年同月增減%]}；還沒公布回傳 None。"""
    y, m = ym.split("-")
    out = {}
    for kind in ("sii", "otc"):
        for suffix in ("_0", "_1"):   # _0 國內公司、_1 國外（KY）公司
            url = f"https://mopsov.twse.com.tw/nas/t21/{kind}/t21sc03_{int(y) - 1911}_{int(m)}{suffix}.html"
            try:
                t = text(fetch(url, timeout=90, tries=2))
            except Exception:
                continue
            for tb in parse_tables(t):
                for r in tb:
                    if len(r) >= 7 and re.fullmatch(r"[0-9A-Z]{4,6}", r[0] or ""):
                        rev, yoy = num(r[2]), num(r[6])
                        if rev is not None:
                            out[r[0]] = [rev, yoy]
            time.sleep(1.5)
    return out or None


# ───────── 綜合損益（累計值） ─────────

def income(year, season):
    """{代號: [營收, 毛利, 營業利益, 稅後淨利, EPS]}，都是年初累計到這一季（金額千元、EPS 元）。"""
    out = {}
    for kind in ("sii", "otc"):
        data = urllib.parse.urlencode({"encodeURIComponent": 1, "step": 1, "firstin": 1, "off": 1, "isQuery": "Y",
                                       "TYPEK": kind, "year": str(year - 1911), "season": f"{season:02d}"}).encode()
        try:
            t = text(fetch("https://mopsov.twse.com.tw/mops/web/ajax_t163sb04", data=data, timeout=120, tries=2))
        except Exception as e:
            log(f"{year}Q{season} {kind} 綜合損益抓取失敗：{e}")
            continue
        for tb in parse_tables(t):
            if not tb or "公司代號" not in tb[0]:
                continue
            h = tb[0]

            def find(test):
                for i, x in enumerate(h):
                    if test(x):
                        return i
                return None
            i_rev = find(lambda x: x == "營業收入") or find(lambda x: x in ("收益", "淨收益", "收入")) or find(lambda x: "營業收入" in x)
            i_gp = find(lambda x: x.startswith("營業毛利"))
            i_op = find(lambda x: x.startswith("營業利益"))
            i_ni = find(lambda x: x.startswith("本期淨利") or x.startswith("本期稅後淨利"))
            i_eps = find(lambda x: "基本每股盈餘" in x)
            if i_eps is None:
                continue
            for r in tb[1:]:
                if len(r) != len(h) or not re.fullmatch(r"[0-9A-Z]{4,6}", r[0] or ""):
                    continue
                g = lambda i: num(r[i]) if i is not None else None
                out[r[0]] = [g(i_rev), g(i_gp), g(i_op), g(i_ni), g(i_eps)]
        time.sleep(3)
    return out or None


# ───────── 主流程 ─────────

def load_cache():
    try:
        b = fetch(PAGES_URL.rstrip("/") + "/data/fund_cache.json", timeout=60, tries=2)
        c = json.loads(b.decode("utf-8"))
        log("讀到上一版的累積資料")
        return c
    except Exception as e:
        log(f"沒有上一版的累積資料（第一次建置或網站還沒發佈）：{e}")
        return {}


def months_back(last_ym, n):
    y, m = map(int, last_ym.split("-"))
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def build(out_dir, trade_days, codes, today):
    """trade_days＝最近的交易日（由舊到新）；codes＝網站上有的代號；today＝'YYYY-MM-DD'。"""
    cache = load_cache()
    inst = cache.get("inst", {})
    mg = cache.get("mg", {})
    rev = cache.get("rev", {})
    inc = cache.get("inc", {})
    days = trade_days[-20:]

    # 三大法人、融資融券：最近 20 個交易日，已經有的不重抓（最新一天每次都重抓）
    for day in days:
        for store, name, fns in ((inst, "三大法人", (inst_twse, inst_tpex)), (mg, "融資融券", (margin_twse, margin_tpex))):
            if day in store and day != days[-1]:
                continue
            got = {}
            for fn in fns:
                try:
                    r = fn(day)
                    if r:
                        got.update(r)
                except Exception as e:
                    log(f"{day} {name} {fn.__name__} 失敗：{e}")
                time.sleep(3 if "twse" in fn.__name__ else 1)
            if got:
                store[day] = got
    for store in (inst, mg):
        for k in [k for k in store if k not in days]:
            del store[k]

    # 月營收：近 13 個月（今天往前推一個月開始）；最新兩個月每次重抓（公司陸續公布）
    y, m = map(int, today[:7].split("-"))
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    months = months_back(f"{y:04d}-{m:02d}", 14)
    for i, ym in enumerate(months):
        if ym in rev and i >= 2:
            continue
        try:
            r = revenue(ym)
            if r:
                rev[ym] = r
                log(f"{ym} 月營收 {len(r)} 家")
        except Exception as e:
            log(f"{ym} 月營收失敗：{e}")
    for k in [k for k in rev if k not in months]:
        del rev[k]

    # 綜合損益：近 3 年各季（累計值）；最新兩季每次重抓
    ty = int(today[:4])
    seasons = [f"{yy}Q{s}" for yy in range(ty, ty - 3, -1) for s in (4, 3, 2, 1)]
    seasons = [q for q in seasons if (int(q[:4]), int(q[-1])) <= (ty, 4)]
    fetched = 0
    for q in seasons:
        yy, s = int(q[:4]), int(q[-1])
        due = {1: "05-15", 2: "08-14", 3: "11-14", 4: "03-31"}[s]
        due_day = f"{yy + 1 if s == 4 else yy}-{due}"
        if today < (f"{yy}-04-20" if s == 1 else f"{yy}-07-20" if s == 2 else f"{yy}-10-20" if s == 3 else f"{yy + 1}-01-20"):
            continue   # 這一季還沒結束太久，不會有財報
        fresh = today <= _add_days(due_day, 20)   # 公布截止日後 20 天內持續重抓
        if q in inc and not fresh:
            continue
        if fetched >= 8:   # 一次最多抓 8 季，避免第一次建置太久
            break
        try:
            r = income(yy, s)
            fetched += 1
            if r:
                inc[q] = r
                log(f"{q} 綜合損益 {len(r)} 家")
        except Exception as e:
            log(f"{q} 綜合損益失敗：{e}")
    for k in [k for k in inc if k not in seasons]:
        del inc[k]

    cache = {"inst": inst, "mg": mg, "rev": rev, "inc": inc, "updated": today}
    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)
    with open(os.path.join(out_dir, "data", "fund_cache.json"), "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))

    # 每檔整理
    per = {}
    inst_days = [d for d in days if d in inst]
    mg_days = [d for d in days if d in mg]
    rev_months = sorted(rev)[-13:]
    q_sorted = sorted(inc, key=lambda q: (int(q[:4]), int(q[-1])))
    for code in codes:
        e = {}
        rows = [[d] + inst[d][code] for d in inst_days if code in inst[d]]
        if rows:
            e["inst"] = rows
        rows = [[d] + mg[d][code] for d in mg_days if code in mg[d]]
        if rows:
            e["mg"] = rows
        rows = [[ym] + rev[ym][code] for ym in rev_months if code in rev[ym]]
        if rows:
            e["rev"] = rows
        qs = []
        for q in q_sorted:   # 累計 → 單季
            cur = inc[q].get(code)
            if not cur:
                continue
            yy, s = int(q[:4]), int(q[-1])
            if s == 1:
                single = cur
            else:
                prev = inc.get(f"{yy}Q{s - 1}", {}).get(code)
                if not prev:
                    continue
                single = [None if a is None or b is None else a - b for a, b in zip(cur, prev)]
            rv, gp, op, ni, eps = single
            pct = lambda x: round(x / rv * 100, 2) if x is not None and rv else None
            qs.append([q, None if eps is None else round(eps, 2), pct(gp), pct(op), pct(ni), None if rv is None else round(rv)])
        if qs:
            e["q"] = qs[-8:]
        if e:
            per[code] = e
    groups = {}
    for code, e in per.items():
        groups.setdefault(code[:3], {})[code] = e
    for g, items in groups.items():
        with open(os.path.join(out_dir, "data", "f" + g + ".json"), "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, separators=(",", ":"))
    log(f"完成：{len(per)} 檔有資料（法人 {len(inst_days)} 天、融資融券 {len(mg_days)} 天、月營收 {len(rev_months)} 個月、財報 {len(q_sorted)} 季）")
    return {"inst": inst_days[-1] if inst_days else None, "rev": rev_months[-1] if rev_months else None,
            "q": q_sorted[-1] if q_sorted else None}


def _add_days(day, n):
    import datetime as dt
    return (dt.date.fromisoformat(day) + dt.timedelta(days=n)).isoformat()
