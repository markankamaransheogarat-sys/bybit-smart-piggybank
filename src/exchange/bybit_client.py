import hmac
import hashlib
import time
import httpx
from typing import Dict, Any, List, Optional
import logging

logger = logging.getLogger(__name__)

class BybitSpotClient:
    """
    Bybit v5 REST API Client specialized for Spot Trading and Market Data.
    Supports both real execution and local dry-run simulation.
    """
    def __init__(self, api_key: str = "", api_secret: str = "", testnet: bool = False, dry_run: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.testnet = testnet
        self.dry_run = dry_run
        self.base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"

        # Virtual balances for simulation (Dry-run mode)
        self.simulated_balances = {
            "USDT": 1000.0,
            "BTC": 0.0,
            "ETH": 0.0,
            "SOL": 0.0
        }

    def _generate_signature(self, timestamp: str, recv_window: str, payload: str) -> str:
        param_str = timestamp + self.api_key + recv_window + payload
        return hmac.new(
            self.api_secret.encode("utf-8"),
            param_str.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

    async def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        timestamp = str(int(time.time() * 1000))
        recv_window = "5000"

        headers = {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "Content-Type": "application/json"
        }

        payload = ""
        if method.upper() == "GET" and params:
            query_str = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
            payload = query_str
        elif data:
            import json
            payload = json.dumps(data)

        if self.api_key and self.api_secret:
            signature = self._generate_signature(timestamp, recv_window, payload)
            headers["X-BAPI-SIGN"] = signature

        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                if method.upper() == "GET":
                    resp = await client.get(url, params=params, headers=headers)
                else:
                    resp = await client.post(url, json=data, headers=headers)
                
                resp.raise_for_status()
                res_json = resp.json()
                if res_json.get("retCode") != 0:
                    logger.error(f"Bybit API error: {res_json.get('retMsg')} (code {res_json.get('retCode')})")
                return res_json
            except Exception as e:
                logger.error(f"HTTP request failed for {endpoint}: {e}")
                return {"retCode": -1, "retMsg": str(e), "result": {}}

    async def get_klines(self, symbol: str, interval: str = "60", limit: int = 50) -> List[Dict[str, Any]]:
        """
        Fetch market candles (klines).
        interval mapping: '15' for 15m, '60' for 1h, '240' for 4h, 'D' for 1d.
        Bybit v5 /v5/market/kline
        """
        params = {
            "category": "spot",
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        }
        res = await self._request("GET", "/v5/market/kline", params=params)
        raw_list = res.get("result", {}).get("list", [])
        
        # Bybit returns: [startTime, openPrice, highPrice, lowPrice, closePrice, volume, turnover]
        # in reverse chronological order (newest first). Let's sort oldest first.
        klines = []
        for item in reversed(raw_list):
            klines.append({
                "timestamp": int(item[0]),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5])
            })
        return klines

    async def get_tickers(self, symbol: Optional[str] = None) -> Dict[str, float]:
        """Fetch current market prices for spot symbols."""
        params = {"category": "spot"}
        if symbol:
            params["symbol"] = symbol
        res = await self._request("GET", "/v5/market/tickers", params=params)
        tickers = {}
        for item in res.get("result", {}).get("list", []):
            sym = item.get("symbol")
            price = float(item.get("lastPrice", 0.0))
            tickers[sym] = price
        return tickers

    async def get_wallet_balance(self) -> Dict[str, float]:
        """Fetch Spot / Unified wallet balances."""
        if self.dry_run or not self.api_key:
            return self.simulated_balances.copy()

        params = {"accountType": "UNIFIED"}
        res = await self._request("GET", "/v5/account/wallet-balance", params=params)
        
        # If UNIFIED fails, fallback to SPOT
        if res.get("retCode") != 0:
            params = {"accountType": "SPOT"}
            res = await self._request("GET", "/v5/account/wallet-balance", params=params)

        balances = {}
        accounts = res.get("result", {}).get("list", [])
        if accounts:
            coins = accounts[0].get("coin", [])
            for c in coins:
                coin_name = c.get("coin")
                wallet_bal = float(c.get("walletBalance", 0.0))
                balances[coin_name] = wallet_bal
        return balances

    async def execute_market_buy(self, symbol: str, quote_amount_usd: float) -> Dict[str, Any]:
        """
        Execute market buy order by spending quote_amount_usd (e.g. 15 USDT).
        """
        coin = symbol.replace("USDT", "")
        if self.dry_run or not self.api_key:
            tickers = await self.get_tickers(symbol)
            price = tickers.get(symbol, 0.0)
            if price <= 0:
                return {"success": False, "error": f"Failed to get price for {symbol}"}
            
            qty = quote_amount_usd / price
            current_usdt = self.simulated_balances.get("USDT", 0.0)
            if current_usdt < quote_amount_usd:
                return {"success": False, "error": "Insufficient simulated USDT"}
            
            self.simulated_balances["USDT"] -= quote_amount_usd
            self.simulated_balances[coin] = self.simulated_balances.get(coin, 0.0) + qty
            
            return {
                "success": True,
                "order_id": f"sim_buy_{int(time.time()*1000)}",
                "symbol": symbol,
                "side": "BUY",
                "amount_usd": quote_amount_usd,
                "qty": qty,
                "price": price,
                "is_simulated": True
            }

        # Real Bybit Spot Market Buy
        data = {
            "category": "spot",
            "symbol": symbol,
            "side": "Buy",
            "orderType": "Market",
            "marketUnit": "quoteCoin",  # Buy by spending USDT quote amount
            "qty": f"{quote_amount_usd:.2f}"
        }
        res = await self._request("POST", "/v5/order/create", data=data)
        if res.get("retCode") == 0:
            order_id = res.get("result", {}).get("orderId")
            tickers = await self.get_tickers(symbol)
            price = tickers.get(symbol, 0.0)
            qty = quote_amount_usd / price if price > 0 else 0.0
            return {
                "success": True,
                "order_id": order_id,
                "symbol": symbol,
                "side": "BUY",
                "amount_usd": quote_amount_usd,
                "qty": qty,
                "price": price,
                "is_simulated": False
            }
        else:
            return {"success": False, "error": res.get("retMsg", "Unknown error")}

    async def execute_market_sell(self, symbol: str, base_qty: float) -> Dict[str, Any]:
        """
        Execute market sell order for base_qty (e.g. 0.005 BTC).
        """
        coin = symbol.replace("USDT", "")
        if self.dry_run or not self.api_key:
            tickers = await self.get_tickers(symbol)
            price = tickers.get(symbol, 0.0)
            if price <= 0:
                return {"success": False, "error": f"Failed to get price for {symbol}"}

            current_coin = self.simulated_balances.get(coin, 0.0)
            if current_coin < base_qty:
                return {"success": False, "error": f"Insufficient simulated {coin}"}

            revenue_usd = base_qty * price
            self.simulated_balances[coin] -= base_qty
            self.simulated_balances["USDT"] = self.simulated_balances.get("USDT", 0.0) + revenue_usd

            return {
                "success": True,
                "order_id": f"sim_sell_{int(time.time()*1000)}",
                "symbol": symbol,
                "side": "SELL",
                "amount_usd": revenue_usd,
                "qty": base_qty,
                "price": price,
                "is_simulated": True
            }

        # Real Bybit Spot Market Sell
        data = {
            "category": "spot",
            "symbol": symbol,
            "side": "Sell",
            "orderType": "Market",
            "qty": f"{base_qty:.6f}".rstrip("0").rstrip(".")
        }
        res = await self._request("POST", "/v5/order/create", data=data)
        if res.get("retCode") == 0:
            order_id = res.get("result", {}).get("orderId")
            tickers = await self.get_tickers(symbol)
            price = tickers.get(symbol, 0.0)
            revenue_usd = base_qty * price
            return {
                "success": True,
                "order_id": order_id,
                "symbol": symbol,
                "side": "SELL",
                "amount_usd": revenue_usd,
                "qty": base_qty,
                "price": price,
                "is_simulated": False
            }
        else:
            return {"success": False, "error": res.get("retMsg", "Unknown error")}
