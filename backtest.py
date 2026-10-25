import asyncio
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, Any, List
from src.exchange.bybit_client import BybitSpotClient
from src.core.analyzer import MarketAnalyzer

async def run_backtest(symbol: str = "BTCUSDT", interval: str = "60", limit: int = 200):
    """
    Simulates:
    1. Dumb Scheduled DCA: Buys fixed $15 every 24 bars.
    2. Smart Piggybank DCA: Base $15 DCA + Bollinger Dip Multipliers (1.5x, 2.5x, 4x) + Profit Harvester (+12% trim).
    """
    print(f"============================================================")
    print(f"   [BACKTEST SIMULATION]: Dumb DCA vs Bybit Smart Piggybank")
    print(f"   Asset: {symbol} | Timeframe: 1h | History: {limit} candles")
    print(f"============================================================")

    client = BybitSpotClient(dry_run=True)
    klines = await client.get_klines(symbol, interval=interval, limit=limit)

    if len(klines) < 30:
        print("Error: Insufficient historical candles fetched.")
        return

    # Benchmark 1: Dumb DCA
    dumb_total_usd = 0.0
    dumb_coins = 0.0

    # Benchmark 2: Smart Piggybank
    smart_total_usd = 0.0
    smart_coins = 0.0
    smart_usdt_harvested = 0.0
    dip_buys_count = 0
    harvest_count = 0

    base_dca_usd = 15.0

    for i in range(25, len(klines)):
        sub_klines = klines[:i+1]
        current_candle = klines[i]
        price = current_candle["close"]

        # 1. Dumb DCA (Every 24 hours / 24 candles)
        if i % 24 == 0:
            dumb_total_usd += base_dca_usd
            dumb_coins += base_dca_usd / price

        # 2. Smart Piggybank DCA Logic
        analysis = MarketAnalyzer.calculate_indicators(sub_klines)
        if not analysis:
            continue

        # Regular DCA schedule
        if i % 24 == 0:
            smart_total_usd += base_dca_usd
            smart_coins += base_dca_usd / price

        # Smart Dip-Buying Check
        is_dip, dip_tier, mult = MarketAnalyzer.evaluate_dip(analysis)
        if is_dip and (i % 4 == 0):  # 4h cooldown
            dip_amt = base_dca_usd * mult
            smart_total_usd += dip_amt
            smart_coins += dip_amt / price
            dip_buys_count += 1

        # Profit Harvester Check
        avg_buy_price = (smart_total_usd / smart_coins) if smart_coins > 0 else price
        should_harvest, reason, harvest_pct = MarketAnalyzer.evaluate_harvest(
            analysis, avg_buy_price=avg_buy_price, min_profit_pct=8.0, rsi_overbought=70.0
        )
        if should_harvest and smart_coins > 0:
            sell_coins = smart_coins * (harvest_pct / 100.0)
            revenue = sell_coins * price
            smart_coins -= sell_coins
            smart_usdt_harvested += revenue
            harvest_count += 1

    final_price = klines[-1]["close"]

    # Valuations
    dumb_val = dumb_coins * final_price
    dumb_pnl_pct = ((dumb_val - dumb_total_usd) / dumb_total_usd * 100.0) if dumb_total_usd > 0 else 0.0

    smart_net_invested = max(1.0, smart_total_usd - smart_usdt_harvested)
    smart_val = (smart_coins * final_price) + smart_usdt_harvested
    smart_pnl_pct = ((smart_val - smart_total_usd) / smart_total_usd * 100.0) if smart_total_usd > 0 else 0.0

    print(f"\n[Final Market Price for {symbol}: ${final_price:,.2f}]")
    print("\n--- 1. Dumb DCA (Fixed periodic buy) ---")
    print(f"Total Invested:   ${dumb_total_usd:.2f}")
    print(f"Coins Holdings:   {dumb_coins:.6f} {symbol.replace('USDT','')}")
    print(f"Portfolio Value:  ${dumb_val:.2f}")
    print(f"Return (PnL):     {dumb_pnl_pct:+.2f}%")

    print("\n--- 2. Bybit Smart Piggybank (Dip-Buying + Harvester) ---")
    print(f"Total Invested:   ${smart_total_usd:.2f}")
    print(f"USDT Harvested:   +${smart_usdt_harvested:.2f} (Take-Profits: {harvest_count})")
    print(f"Dip-Buys Count:   {dip_buys_count} opportunistic discount buys")
    print(f"Coins Holdings:   {smart_coins:.6f} {symbol.replace('USDT','')}")
    print(f"Total Value:      ${smart_val:.2f} (Crypto + Harvested Reserve)")
    print(f"Return (PnL):     {smart_pnl_pct:+.2f}%")

    alpha = smart_pnl_pct - dumb_pnl_pct
    print(f"\n[RESULT] SMART ALPHA: {alpha:+.2f}% outperformance against standard DCA!")
    print("============================================================\n")

if __name__ == "__main__":
    asyncio.run(run_backtest("BTCUSDT", interval="60", limit=200))
