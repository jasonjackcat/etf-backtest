# -*- coding: utf-8 -*-
"""
「法人對作」分頁的資料：外資買超、投信賣超的個股，每天一份名單，附上之後的報酬（給網頁篩選與回測用），由 build_site.py 每天呼叫。

累積資料（只留外資和投信方向相反的個股：外資買投信賣、外資賣投信買；排除 00 開頭的 ETF）：
  先讀上一版網站發佈的 data/flow_cache.json；沒有的話讀 repo 裡的 flow_seed.json（第一次用本機歷史資料補齊）。
  最近 20 天直接用 fundamentals.py 剛抓好的三大法人（data/fund_cache.json）；
  其他缺的交易日每次最多補抓 BACKFILL 天（新的先補），慢慢補齊最近 KEEP 個交易日。

輸出：
  data/flow_cache.json  {"inst": {日期: {代號: [外資張, 投信張]}}}（下次建置用）
  data/flow.json        {"asof", "hold", "days": [日期…], "ev": [每天的名單 或 null（那天沒有法人資料）]}
     "rv": [每天 外資賣超、投信買超 的代號清單]（給「法人反轉」條件用：先外資賣投信買，再翻成外資買投信賣）
     名單每列：[代號, 外資買超張, 投信買賣超張(負數), 成交量張, 收盤, 漲跌%, 持有 1/3/5/10/20 天報酬%…]
     報酬：訊號日隔天開盤買進、第 h 個交易日收盤賣出（進場那天算第 1 天），含期間配息、已調整分割，未扣交易成本；還沒到期是 null
任何失敗都只印訊息，不影響網站其他部分。
"""
import json
import os
import time

import fundamentals as F

KEEP = 750          # 保留最近幾個交易日（約 3 年）
BACKFILL = 40       # 每次最多補抓幾天
HOLD = [1, 3, 5, 10, 20]


def log(msg):
    print(f"[法人對作] {msg}", flush=True)


CACHE_V = 2        # 2＝兩個方向都有（v1 只有外資買投信賣，不能用）


def sparse(day_map):
    """{代號: [外, 投, 自]} → 只留外資和投信方向相反的個股 {代號: [外, 投]}"""
    return {c: [v[0], v[1]] for c, v in day_map.items()
            if not c.startswith("00") and v[0] * v[1] < 0}


def load_hist(here):
    try:
        c = json.loads(F.fetch(F.PAGES_URL.rstrip("/") + "/data/flow_cache.json", timeout=60, tries=2).decode("utf-8"))
        if c.get("v") == CACHE_V:
            log(f"讀到上一版的累積資料（{len(c.get('inst', {}))} 天）")
            return c.get("inst", {})
        log("上一版的累積資料是舊格式，改用 flow_seed.json")
    except Exception as e:
        log(f"沒有上一版的累積資料：{e}")
    seed = os.path.join(here, "flow_seed.json")
    if os.path.exists(seed):
        with open(seed, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("v") != CACHE_V:
            log("flow_seed.json 是舊格式，不使用")
            return {}
        log(f"改用 flow_seed.json（{len(c.get('inst', {}))} 天）")
        return c.get("inst", {})
    return {}


def fetch_day(day):
    got = {}
    for fn in (F.inst_twse, F.inst_tpex):
        try:
            r = fn(day)
            if r:
                got.update(r)
        except Exception as e:
            log(f"{day} {fn.__name__} 失敗：{e}")
        time.sleep(3 if "twse" in fn.__name__ else 1)
    return got


def idx_of(code, d, cache):
    """{日期: 在 px 裡的位置}（每檔只建一次）"""
    if code not in cache:
        cache[code] = {r[0]: i for i, r in enumerate(d.get("px") or [])}
    return cache[code]


def event_row(code, day, f, t, d, idx_map):
    """一筆訊號的整列；這檔那天沒有交易就回傳 None"""
    px = d.get("px") or []
    i = idx_map.get(day)
    if i is None or i == 0:
        return None
    hlv = d.get("hlv") or []
    hi = i - (len(px) - len(hlv))
    vol = hlv[hi][2] if 0 <= hi < len(hlv) else None
    close, prev = px[i][2], px[i - 1][2]
    chg = round((close / prev - 1) * 100, 2) if prev else None
    divs = d.get("div") or {}
    splits = d.get("splits") or []
    rets = []
    for h in HOLD:
        e, x = i + 1, i + h
        if x >= len(px) or not px[e][1]:
            rets.append(None)
            continue
        d0, d1 = px[e][0], px[x][0]
        ratio = 1.0
        for sd, r in splits:
            if d0 < sd <= d1 and r:
                ratio *= r
        cash = sum(v for k, v in divs.items() if d0 < k <= d1)
        rets.append(round(((px[x][2] * ratio + cash) / px[e][1] - 1) * 100, 2))
    return [code, f, t, vol, round(close, 2), chg] + rets


def build(out_dir, data, here, days_all):
    """data＝build_site 的全部個股資料；days_all＝交易日（由舊到新，用 0050 的日期）"""
    days = days_all[-KEEP:]
    hist = load_hist(here)
    # 最近 20 天：用 fundamentals.py 剛抓好的
    fc = os.path.join(out_dir, "data", "fund_cache.json")
    if os.path.exists(fc):
        try:
            with open(fc, encoding="utf-8") as f:
                for day, m in json.load(f).get("inst", {}).items():
                    if m:
                        hist[day] = sparse(m)
        except Exception as e:
            log(f"讀 fund_cache.json 失敗：{e}")
    missing = [d for d in reversed(days) if d not in hist]
    if missing:
        log(f"缺 {len(missing)} 天，這次補抓 {min(len(missing), BACKFILL)} 天")
        for day in missing[:BACKFILL]:
            got = fetch_day(day)
            if got:
                hist[day] = sparse(got)
    keep = set(days)
    hist = {k: v for k, v in hist.items() if k in keep}
    os.makedirs(os.path.join(out_dir, "data"), exist_ok=True)
    with open(os.path.join(out_dir, "data", "flow_cache.json"), "w", encoding="utf-8") as f:
        json.dump({"v": CACHE_V, "inst": hist}, f, ensure_ascii=False, separators=(",", ":"))

    idx_cache, ev, rv, n = {}, [], [], 0
    for day in days:
        m = hist.get(day)
        if m is None:
            ev.append(None)
            rv.append(None)
            continue
        rv.append(sorted(c for c, (fo, it) in m.items() if fo < 0))
        rows = []
        for code, (fo, it) in m.items():
            if fo <= 0:
                continue
            d = data.get(code)
            if not d:
                continue
            r = event_row(code, day, fo, it, d, idx_of(code, d, idx_cache))
            if r:
                rows.append(r)
        rows.sort(key=lambda r: -r[1])
        ev.append(rows)
        n += len(rows)
    have = [d for d in days if hist.get(d) is not None]
    out = {"asof": have[-1] if have else None, "hold": HOLD, "days": days, "ev": ev, "rv": rv}
    with open(os.path.join(out_dir, "data", "flow.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    log(f"完成：{len(have)}/{len(days)} 天有法人資料，訊號 {n} 筆，資料至 {out['asof']}")
    return out["asof"]
