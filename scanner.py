#!/usr/bin/env python3
# AI Penny Scanner — GitHub Actions version
import os, sys, time, requests
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta
import pytz

TOP_N = int(os.getenv("TOP_N", "15"))
MIN_PRICE = float(os.getenv("MIN_PRICE", "0.25"))
MAX_PRICE = float(os.getenv("MAX_PRICE", "5.00"))
MIN_AVG_VOL = int(os.getenv("MIN_AVG_VOL", "200000"))
VOL_RATIO_THRESHOLD = float(os.getenv("VOL_RATIO_THRESHOLD", "3.0"))
PCT_CHANGE_MIN = float(os.getenv("PCT_CHANGE_MIN", "5.0"))
NEWS_LOOKBACK_DAYS = int(os.getenv("NEWS_LOOKBACK_DAYS", "2"))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

def load_universe():
    # GitHub dataset (more reliable than NASDAQ FTP)
    url = "https://raw.githubusercontent.com/datasets/nasdaq-listings/master/data/nasdaq-listed-symbols.csv"
    try:
        dfu = pd.read_csv(url)
        tickers = sorted(dfu['Symbol'].dropna().unique().tolist())
        if len(tickers) < 50:
            raise ValueError("ticker list too small")
        return tickers
    except Exception as e:
        # Fallback sample list
        return ["RR","SNDL","BBIG","GME","AMC","NNDM","GNS","NAKD","CEI","MARA","RIOT"]

def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def scan_universe(tickers):
    results = []
    lookback_days = 30
    for batch in chunk(tickers, 200):
        data = yf.download(batch, period=f"{lookback_days}d", interval="1d", auto_adjust=False, progress=False, threads=True)
        if data is None or data.empty:
            continue
        if isinstance(data.columns, pd.MultiIndex):
            close = data['Close']
            vol   = data['Volume']
        else:
            close = data[['Close']]
            vol   = data[['Volume']]
        for tkr in close.columns:
            c = close[tkr].dropna()
            v = vol[tkr].dropna()
            if len(c) < 5 or len(v) < 5:
                continue
            last_px = float(c.iloc[-1])
            if not (MIN_PRICE <= last_px < MAX_PRICE):
                continue
            avg20 = float(v.tail(20).mean()) if len(v) >= 20 else float(v.mean())
            if avg20 < MIN_AVG_VOL:
                continue
            today_vol = float(v.iloc[-1])
            vol_ratio = today_vol / avg20 if avg20 > 0 else 0.0
            if vol_ratio < VOL_RATIO_THRESHOLD:
                continue
            pct_change = (c.iloc[-1] / c.iloc[-2] - 1.0) * 100.0 if len(c) >= 2 else 0.0
            if pct_change < PCT_CHANGE_MIN:
                continue
            hi20 = float(c.tail(20).max())
            results.append({
                "Ticker": tkr,
                "LastPrice": round(last_px, 4),
                "PctChange": round(pct_change, 2),
                "AvgVol20d": int(avg20),
                "TodayVol": int(today_vol),
                "VolRatio": round(vol_ratio, 2),
                "High20d": round(hi20, 4),
                "Breakout": last_px >= hi20 * 0.995
            })
    return pd.DataFrame(results).sort_values(["VolRatio","PctChange"], ascending=False).reset_index(drop=True)

def news_flag(ticker):
    try:
        news = yf.Ticker(ticker).news or []
        cutoff = datetime.utcnow() - timedelta(days=NEWS_LOOKBACK_DAYS)
        recent = [n for n in news if datetime.utcfromtimestamp(n.get("providerPublishTime", 0)) >= cutoff]
        return "Yes" if recent else "No"
    except Exception:
        return "Unknown"

def format_discord(df, top_n=10):
    if df is None or df.empty:
        return "**Penny Scan Alert**\\nNo candidates today."
    df = df.head(top_n).copy()
    lines = ["**Penny Scan Alert**"]
    for _, r in df.iterrows():
        lines.append(
            (f"\\n**{r['Ticker']}**  ${r['LastPrice']} | "
             f"Entry {r['Entry']} | Stop {r['Stop']} | T1 {r['Target1']} | T2 {r['Target2']} | "
             f"Vol× {r['VolRatio']} | Chg {r['PctChange']}% | News48h {r['RecentNews48h']}")
        )
    return "\\n".join(lines)

def main():
    tickers = load_universe()
    scan = scan_universe(tickers)

    if not scan.empty:
        scan["RecentNews48h"] = scan["Ticker"].apply(news_flag)
        def plan_rows(r):
            px = r["LastPrice"]
            entry = round(max(MIN_PRICE, px * 0.97), 4)
            stop  = round(entry * 0.90, 4)
            t1    = round(entry * 1.12, 4)
            t2    = round(entry * 1.25, 4)
            return pd.Series({"Entry":entry, "Stop":stop, "Target1":t1, "Target2":t2})
        scan[["Entry","Stop","Target1","Target2"]] = scan.apply(plan_rows, axis=1)
        cols = ["Ticker","LastPrice","Entry","Stop","Target1","Target2","VolRatio","PctChange","RecentNews48h"]
        scan = scan[cols]
        out_csv = f"penny_scan_simple_{datetime.utcnow().date()}.csv"
        scan.head(TOP_N).to_csv(out_csv, index=False)
        print("Saved:", out_csv)
        # Post to Discord
        if DISCORD_WEBHOOK_URL:
            msg = format_discord(scan, top_n=min(TOP_N, 10))
            try:
                r = requests.post(DISCORD_WEBHOOK_URL, json={"content": msg[:1900]}, timeout=20)
                print("Discord status:", r.status_code, r.text[:120])
            except Exception as e:
                print("Discord post error:", e)
        else:
            print("No DISCORD_WEBHOOK_URL set; skipping Discord.")
    else:
        print("No candidates found today.")
        if DISCORD_WEBHOOK_URL:
            try:
                r = requests.post(DISCORD_WEBHOOK_URL, json={"content": "**Penny Scan Alert**\\nNo candidates today."}, timeout=20)
                print("Discord status:", r.status_code, r.text[:120])
            except Exception as e:
                print("Discord post error:", e)

if __name__ == "__main__":
    main()
