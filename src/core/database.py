import aiosqlite
import time
from typing import List, Dict, Any, Optional

DB_PATH = "piggybank.db"

class Database:
    @staticmethod
    async def init_db():
        async with aiosqlite.connect(DB_PATH) as db:
            # Table for trades (buys, sells, harvests)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT UNIQUE,
                    symbol TEXT,
                    side TEXT,
                    amount_usd REAL,
                    qty REAL,
                    price REAL,
                    strategy TEXT,
                    details TEXT,
                    is_simulated INTEGER,
                    created_at INTEGER
                )
            """)

            # Table for tracking asset average cost basis (cost basis accounting)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS portfolio_positions (
                    coin TEXT PRIMARY KEY,
                    total_qty REAL,
                    total_invested_usd REAL,
                    avg_buy_price REAL,
                    last_updated INTEGER
                )
            """)

            # Table for logs and state
            await db.execute("""
                CREATE TABLE IF NOT EXISTS system_state (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at INTEGER
                )
            """)
            await db.commit()

    @staticmethod
    async def record_order(order_data: Dict[str, Any]):
        async with aiosqlite.connect(DB_PATH) as db:
            now = int(time.time())
            await db.execute("""
                INSERT OR REPLACE INTO orders 
                (order_id, symbol, side, amount_usd, qty, price, strategy, details, is_simulated, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                order_data.get("order_id"),
                order_data.get("symbol"),
                order_data.get("side"),
                order_data.get("amount_usd"),
                order_data.get("qty"),
                order_data.get("price"),
                order_data.get("strategy", "unknown"),
                order_data.get("details", ""),
                1 if order_data.get("is_simulated") else 0,
                now
            ))

            # Update cost basis for this coin
            symbol = order_data.get("symbol", "")
            coin = symbol.replace("USDT", "")
            side = order_data.get("side", "").upper()
            qty = order_data.get("qty", 0.0)
            amount_usd = order_data.get("amount_usd", 0.0)

            # Get current position
            cursor = await db.execute("SELECT total_qty, total_invested_usd FROM portfolio_positions WHERE coin = ?", (coin,))
            row = await cursor.fetchone()
            curr_qty = row[0] if row else 0.0
            curr_invested = row[1] if row else 0.0

            if side == "BUY":
                new_qty = curr_qty + qty
                new_invested = curr_invested + amount_usd
                new_avg = new_invested / new_qty if new_qty > 0 else 0.0
            else: # SELL / HARVEST
                new_qty = max(0.0, curr_qty - qty)
                # Keep same avg price, decrease invested proportionally
                avg_price = curr_invested / curr_qty if curr_qty > 0 else 0.0
                new_invested = new_qty * avg_price
                new_avg = avg_price

            await db.execute("""
                INSERT OR REPLACE INTO portfolio_positions (coin, total_qty, total_invested_usd, avg_buy_price, last_updated)
                VALUES (?, ?, ?, ?, ?)
            """, (coin, new_qty, new_invested, new_avg, now))

            await db.commit()

    @staticmethod
    async def get_position(coin: str) -> Dict[str, Any]:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT total_qty, total_invested_usd, avg_buy_price FROM portfolio_positions WHERE coin = ?", (coin,))
            row = await cursor.fetchone()
            if row:
                return {
                    "coin": coin,
                    "total_qty": row[0],
                    "total_invested_usd": row[1],
                    "avg_buy_price": row[2]
                }
            return {"coin": coin, "total_qty": 0.0, "total_invested_usd": 0.0, "avg_buy_price": 0.0}

    @staticmethod
    async def get_all_positions() -> Dict[str, Dict[str, Any]]:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT coin, total_qty, total_invested_usd, avg_buy_price FROM portfolio_positions")
            rows = await cursor.fetchall()
            return {
                r[0]: {
                    "coin": r[0],
                    "total_qty": r[1],
                    "total_invested_usd": r[2],
                    "avg_buy_price": r[3]
                } for r in rows
            }

    @staticmethod
    async def get_recent_orders(limit: int = 50) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT order_id, symbol, side, amount_usd, qty, price, strategy, details, is_simulated, created_at
                FROM orders ORDER BY created_at DESC LIMIT ?
            """, (limit,))
            rows = await cursor.fetchall()
            return [{
                "order_id": r[0],
                "symbol": r[1],
                "side": r[2],
                "amount_usd": r[3],
                "qty": r[4],
                "price": r[5],
                "strategy": r[6],
                "details": r[7],
                "is_simulated": bool(r[8]),
                "created_at": r[9]
            } for r in rows]

    @staticmethod
    async def get_daily_spent_usd() -> float:
        """Calculate total USD spent on buys in the last 24 hours."""
        since = int(time.time() - 86400)
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("""
                SELECT SUM(amount_usd) FROM orders 
                WHERE side = 'BUY' AND created_at >= ?
            """, (since,))
            row = await cursor.fetchone()
            return row[0] if (row and row[0] is not None) else 0.0
