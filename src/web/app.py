from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from typing import Dict, Any
import logging
from src.core.database import Database

logger = logging.getLogger(__name__)

def create_app(engine) -> FastAPI:
    app = FastAPI(title="Bybit Smart Piggybank Dashboard")

    @app.get("/api/status")
    async def get_status():
        wallet = await engine.bybit.get_wallet_balance()
        positions = await Database.get_all_positions()
        orders = await Database.get_recent_orders(limit=30)
        daily_spent = await Database.get_daily_spent_usd()

        # Calculate current valuation
        tickers = await engine.bybit.get_tickers()
        total_portfolio_usd = wallet.get("USDT", 0.0)
        coin_breakdown = []

        for coin, qty in wallet.items():
            if coin == "USDT":
                continue
            price = tickers.get(f"{coin}USDT", 0.0)
            val_usd = qty * price
            total_portfolio_usd += val_usd
            pos = positions.get(coin, {})
            avg_price = pos.get("avg_buy_price", 0.0)
            profit_pct = ((price - avg_price) / avg_price * 100.0) if avg_price > 0 else 0.0

            coin_breakdown.append({
                "coin": coin,
                "qty": qty,
                "price": price,
                "value_usd": val_usd,
                "avg_price": avg_price,
                "profit_pct": profit_pct
            })

        return {
            "is_running": engine.is_running,
            "dry_run": engine.bybit.dry_run,
            "total_portfolio_usd": total_portfolio_usd,
            "usdt_reserve": wallet.get("USDT", 0.0),
            "daily_spent_usd": daily_spent,
            "max_daily_spend": engine.risk_manager.max_daily_spend,
            "circuit_breaker_active": engine.risk_manager.circuit_breaker_active,
            "coins": coin_breakdown,
            "market_analysis": engine.latest_analysis,
            "recent_orders": orders
        }

    @app.post("/api/actions/manual-buy")
    async def manual_buy(data: Dict[str, Any]):
        symbol = data.get("symbol")
        amount_usd = float(data.get("amount_usd", 10.0))
        if not symbol:
            raise HTTPException(status_code=400, detail="Symbol is required")

        wallet = await engine.bybit.get_wallet_balance()
        can_buy, reason = await engine.risk_manager.can_execute_buy(amount_usd, wallet.get("USDT", 0.0))
        if not can_buy:
            raise HTTPException(status_code=400, detail=reason)

        res = await engine.bybit.execute_market_buy(symbol, amount_usd)
        if not res.get("success"):
            raise HTTPException(status_code=500, detail=res.get("error"))

        order_data = {
            "order_id": res["order_id"],
            "symbol": symbol,
            "side": "BUY",
            "amount_usd": amount_usd,
            "qty": res["qty"],
            "price": res["price"],
            "strategy": "MANUAL_OPERATOR",
            "details": "Triggered via Web Dashboard or AI Operator",
            "is_simulated": res["is_simulated"]
        }
        await Database.record_order(order_data)
        await engine.notifier.notify_buy(
            symbol=symbol,
            strategy="MANUAL / AI OPERATOR",
            amount_usd=amount_usd,
            qty=res["qty"],
            price=res["price"],
            details="Manual trigger",
            is_sim=res["is_simulated"]
        )
        return {"status": "success", "order": res}

    @app.post("/api/actions/manual-harvest")
    async def manual_harvest(data: Dict[str, Any]):
        coin = data.get("coin")
        pct = float(data.get("pct", 15.0))
        symbol = f"{coin}USDT"

        wallet = await engine.bybit.get_wallet_balance()
        curr_qty = wallet.get(coin, 0.0)
        sell_qty = curr_qty * (pct / 100.0)

        if sell_qty <= 0:
            raise HTTPException(status_code=400, detail="Insufficient coin balance to harvest")

        res = await engine.bybit.execute_market_sell(symbol, sell_qty)
        if not res.get("success"):
            raise HTTPException(status_code=500, detail=res.get("error"))

        order_data = {
            "order_id": res["order_id"],
            "symbol": symbol,
            "side": "SELL",
            "amount_usd": res["amount_usd"],
            "qty": res["qty"],
            "price": res["price"],
            "strategy": "MANUAL_HARVEST",
            "details": f"Manual harvest of {pct}%",
            "is_simulated": res["is_simulated"]
        }
        await Database.record_order(order_data)
        return {"status": "success", "order": res}

    @app.get("/", response_class=HTMLResponse)
    async def get_dashboard():
        html = """
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>Bybit Smart Piggybank Dashboard</title>
            <script src="https://cdn.tailwindcss.com"></script>
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
        </head>
        <body class="bg-slate-950 text-slate-100 min-h-screen flex flex-col font-sans">
            <!-- Header -->
            <header class="border-b border-slate-800 bg-slate-900/60 backdrop-blur sticky top-0 z-50 px-6 py-4 flex items-center justify-between">
                <div class="flex items-center space-x-3">
                    <div class="p-2 bg-amber-500/10 text-amber-400 rounded-xl border border-amber-500/20 text-xl font-bold">
                        <i class="fa-solid fa-piggy-bank"></i>
                    </div>
                    <div>
                        <h1 class="text-lg font-bold tracking-tight">Bybit Smart Piggybank</h1>
                        <p class="text-xs text-slate-400">Autonomous DCA & Dip Accumulator</p>
                    </div>
                </div>
                <div class="flex items-center space-x-4">
                    <span id="mode-badge" class="px-2.5 py-1 text-xs font-semibold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                        Loading...
                    </span>
                    <button onclick="fetchStatus()" class="p-2 text-slate-400 hover:text-white rounded-lg hover:bg-slate-800 transition">
                        <i class="fa-solid fa-rotate"></i>
                    </button>
                </div>
            </header>

            <!-- Main Container -->
            <main class="flex-1 max-w-7xl w-full mx-auto p-6 space-y-6">
                <!-- Top Metrics Row -->
                <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
                    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                        <span class="text-xs text-slate-400 font-medium uppercase tracking-wider">Общий баланс портфеля</span>
                        <div class="text-2xl font-bold mt-1 text-white" id="total-val">$0.00</div>
                        <span class="text-xs text-slate-500 mt-1 block">Крипта + USDT резерв</span>
                    </div>
                    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                        <span class="text-xs text-slate-400 font-medium uppercase tracking-wider">Резерв USDT</span>
                        <div class="text-2xl font-bold mt-1 text-emerald-400" id="usdt-val">$0.00</div>
                        <span class="text-xs text-slate-500 mt-1 block">Готово под выкуп просадок</span>
                    </div>
                    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                        <span class="text-xs text-slate-400 font-medium uppercase tracking-wider">Потрачено за 24ч</span>
                        <div class="text-2xl font-bold mt-1 text-cyan-400" id="daily-spent">$0.00</div>
                        <span class="text-xs text-slate-500 mt-1 block" id="daily-cap">Лимит: $150.00</span>
                    </div>
                    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                        <span class="text-xs text-slate-400 font-medium uppercase tracking-wider">Circuit Breaker</span>
                        <div class="text-2xl font-bold mt-1" id="cb-status">OK</div>
                        <span class="text-xs text-slate-500 mt-1 block">Защита от ножей рынка</span>
                    </div>
                </div>

                <!-- Portfolio Assets & Indicators -->
                <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                    <div class="lg:col-span-2 bg-slate-900 border border-slate-800 rounded-2xl p-5">
                        <h2 class="text-base font-semibold mb-4 flex items-center justify-between">
                            <span>Накопленные активы (Классический портфель)</span>
                            <span class="text-xs text-slate-400">BTC, ETH, SOL</span>
                        </h2>
                        <div class="overflow-x-auto">
                            <table class="w-full text-left text-sm">
                                <thead>
                                    <tr class="text-slate-500 border-b border-slate-800 text-xs uppercase">
                                        <th class="pb-3">Актив</th>
                                        <th class="pb-3">Количество</th>
                                        <th class="pb-3">Текущая цена</th>
                                        <th class="pb-3">Ср. цена входа</th>
                                        <th class="pb-3">PnL</th>
                                        <th class="pb-3">Действия</th>
                                    </tr>
                                </thead>
                                <tbody id="assets-tbody" class="divide-y divide-slate-800/60">
                                    <!-- Dynamic rows -->
                                </tbody>
                            </table>
                        </div>
                    </div>

                    <!-- Market Radar (AI / Indicators) -->
                    <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                        <h2 class="text-base font-semibold mb-4">Индикаторы & Сигналы (1h)</h2>
                        <div id="radar-container" class="space-y-4">
                            <!-- Dynamic indicator cards -->
                        </div>
                    </div>
                </div>

                <!-- Recent Orders History -->
                <div class="bg-slate-900 border border-slate-800 rounded-2xl p-5">
                    <h2 class="text-base font-semibold mb-4">История операций копилки</h2>
                    <div class="overflow-x-auto">
                        <table class="w-full text-left text-sm">
                            <thead>
                                <tr class="text-slate-500 border-b border-slate-800 text-xs uppercase">
                                    <th class="pb-3">Время</th>
                                    <th class="pb-3">Тип</th>
                                    <th class="pb-3">Актив</th>
                                    <th class="pb-3">Объем ($)</th>
                                    <th class="pb-3">Цена</th>
                                    <th class="pb-3">Стратегия</th>
                                    <th class="pb-3">Детали</th>
                                </tr>
                            </thead>
                            <tbody id="orders-tbody" class="divide-y divide-slate-800/60">
                                <!-- Dynamic rows -->
                            </tbody>
                        </table>
                    </div>
                </div>
            </main>

            <script>
                async function fetchStatus() {
                    try {
                        const res = await fetch('/api/status');
                        const data = await res.json();

                        // Badges
                        const modeBadge = document.getElementById('mode-badge');
                        if (data.dry_run) {
                            modeBadge.textContent = '🧪 DRY-RUN (SIMULATION)';
                            modeBadge.className = 'px-2.5 py-1 text-xs font-semibold rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20';
                        } else {
                            modeBadge.textContent = '🟢 LIVE TRADING';
                            modeBadge.className = 'px-2.5 py-1 text-xs font-semibold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20';
                        }

                        // Top metrics
                        document.getElementById('total-val').textContent = `$${data.total_portfolio_usd.toFixed(2)}`;
                        document.getElementById('usdt-val').textContent = `$${data.usdt_reserve.toFixed(2)}`;
                        document.getElementById('daily-spent').textContent = `$${data.daily_spent_usd.toFixed(2)}`;
                        document.getElementById('daily-cap').textContent = `Лимит: $${data.max_daily_spend.toFixed(2)}`;

                        const cb = document.getElementById('cb-status');
                        if (data.circuit_breaker_active) {
                            cb.textContent = 'PAUSED';
                            cb.className = 'text-2xl font-bold mt-1 text-red-500';
                        } else {
                            cb.textContent = 'HEALTHY';
                            cb.className = 'text-2xl font-bold mt-1 text-emerald-400';
                        }

                        // Assets Table
                        const tbody = document.getElementById('assets-tbody');
                        tbody.innerHTML = '';
                        data.coins.forEach(c => {
                            const pnlClass = c.profit_pct >= 0 ? 'text-emerald-400' : 'text-rose-400';
                            const pnlSign = c.profit_pct >= 0 ? '+' : '';
                            tbody.innerHTML += `
                                <tr class="hover:bg-slate-800/30">
                                    <td class="py-3 font-semibold text-white">${c.coin}</td>
                                    <td class="py-3 text-slate-300">${c.qty.toFixed(6)}</td>
                                    <td class="py-3 text-slate-300">$${c.price.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
                                    <td class="py-3 text-slate-400">$${c.avg_price.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
                                    <td class="py-3 font-semibold ${pnlClass}">${pnlSign}${c.profit_pct.toFixed(1)}%</td>
                                    <td class="py-3 space-x-2">
                                        <button onclick="manualBuy('${c.coin}USDT', 10)" class="px-2 py-1 text-xs bg-indigo-600 hover:bg-indigo-500 text-white rounded font-medium">DCA $10</button>
                                        <button onclick="manualHarvest('${c.coin}', 15)" class="px-2 py-1 text-xs bg-amber-600 hover:bg-amber-500 text-white rounded font-medium">Фикс 15%</button>
                                    </td>
                                </tr>
                            `;
                        });

                        // Radar / Indicators
                        const radar = document.getElementById('radar-container');
                        radar.innerHTML = '';
                        for (const [sym, ind] of Object.entries(data.market_analysis)) {
                            const rsiColor = ind.rsi < 35 ? 'text-emerald-400 font-bold' : (ind.rsi > 70 ? 'text-rose-400 font-bold' : 'text-slate-300');
                            radar.innerHTML += `
                                <div class="p-3 bg-slate-800/40 border border-slate-800 rounded-xl">
                                    <div class="flex justify-between items-center mb-1">
                                        <span class="font-bold text-white text-sm">${sym}</span>
                                        <span class="text-xs ${rsiColor}">RSI: ${ind.rsi.toFixed(1)}</span>
                                    </div>
                                    <div class="text-xs text-slate-400 flex justify-between">
                                        <span>Нижний BB: $${ind.lower_bb.toFixed(2)}</span>
                                        <span>Верхний BB: $${ind.upper_bb.toFixed(2)}</span>
                                    </div>
                                </div>
                            `;
                        }

                        // Orders Table
                        const ordersTbody = document.getElementById('orders-tbody');
                        ordersTbody.innerHTML = '';
                        data.recent_orders.slice(0, 15).forEach(o => {
                            const isBuy = o.side === 'BUY';
                            const sideClass = isBuy ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-amber-500/10 text-amber-400 border border-amber-500/20';
                            const dateStr = new Date(o.created_at * 1000).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit', second:'2-digit'});
                            ordersTbody.innerHTML += `
                                <tr class="hover:bg-slate-800/30 text-xs">
                                    <td class="py-2.5 text-slate-400">${dateStr}</td>
                                    <td class="py-2.5"><span class="px-2 py-0.5 rounded ${sideClass} font-semibold">${o.side}</span></td>
                                    <td class="py-2.5 text-white font-medium">${o.symbol}</td>
                                    <td class="py-2.5 text-slate-200">$${o.amount_usd.toFixed(2)}</td>
                                    <td class="py-2.5 text-slate-400">$${o.price.toLocaleString(undefined, {minimumFractionDigits: 2})}</td>
                                    <td class="py-2.5 text-indigo-400 font-medium">${o.strategy}</td>
                                    <td class="py-2.5 text-slate-400 truncate max-w-xs">${o.details}</td>
                                </tr>
                            `;
                        });

                    } catch (e) {
                        console.error('Failed to fetch status:', e);
                    }
                }

                async function manualBuy(symbol, amountUsd) {
                    if (!confirm(`Купить ${symbol} на $${amountUsd}?`)) return;
                    await fetch('/api/actions/manual-buy', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({symbol, amount_usd: amountUsd})
                    });
                    fetchStatus();
                }

                async function manualHarvest(coin, pct) {
                    if (!confirm(`Зафиксировать ${pct}% прибыли ${coin} в USDT резерв?`)) return;
                    await fetch('/api/actions/manual-harvest', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({coin, pct})
                    });
                    fetchStatus();
                }

                fetchStatus();
                setInterval(fetchStatus, 5000);
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

    return app
