import json
from typing import Dict, Any, List
from src.core.database import Database

class PiggybankMCPServer:
    """
    Model Context Protocol (MCP) tool provider for external AI Agents (Claude, Cursor, Antigravity, etc.).
    Exposes commands to monitor portfolio, evaluate signals, and execute or approve trades.
    """
    def __init__(self, engine):
        self.engine = engine

    def get_tool_definitions(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "get_piggybank_status",
                "description": "Returns current wallet balances, crypto portfolio valuation, PnL per asset, and 24h spending limit.",
                "parameters": {"type": "object", "properties": {}}
            },
            {
                "name": "get_market_analysis",
                "description": "Returns latest Bollinger Bands, RSI (14), and dip/harvest conditions for BTC, ETH, and SOL.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Optional symbol (e.g. BTCUSDT, ETHUSDT, SOLUSDT)"}
                    }
                }
            },
            {
                "name": "ai_operator_execute_buy",
                "description": "Allows the AI operator to execute a manual DCA or opportunistic buy of a crypto asset.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "e.g. BTCUSDT, ETHUSDT, SOLUSDT"},
                        "amount_usd": {"type": "number", "description": "Amount in USDT to invest (e.g. 15.0)"},
                        "reason": {"type": "string", "description": "Reasoning by the AI operator for taking this action"}
                    },
                    "required": ["symbol", "amount_usd", "reason"]
                }
            },
            {
                "name": "ai_operator_harvest_profit",
                "description": "Allows the AI operator to harvest a percentage of an asset back into USDT reserve.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "coin": {"type": "string", "description": "Asset name (BTC, ETH, SOL)"},
                        "harvest_pct": {"type": "number", "description": "Percentage to harvest (e.g. 15.0 for 15%)"},
                        "reason": {"type": "string", "description": "Reasoning by the AI operator"}
                    },
                    "required": ["coin", "harvest_pct", "reason"]
                }
            }
        ]

    async def handle_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if tool_name == "get_piggybank_status":
            wallet = await self.engine.bybit.get_wallet_balance()
            positions = await Database.get_all_positions()
            daily_spent = await Database.get_daily_spent_usd()
            return {
                "wallet": wallet,
                "positions": positions,
                "daily_spent_usd": daily_spent,
                "dry_run": self.engine.bybit.dry_run,
                "circuit_breaker_active": self.engine.risk_manager.circuit_breaker_active
            }

        elif tool_name == "get_market_analysis":
            symbol = arguments.get("symbol")
            if symbol:
                return {symbol: self.engine.latest_analysis.get(symbol, {})}
            return self.engine.latest_analysis

        elif tool_name == "ai_operator_execute_buy":
            sym = arguments["symbol"]
            amount = float(arguments["amount_usd"])
            reason = arguments.get("reason", "AI Operator trigger")

            wallet = await self.engine.bybit.get_wallet_balance()
            can_buy, risk_reason = await self.engine.risk_manager.can_execute_buy(amount, wallet.get("USDT", 0.0))
            if not can_buy:
                return {"success": False, "error": f"Risk Manager blocked trade: {risk_reason}"}

            res = await self.engine.bybit.execute_market_buy(sym, amount)
            if res.get("success"):
                order_data = {
                    "order_id": res["order_id"],
                    "symbol": sym,
                    "side": "BUY",
                    "amount_usd": amount,
                    "qty": res["qty"],
                    "price": res["price"],
                    "strategy": "AI_OPERATOR_BUY",
                    "details": reason,
                    "is_simulated": res["is_simulated"]
                }
                await Database.record_order(order_data)
                await self.engine.notifier.notify_buy(
                    symbol=sym,
                    strategy="AI OPERATOR ACTION",
                    amount_usd=amount,
                    qty=res["qty"],
                    price=res["price"],
                    details=f"AI Reasoning: {reason}",
                    is_sim=res["is_simulated"]
                )
                return {"success": True, "result": res}
            return {"success": False, "error": res.get("error")}

        elif tool_name == "ai_operator_harvest_profit":
            coin = arguments["coin"]
            pct = float(arguments["harvest_pct"])
            reason = arguments.get("reason", "AI Operator Harvest")
            sym = f"{coin}USDT"

            wallet = await self.engine.bybit.get_wallet_balance()
            curr_qty = wallet.get(coin, 0.0)
            sell_qty = curr_qty * (pct / 100.0)
            if sell_qty <= 0:
                return {"success": False, "error": f"No {coin} balance to harvest"}

            res = await self.engine.bybit.execute_market_sell(sym, sell_qty)
            if res.get("success"):
                order_data = {
                    "order_id": res["order_id"],
                    "symbol": sym,
                    "side": "SELL",
                    "amount_usd": res["amount_usd"],
                    "qty": res["qty"],
                    "price": res["price"],
                    "strategy": "AI_OPERATOR_HARVEST",
                    "details": reason,
                    "is_simulated": res["is_simulated"]
                }
                await Database.record_order(order_data)
                await self.engine.notifier.notify_harvest(
                    symbol=sym,
                    profit_pct=0.0,
                    revenue_usd=res["amount_usd"],
                    qty=res["qty"],
                    price=res["price"],
                    details=f"AI Reasoning: {reason}",
                    is_sim=res["is_simulated"]
                )
                return {"success": True, "result": res}
            return {"success": False, "error": res.get("error")}

        return {"error": f"Tool '{tool_name}' not recognized"}
