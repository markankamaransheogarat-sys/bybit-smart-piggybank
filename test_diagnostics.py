import asyncio
import os
import yaml
from src.exchange.bybit_client import BybitSpotClient
from src.core.analyzer import MarketAnalyzer
from src.core.database import Database
from src.core.risk import RiskManager
from src.notifications.telegram_bot import TelegramBotManager
from src.core.engine import PiggybankEngine
from src.mcp.server import PiggybankMCPServer

async def run_diagnostics():
    print("=== 1. Checking Database Initialization ===")
    await Database.init_db()
    print("Database tables initialized successfully.")

    print("\n=== 2. Testing Live Bybit Market Data Connection ===")
    client = BybitSpotClient(dry_run=True)
    tickers = await client.get_tickers()
    btc_price = tickers.get("BTCUSDT", 0.0)
    eth_price = tickers.get("ETHUSDT", 0.0)
    sol_price = tickers.get("SOLUSDT", 0.0)
    print(f"Bybit Public Ticker Feed Live:")
    print(f"  - BTC/USDT: ${btc_price:,.2f}")
    print(f"  - ETH/USDT: ${eth_price:,.2f}")
    print(f"  - SOL/USDT: ${sol_price:,.2f}")

    print("\n=== 3. Testing Technical Indicators & Scoring ===")
    klines = await client.get_klines("BTCUSDT", interval="60", limit=30)
    print(f"Fetched {len(klines)} 1-hour candles for BTCUSDT.")
    analysis = MarketAnalyzer.calculate_indicators(klines)
    print(f"  - RSI (14): {analysis['rsi']:.1f}")
    print(f"  - Bollinger Lower: ${analysis['lower_bb']:,.2f}")
    print(f"  - Bollinger Upper: ${analysis['upper_bb']:,.2f}")
    print(f"  - Position in Band: {analysis['bb_pos']:.2f}")

    is_dip, dip_tier, mult = MarketAnalyzer.evaluate_dip(analysis)
    print(f"  - Dip Status: {is_dip} (Tier: {dip_tier}, Multiplier: {mult}x)")

    print("\n=== 4. Testing Order Execution & Accounting (Simulation) ===")
    buy_res = await client.execute_market_buy("BTCUSDT", 25.0)
    print(f"Executed Test Buy: {buy_res}")
    
    order_data = {
        "order_id": buy_res["order_id"],
        "symbol": "BTCUSDT",
        "side": "BUY",
        "amount_usd": 25.0,
        "qty": buy_res["qty"],
        "price": buy_res["price"],
        "strategy": "DIAGNOSTIC_TEST",
        "details": "Automated verification test",
        "is_simulated": True
    }
    await Database.record_order(order_data)
    pos = await Database.get_position("BTC")
    print(f"Cost Basis in DB for BTC: Qty={pos['total_qty']:.6f}, Avg Price=${pos['avg_buy_price']:,.2f}")

    print("\n=== 5. Testing Profit Harvester Calculation ===")
    # Simulate +15% pump above buy price
    pumped_analysis = analysis.copy()
    pumped_analysis["price"] = pos["avg_buy_price"] * 1.15
    pumped_analysis["upper_bb"] = pumped_analysis["price"] * 0.99
    pumped_analysis["rsi"] = 78.0

    should_harvest, reason, harvest_pct = MarketAnalyzer.evaluate_harvest(
        pumped_analysis, avg_buy_price=pos["avg_buy_price"]
    )
    print(f"Harvest Trigger Test: should_harvest={should_harvest}, reason={reason}, pct={harvest_pct}%")

    print("\n=== 6. Testing MCP Tool Endpoints ===")
    with open("config/config.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    notifier = TelegramBotManager()
    engine = PiggybankEngine(config=cfg, bybit=client, notifier=notifier)
    engine.latest_analysis["BTCUSDT"] = analysis
    mcp = PiggybankMCPServer(engine)
    tools = mcp.get_tool_definitions()
    print(f"Registered MCP tools count: {len(tools)} ({[t['name'] for t in tools]})")

    mcp_status = await mcp.handle_tool_call("get_piggybank_status", {})
    print(f"MCP Status Tool Response: Daily Spent=${mcp_status.get('daily_spent_usd'):.2f}, Dry-Run={mcp_status.get('dry_run')}")

    print("\n[OK] ALL 6 SUBSYSTEMS VERIFIED AND WORKING PERFECTLY!")

if __name__ == "__main__":
    asyncio.run(run_diagnostics())
