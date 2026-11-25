# Autonomous AI Quantitative Trading Bot & Portfolio Dashboard (Moomoo Paper Trading)

An end-to-end, event-driven quantitative trading system and real-time dark-mode terminal locked strictly to **Moomoo Paper Trading (`TrdEnv.SIMULATE`)**.

---

## Project Structure

```text
TradeBot/
├── config/
│   ├── __init__.py
│   └── settings.py                # Pydantic settings & SIMULATE-only validator
├── backend/
│   ├── __init__.py
│   ├── moomoo_client.py           # OpenD connection, portfolio sync, TrdEnv.SIMULATE guardrail
│   ├── risk_manager.py            # 5% position cap, 20% sector cap, ATR SL, -2% circuit breaker
│   ├── ai_advisor.py              # Pydantic Buy/Hold/Sell synthesis & trade plan generator
│   ├── database.py                # DuckDB persistence for NAV, orders, social feed & backtests
│   └── app.py                     # FastAPI REST + WebSocket server
├── skills/
│   ├── __init__.py
│   └── social_research_skills.py  # Login-free Reddit, X/Threads, LinkedIn/IG, News & SEC scrapers
├── strategies/
│   ├── __init__.py
│   ├── indicators.py              # RSI, MACD, Bollinger Bands, ATR, Volume Profile
│   └── base.py                    # Vectorized strategy signal generators
├── backtester/
│   ├── __init__.py
│   └── backtest_engine.py         # Walk-forward backtester, Parquet cache & Auto-Gatekeeper
├── frontend/
│   ├── package.json
│   ├── vite.config.ts             # Proxies /api and /ws to FastAPI (127.0.0.1:8000)
│   └── src/App.tsx                # Finviz-style React + Tailwind + Recharts terminal
├── tests/
│   └── test_tradebot.py           # Unit & integration tests
├── .env.example
├── requirements.txt
└── README.md
```

---

## Step-by-Step Setup: Moomoo OpenD (Simulated Mode) & Bot Launch

### 1. Install Dependencies
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Optional: install Chromium for Playwright if using headless browser dorking
playwright install chromium
```

### 2. Configure Environment
```bash
cp .env.example .env
```
Edit `.env` to add your optional `OPENAI_API_KEY` or `GEMINI_API_KEY` (if omitted, the bot automatically uses its built-in deterministic `local_quant` synthesizer). Notice `MOOMOO_TRD_ENV=SIMULATE` is strictly enforced in code and config.

### 3. Start Moomoo OpenD in Paper Trading Mode
1. Download and install **Moomoo OpenD** (GUI or CLI) from the official Moomoo OpenAPI portal.
2. Launch OpenD and log in with your Moomoo account credentials.
3. Ensure the **API Listening Host/Port** is set to `127.0.0.1:11111` (matching `.env`).
4. In the Moomoo app / OpenD settings, ensure your **Paper Trading (Simulated)** US Stocks account is active.
   > **Safety Guardrail:** Every order, modification, cancellation, and account query in `backend/moomoo_client.py` hardcodes and asserts `trd_env=TrdEnv.SIMULATE`. If OpenD is offline, the bot automatically enters `LOCAL_SIM_FALLBACK` mode so you can test the UI and backtester safely.

### 4. Launch the FastAPI Backend & WebSocket Server
```bash
uvicorn backend.app:app --host 127.0.0.1 --port 8000 --reload
```

### 5. Launch the Finviz-Style React Quantitative Dashboard
In a second terminal:
```bash
cd frontend
npm install
npm run dev
```
Open `http://127.0.0.1:5173` in your browser.

### 6. Run Automated Tests
```bash
pytest -v
```
