#!/usr/bin/env python3
import json, os, pandas as pd

def calc_weight(d1_chg, d2_pb):
    if d1_chg >= 8 and 3 <= d2_pb < 5: return 2.5
    if d1_chg >= 8: return 1.5
    if d1_chg >= 6 and 3 <= d2_pb < 5: return 1.5
    if d2_pb < 2: return 0.5
    return 1.0

src = r"C:\Users\Administrator\Desktop\quant_pulse\backtest\backtest_d1d2_v5_results.json"
with open(src, "r", encoding="utf-8") as f:
    trades = json.load(f)

for t in trades:
    t["weight"] = calc_weight(t["d1_chg"], t["d2_pullback"])

df = pd.DataFrame(trades)
n = len(df)
wins = df[df["is_win"]]
wr = len(wins)/n*100
mp = df["pnl_pct"].mean()
wavg = (df["pnl_pct"] * df["weight"]).sum() / df["weight"].sum()

print(f"v6 权重分析")
print(f"总: {n}笔  胜率: {wr:.1f}%")
print(f"未加权: {mp:+.2f}%  加权: {wavg:+.2f}%  提升: {wavg-mp:+.2f}%")
print()
for w in sorted(df["weight"].unique(), reverse=True):
    sub = df[df["weight"] == w]
    print(f"  {w:.1f}x: {len(sub)}笔({len(sub)/n*100:.1f}%) {sub[is_win].mean()*100:.1f}% {sub[pnl_pct].mean():+.2f}%")

