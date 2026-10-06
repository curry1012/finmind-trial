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
