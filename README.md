# Forever Forex Signals — Twelve Data backend

This version uses a **Node.js server** to call Twelve Data and calculates an EMA 9/21 crossover. The API key is read only by the server from `.env`; it is never sent to the browser.

## Requirements
- Node.js 20 or newer: https://nodejs.org/en/download
- A Twelve Data account and API key: https://twelvedata.com/

## Setup on Windows
1. Download and unzip this folder.
2. Open the folder `forever-forex-signals-live` in File Explorer.
3. In that folder, copy `.env.example` and rename the copy to `.env` (make sure it is not named `.env.txt`).
4. Open `.env` in Notepad. Replace `put_your_key_here` with your own Twelve Data API key. Do not add quotes or spaces around it. Save.
5. Open a terminal in this folder: click the File Explorer address bar, type `cmd`, and press Enter.
6. Run `npm install` and wait for it to finish.
7. Run `npm start`.
8. Open http://localhost:3000 in your browser. Click **Fetch live data**.

## macOS / Linux
Open Terminal in this folder and run `cp .env.example .env`, edit `.env` in a text editor, then run `npm install` and `npm start`. Keep the key only in `.env`.

## Verify the setup
- Visit `http://localhost:3000/api/health`. You should see `"apiKeyConfigured": true` if the server found your key.
- Visit `http://localhost:3000/api/signals?symbol=EUR%2FUSD&interval=15min` to test a signal endpoint.
- Do not publish your `.env` file. `.gitignore` excludes it from Git.

## Security notes
- Do not paste your API key into chat, `index.html`, `app.js`, a screenshot, or a public GitHub repository.
- Keep `.env` private. If the key was ever committed to a public repository, revoke/rotate it in Twelve Data.
- This starter limits allowed pairs and timeframes, rate-limits API calls, caches data for 60 seconds, sets common security headers, and uses the Authorization header to send the key to Twelve Data.
- For a public deployment, add HTTPS, stricter per-user rate limits, monitoring, secret-manager configuration, and verify the market-data provider's redistribution terms.

## Strategy limitations
- Signal rule (experimental): BUY requires a fresh EMA 9/21 bullish crossover, close above EMA 50, and RSI(14) between 50 and 68. SELL requires a fresh bearish crossover, close below EMA 50, and RSI(14) between 32 and 50. Otherwise the app returns WAIT. This is a heuristic filter, not a validated profitable strategy.
- The endpoint uses 100 candles and calculates EMA 9/21, EMA 50, RSI(14), and ATR(14) server-side. It reports the latest returned candle time; that is not a promise that data is real-time.
- The strategy is educational, not a recommendation or guarantee. It does not calculate broker-specific spread/slippage, place trades, or account for economic news. Backtest and paper-trade before any live trading.
- The UI scans one pair at a time to help control API-credit usage. Twelve Data symbol availability and rate limits depend on your plan. Review https://twelvedata.com/docs.

## Deploying later
Deploy the Node app to a trusted Node.js host, configure `TWELVE_DATA_API_KEY` as a server-side environment secret in the host dashboard, and never upload `.env`. Use HTTPS. Do not publish this as a paid signal service until data licensing, strategy testing, disclosures, user access controls, and payment handling are implemented.

## Stop-loss and take-profit estimates
The dashboard calculates ATR(14) from returned OHLC candles. For a fresh BUY or SELL crossover, it estimates the stop at 1.5 × ATR from the latest close and places the take-profit at the selected reward-to-risk ratio (1:1, 1:1.5, 1:2, or 1:3). For WAIT, it deliberately does not suggest entry/exit levels. These are educational estimates only, not broker orders or guaranteed execution prices. They do not account for spread, slippage, gaps, commission, or account-specific risk sizing. Test with historical data and paper trading first.


## Optional MT5 demo BUY/SELL buttons (Windows)

**Important:** This bridge is for local testing only. Do not expose port 8765 to your network or internet. It refuses accounts MT5 identifies as non-demo and refuses non-USD accounts. Never put broker credentials in this website or share them in chat.

1. Install/open MetaTrader 5 and log into your broker's **demo** account. Confirm EURUSD appears in Market Watch and that Algo Trading / trading permissions are enabled only as needed for your demo test.
2. In this project folder, open Command Prompt and install the Python package: `py -m pip install MetaTrader5`.
3. Keep the MT5 desktop terminal open. In a second Command Prompt opened in this project folder, run: `py mt5_demo_bridge.py`. Leave this window open.
4. In another Command Prompt, run the website as usual: `npm install` (first time only), then `npm start`.
5. Visit `http://localhost:3000`, click **Fetch live data**, and wait for a valid BUY or SELL setup. The buttons remain disabled for WAIT or if the MT5 bridge is disconnected.
6. Click BUY or SELL, carefully review the confirmation window, and click **Confirm demo order** only if the pair, price, lot size, stop-loss, take-profit and estimated loss are acceptable. Check MT5's Trade tab to verify the result.

### Included safety checks
- Node website and Python bridge both listen on `127.0.0.1` only; the browser calls the bridge through the Node backend.
- MT5 must report a DEMO account, USD currency, connected terminal, and trading permissions enabled.
- Each order requires a one-use preview token and an explicit confirmation within 60 seconds.
- Uses current broker quote, broker stop-distance checks, `order_check`, estimated loss sizing at 0.5% of balance, a hard 0.10-lot cap, max two open positions, and a 2% daily realized-loss lockout.
- Blocks if quote/history/margin/risk checks are unavailable or fail. Broker conditions may still reject orders, and estimated loss can differ due to slippage, gaps, spread, commissions or execution.

This is a starter integration, not audited trading software. Test only on demo and inspect every order. Do not use a live account; do not deploy this bridge publicly. The 2% daily check uses today's realized deal history and does not fully capture floating losses, so it is not a complete account-level risk manager. No strategy is guaranteed profitable.
