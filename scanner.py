#!/usr/bin/env python3
# AI Penny Scanner — hardened: retries, throttles, safe batch dl, best-effort everything

import os, sys, time, math, requests, traceback
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

# -------- Env / defaults --------
TOP_N = int(os.getenv("TOP_N", "8"))
MIN_PRICE = float(os.getenv("MIN_PRICE", "0.50"))
MAX_PRICE = float(os.getenv("MAX_PRICE", "3.00"))
MIN_AVG_VOL = int(os.getenv("MIN_AVG_VOL", "500000"))
VOL_RATIO_THRESHOLD = float(os.getenv("VOL_RATIO_THRESHOLD", "4.0"))
PCT_CHANGE_MIN = float(os.getenv("PCT_CHANGE_MIN", "8.0"))
NEWS_LOOKBACK_DAYS = int(os.getenv("NEWS_LOOKBACK_DAYS", "0"))  # keep 0 for now
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# -------- Helpers --------
def log(msg):
    print(msg, flush=True)

def safe_get(url, retries=3, sleep_s=2):
    for i in range(retries):
        try:
            return requests.get(url, timeout=20)
        except Exception as e:
            log(f"[WARN] GET failed (attempt {i+1}/{retries}): {e}")
            time.sleep(sleep_s)
    return None

def load_universe(max_symbols=600):
    # Use reliable GitHub list
    url = "https://raw.githubusercontent.com/datasets/nasdaq-listings/master/data/nasdaq-listed-symbols.csv"
    r = safe_get(url)
    if r and r.status_code == 200:
        try:
            dfu = pd.read_csv(pd.compat.StringIO(r.text))
        except Exception:
            # pandas 2.x: no compat.StringIO. Fallback:
            from io import StringIO
            dfu = pd.read_csv(StringIO(r.text))
        tickers = sorted(dfu['Symbol'].dropna().unique().tolist())
    else:
        tickers = []

    if not tickers:
        # Fallback sample
        tickers = ["RR","SNDL","BBIG","GME","AMC","NNDM","GNS","NAKD","CEI","MARA","RIOT"]

    # Clean & cap
    tickers = [t for t in tickers if isinstance(t, str) and t.isalpha() and 1 <= len(t) <= 5]
    return tickers[:max_symbols]

def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i+n]

def dl_batch(batch, lookback_days=30, retries=3):
    """Download OHLCV for a batch safely, no threads, with retries."""
    for i in range(retries):
        try:
            data = yf.download(
                batch,
                period=f"{lookback_days}d",
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,  # <- reliability > speed
                group_by="ticker",
            )
            if data is None or data.empty:
                raise ValueError("empty batch download")
            return data
        except Exception as e:
            log(f"[WARN] batch dl failed (attempt {i+1}/{retries}) for {len(batch)} tickers: {e}")
            time.sleep(2 + i*2)
    return None

def extract_series(data, tkr):
    """Return close, vol series for ticker across both MultiIndex and dict-like formats safely."""
    try:
        # When group_by="ticker", data behaves like a dict of DataFrames per ticker
        if isinstance(data, dict) or (hasattr(data, "keys") and tkr in data):
            df = data[tkr]
            return df["Close"].dropna(), df["Volume"].dropna()
        # Older MultiIndex style
        if isinstance(data.columns, pd.MultiIndex):
            return data["Close"][tkr].dropna(), data["Volume"][tkr].dropna()
        # Single ticker fallback
        if "Close" in data and "Volume" in data:
            return data["Close"].dropna(), data["Volume"].dropna()
    except Exception:
        pass
    return pd.Series(dtype=float), pd.Series(dtype=float)

def news_flag(ticker: str) -> str:
    """Best-effort news flag; never crash."""
    try:
        lookback = NEWS_LOOKBACK_DAYS
    except Exception:
        lookback = 0
    if lookback <= 0:
        return "No"
    try:
        nlist = yf.Ticker(ticker).news or []
        cutoff = datetime.utcnow() - timedelta(days=lookback)
        for n in nlist:
            ts = n.get("providerPublishTime") or n.get("publishedAt") or n.get("time_published")
            if not ts:
                continue
            try:
                ts_dt = datetime.utcfromtimestamp(int(ts))
            except Exception:
                continue
            if ts_dt >= cutoff:
                return "Yes"
        return "No"
    except Exception:
        return "Unknown"

def format_discord(df, top_n=10):
    if df is None or df.empty:
        return "**Penny Scan Alert**\nNo candidates today."
    df = df.head(top_n).copy()
    lines = ["**Penny Scan Alert**"]
    for _, r in df.iterrows():
        lines.append(
            (f"\n**{r['Ticker']}**  ${r['LastPrice']} | "
             f"Entry {r['Entry']} | Stop {r['Stop']} | T1 {r['Target1']} | T2 {r['Target2']} | "
             f"Vol× {r['VolRatio']} | Chg {r['PctChange']}% | News48h {r.get('RecentNews48h','No')}")
        )
    return "\n".join(lines)

# -------- Main scan --------
def scan_universe(tickers):
    results = []
    lookback_days = 30
    BATCH = 100  # small batches for reliability

    for batch in chunk(tickers, BATCH):
        time.sleep(0.3)  # batch throttle
        data = dl_batch(batch, lookback_days=lookback_days, retries=3)
        if data is None:
            log(f"[WARN] skipping batch of {len(batch)} (still None after retries)")
            continue

        for t in batch:
            try:
                time.sleep(0.03)  # per-ticker throttle
                c, v = extract_series(data, t)
                if c.empty or v.empty or len(c) < 5 or len(v) < 5:
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
                    "Ticker": t,
                    "LastPrice": round(last_px, 4),
                    "PctChange": round(pct_change, 2),
                    "AvgVol20d": int(avg20),
                    "TodayVol": int(today_vol),
                    "VolRatio": round(vol_ratio, 2),
                    "High20d": round(hi20, 4),
                    "Breakout": last_px >= hi20 * 0.995
                })
            except Exception as e:
                # Never fail the run due to a single ticker
                log(f"[WARN] ticker {t} skipped: {e}")
                continue

    if not results:
        return pd.DataFrame(columns=["Ticker","LastPrice","PctChange","AvgVol20d","TodayVol","VolRatio","High20d","Breakout"])

    df = pd.DataFrame(results).sort_values(["VolRatio","PctChange"], ascending=False).reset_index(drop=True)
    return df

def main():
    log("[INFO] loading universe...")
    tickers = load_universe(max_symbols=600)
    log(f"[INFO] universe size: {len(tickers)}")

    log("[INFO] scanning...")
    scan = scan_universe(tickers)

    if not scan.empty:
        # Optional news (respects env)
        if NEWS_LOOKBACK_DAYS > 0:
            log("[INFO] adding news flags...")
            scan["RecentNews48h"] = scan["Ticker"].apply(news_flag)
        else:
            scan["RecentNews48h"] = "No"

        # Plan fields
        def plan_rows(r):
            px = r["LastPrice"]
            entry = round(max(MIN_PRICE, px * 0.97), 4)  # ~3% pullback
            stop  = round(entry * 0.90, 4)               # ~10% risk
            t1    = round(entry * 1.12, 4)               # +12%
            t2    = round(entry * 1.25, 4)               # +25%
            return pd.Series({"Entry":entry, "Stop":stop, "Target1":t1, "Target2":t2})

        scan[["Entry","Stop","Target1","Target2"]] = scan.apply(plan_rows, axis=1)
        cols = ["Ticker","LastPrice","Entry","Stop","Target1","Target2","VolRatio","PctChange","RecentNews48h"]
        scan = scan[cols]

        out_csv = f"penny_scan_simple_{datetime.utcnow().date()}.csv"
        scan.head(TOP_N).to_csv(out_csv, index=False)
        log(f"[INFO] saved: {out_csv}")

        if DISCORD_WEBHOOK_URL:
            msg = format_discord(scan, top_n=min(TOP_N, 10))
            try:
                r = requests.post(DISCORD_WEBHOOK_URL, json={"content": msg[:1900]}, timeout=20)
                log(f"[INFO] discord status: {r.status_code}")
            except Exception as e:
                log(f"[WARN] discord post error: {e}")
        else:
            log("[INFO] no DISCORD_WEBHOOK_URL set; skipping Discord.")
    else:
        log("[INFO] no candidates found today.")
        if DISCORD_WEBHOOK_URL:
            try:
                r = requests.post(DISCORD_WEBHOOK_URL, json={"content": "**Penny Scan Alert**\nNo candidates today."}, timeout=20)
                log(f"[INFO] discord status: {r.status_code}")
            except Exception as e:
                log(f"[WARN] discord post error: {e}")

if __name__ == "__main__":
    main()