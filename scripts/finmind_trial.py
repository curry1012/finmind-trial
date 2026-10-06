#!/usr/bin/env python3
"""FinMind 試驗腳本（GitHub Actions 版）
用法：python scripts/finmind_trial.py fetch   # 正式抓取（06:30 起，失敗每 10 分鐘重試，最晚 06:50）
      python scripts/finmind_trial.py probe   # 探測：只記錄各資料最新日期
"""
import os, sys, json, csv, time
import datetime as dt
from zoneinfo import ZoneInfo
import requests

TZ = ZoneInfo("Asia/Taipei")
API = "https://api.finmindtrade.com/api/v4/data"
TOKEN = os.environ.get("FINMIND_TOKEN", "")
TRIGGER = os.environ.get("GITHUB_EVENT_NAME", "manual")
OUT = os.path.join("data", "trial")
DEADLINE = (6, 50)       # 重試最晚時間（台北）
RETRY_SEC = 600          # 重試間隔 10 分鐘
TOL_SEC = 120            # 容許誤差
PLATFORM = "github_actions"
US_SYMBOLS = ["^IXIC", "^SOX", "NVDA", "TSM"]

stats = {"calls": 0, "errors": 0, "error_list": [], "auth_mode": ""}


def now_tpe():
    return dt.datetime.now(TZ)


def iso(t):
    return t.isoformat(timespec="seconds")


def fm(dataset, **params):
    """呼叫 FinMind；先用 Authorization 標頭，失敗再改用 token 參數。回傳 data 清單，失敗回傳 None。"""
    p = {"dataset": dataset, **params}
    modes = [("header", {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}, {}),
             ("param", {}, {"token": TOKEN} if TOKEN else {})]
    for mode, headers, extra in modes:
        for _ in range(2):  # 網路錯誤最多重試 1 次
            stats["calls"] += 1
            try:
                r = requests.get(API, params={**p, **extra}, headers=headers, timeout=30)
                j = r.json()
                if r.status_code == 200 and j.get("status") == 200:
                    stats["auth_mode"] = mode
                    return j.get("data", [])
                stats["errors"] += 1
                stats["error_list"].append(f"{dataset}:{mode}:HTTP{r.status_code}:{j.get('msg')}")
                break  # 此模式失敗，換下一個模式
            except Exception as e:  # noqa
                stats["errors"] += 1
                stats["error_list"].append(f"{dataset}:{mode}:EXC:{type(e).__name__}")
                time.sleep(2)
    return None


def expected_us_date(today):
    d = today - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d.isoformat()


def expected_tw_date(today):
    rows = fm("TaiwanStockTradingDate", start_date=(today - dt.timedelta(days=20)).isoformat())
    if not rows:
        return None
    ds = [r["date"] for r in rows if r["date"] < today.isoformat()]
    return max(ds) if ds else None


def rec(key, data_date, payload):
    return {"key": key, "data_date": data_date, "payload": payload}


def collect(today):
    """抓取全部項目，回傳 (records, flags, expected)。"""
    recs = []
    exp_us = expected_us_date(today)
    exp_tw = expected_tw_date(today)
    start10 = (today - dt.timedelta(days=10)).isoformat()
    start7 = (today - dt.timedelta(days=7)).isoformat()

    us_dates = {}
    for sym in US_SYMBOLS:
        rows = fm("USStockPrice", data_id=sym, start_date=start10)
        if rows and len(rows) >= 2:
            rows.sort(key=lambda r: r["date"])
            last, prev = rows[-1], rows[-2]
            chg = round(last["Close"] - prev["Close"], 2)
            pct = round((last["Close"] / prev["Close"] - 1) * 100, 2)
            recs.append(rec(sym, last["date"], {"close": last["Close"], "prev_date": prev["date"],
                                                "prev_close": prev["Close"], "change": chg, "pct": pct}))
            us_dates[sym] = last["date"]
        else:
            recs.append(rec(sym, None, {"error": "no_data"}))

    taiex_date = None
    rows = fm("TaiwanStockPrice", data_id="TAIEX", start_date=start10)
    if rows:
        rows.sort(key=lambda r: r["date"])
        last = rows[-1]
        spread = last.get("spread")
        pct = round(spread / (last["close"] - spread) * 100, 2) if spread is not None else None
        recs.append(rec("TAIEX", last["date"], {"close": last["close"], "spread": spread, "pct": pct,
                                                "trading_money_100m": round(last["Trading_money"] / 1e8, 2)}))
        taiex_date = last["date"]
    else:
        recs.append(rec("TAIEX", None, {"error": "no_data"}))

    inst_date = None
    rows = fm("TaiwanStockTotalInstitutionalInvestors", start_date=start7)
    if rows:
        inst_date = max(r["date"] for r in rows)
        pl = {r["name"]: {"buy": r["buy"], "sell": r["sell"], "net_100m": round((r["buy"] - r["sell"]) / 1e8, 2)}
              for r in rows if r["date"] == inst_date}
        recs.append(rec("INST", inst_date, pl))
    else:
        recs.append(rec("INST", None, {"error": "no_data"}))

    margin_date = None
    rows = fm("TaiwanStockTotalMarginPurchaseShortSale", start_date=start7)
    if rows:
        margin_date = max(r["date"] for r in rows)
        recs.append(rec("MARGIN", margin_date, {"rows": [r for r in rows if r["date"] == margin_date]}))
    else:
        recs.append(rec("MARGIN", None, {"error": "no_data"}))

    rows = fm("GovernmentBondsYield", data_id="United States 10-Year", start_date=start10)
    if rows:
        rows.sort(key=lambda r: r["date"])
        recs.append(rec("US10Y", rows[-1]["date"], {"value": rows[-1]["value"]}))
    else:
        recs.append(rec("US10Y", None, {"error": "no_data"}))

    flags = {
        "us_ready": us_dates.get("^IXIC") == exp_us and us_dates.get("^SOX") == exp_us,
        "tw_ready": taiex_date == exp_tw and exp_tw is not None,
        "inst_ready": inst_date == exp_tw and exp_tw is not None,
        "margin_ready": margin_date == exp_tw and exp_tw is not None,
    }
    flags["all_ready"] = all(flags.values())
    return recs, flags, {"expected_us_date": exp_us, "expected_tw_date": exp_tw}


def run_fetch():
    os.makedirs(OUT, exist_ok=True)
    start = now_tpe()
    today = start.date()
    deadline = dt.datetime(today.year, today.month, today.day, DEADLINE[0], DEADLINE[1], tzinfo=TZ)
    attempts, expected = [], {}
    n = 0
    while True:
        n += 1
        stats.update({"calls": 0, "errors": 0, "error_list": []})
        t0 = now_tpe()
        recs, flags, expected = collect(today)
        t1 = now_tpe()
        attempts.append({"n": n, "started_at": iso(t0), "finished_at": iso(t1), "flags": flags,
                         "calls": stats["calls"], "errors": stats["errors"],
                         "error_list": stats["error_list"], "auth_mode": stats["auth_mode"], "records": recs})
        print(f"attempt {n} @ {iso(t0)} flags={flags}")
        if flags["all_ready"]:
            break
        if (t1 + dt.timedelta(seconds=RETRY_SEC) - deadline).total_seconds() > TOL_SEC:
            break
        time.sleep(RETRY_SEC)
    end = now_tpe()
    ready_at = next((a["n"] for a in attempts if a["flags"]["all_ready"]), None)
    final = "ready" if ready_at else "not_ready_by_deadline"
    summary = {"platform": PLATFORM, "trigger": TRIGGER, "run_date": today.isoformat(),
               "scheduled_nominal": "06:30", "actual_start": iso(start), "finish": iso(end),
               **expected, "final_status": final, "ready_at_attempt": ready_at, "attempts": attempts}
    fname = f"{today.isoformat()}_{TRIGGER}_{start.strftime('%H%M')}.json"
    with open(os.path.join(OUT, fname), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    log = os.path.join(OUT, "runlog.csv")
    new = not os.path.exists(log)
    last = attempts[-1]
    with open(log, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["run_date", "platform", "trigger", "actual_start", "finish", "duration_s", "attempts",
                        "final_status", "ready_at_attempt", "us_ready", "tw_ready", "inst_ready", "margin_ready",
                        "total_calls", "total_errors", "auth_mode"])
        w.writerow([today.isoformat(), PLATFORM, TRIGGER, iso(start), iso(end), int((end - start).total_seconds()),
                    len(attempts), final, ready_at, last["flags"]["us_ready"], last["flags"]["tw_ready"],
                    last["flags"]["inst_ready"], last["flags"]["margin_ready"],
                    sum(a["calls"] for a in attempts), sum(a["errors"] for a in attempts), last["auth_mode"]])
    print("done:", final, fname)


def run_probe():
    os.makedirs(OUT, exist_ok=True)
    t = now_tpe()
    today = t.date()
    stats.update({"calls": 0, "errors": 0, "error_list": []})
    start10 = (today - dt.timedelta(days=10)).isoformat()
    start7 = (today - dt.timedelta(days=7)).isoformat()

    def latest(dataset, **kw):
        rows = fm(dataset, **kw)
        return max(r["date"] for r in rows) if rows else ""

    row = [iso(t), PLATFORM, TRIGGER, expected_us_date(today), expected_tw_date(today) or "",
           latest("USStockPrice", data_id="^IXIC", start_date=start10),
           latest("USStockPrice", data_id="^SOX", start_date=start10),
           latest("TaiwanStockPrice", data_id="TAIEX", start_date=start10),
           latest("TaiwanStockTotalInstitutionalInvestors", start_date=start7),
           latest("TaiwanStockTotalMarginPurchaseShortSale", start_date=start7),
           stats["errors"]]
    path = os.path.join(OUT, "probe.csv")
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["probed_at", "platform", "trigger", "expected_us_date", "expected_tw_date",
                        "ixic_latest", "sox_latest", "taiex_latest", "inst_latest", "margin_latest", "errors"])
        w.writerow(row)
    print(row)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if mode == "probe":
        run_probe()
    else:
        run_fetch()
