import 'dotenv/config';
import express from 'express';
import helmet from 'helmet';
import rateLimit from 'express-rate-limit';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const app = express();
const PORT = Number(process.env.PORT || 3000);
const API_KEY = process.env.TWELVE_DATA_API_KEY;
const CACHE_SECONDS = Math.max(15, Number(process.env.CACHE_SECONDS || 60));
const cache = new Map();
const allowedPairs = new Set([
  'EUR/USD', 'GBP/USD', 'USD/JPY', 'USD/CHF', 'AUD/USD', 'USD/CAD', 'NZD/USD',
  'EUR/GBP', 'EUR/JPY', 'GBP/JPY', 'AUD/JPY', 'EUR/AUD', 'GBP/CHF'
]);
const allowedIntervals = new Set(['5min', '15min', '30min', '1h', '4h', '1day']);

app.disable('x-powered-by');
app.use(helmet({ contentSecurityPolicy: { directives: {
  defaultSrc: ["'self'"], scriptSrc: ["'self'"], styleSrc: ["'self'"],
  imgSrc: ["'self'", 'data:'], connectSrc: ["'self'"], objectSrc: ["'none'"],
  upgradeInsecureRequests: null
}}}));
app.use(express.json({ limit: '10kb' }));
app.use('/api/', rateLimit({ windowMs: 60_000, limit: 60, standardHeaders: 'draft-7', legacyHeaders: false }));
app.use(express.static(path.join(__dirname, 'public'), { extensions: ['html'] }));

function ema(values, period) {
  if (values.length < period) return [];
  const alpha = 2 / (period + 1);
  // Seed with the first close, then smooth through the full series.
  let current = values[0];
  const result = [current];
  for (let i = 1; i < values.length; i++) {
    current = values[i] * alpha + current * (1 - alpha);
    result.push(current);
  }
  return result;
}

function rsi(values, period = 14) {
  if (values.length <= period) return null;
  let gains = 0, losses = 0;
  for (let i = 1; i <= period; i++) {
    const change = values[i] - values[i - 1];
    if (change > 0) gains += change; else losses -= change;
  }
  let avgGain = gains / period, avgLoss = losses / period;
  for (let i = period + 1; i < values.length; i++) {
    const change = values[i] - values[i - 1];
    avgGain = (avgGain * (period - 1) + Math.max(change, 0)) / period;
    avgLoss = (avgLoss * (period - 1) + Math.max(-change, 0)) / period;
  }
  if (avgLoss === 0) return 100;
  return 100 - (100 / (1 + avgGain / avgLoss));
}

function makeSignal(symbol, interval, rows, riskReward = 2) {
  // Twelve Data returns newest candles first; indicators need chronological candles.
  const candles = rows.map(r => ({
    time: r.datetime, open: Number(r.open), high: Number(r.high),
    low: Number(r.low), close: Number(r.close)
  })).filter(r => [r.open, r.high, r.low, r.close].every(v => Number.isFinite(v) && v > 0) && r.high >= r.low)
    .reverse();
  if (candles.length < 55) throw new Error('Not enough valid candles for the EMA 9/21, EMA 50, and RSI filters. Try another pair or timeframe.');
  const closes = candles.map(c => c.close);
  const fast = ema(closes, 9), slow = ema(closes, 21), trend = ema(closes, 50);
  const last = closes.length - 1, prev = last - 1;
  const rsi14 = rsi(closes, 14);
  let crossover = 'NONE';
  if (fast[prev] <= slow[prev] && fast[last] > slow[last]) crossover = 'BULLISH';
  else if (fast[prev] >= slow[prev] && fast[last] < slow[last]) crossover = 'BEARISH';

  // Multi-filter entry: crossover + EMA50 trend + RSI momentum confirmation.
  // RSI limits avoid chasing the most extended readings; they do not predict outcomes.
  let direction = 'WAIT';
  let reason = 'No fresh EMA 9/21 crossover on the latest candle. Wait; do not force a trade.';
  if (crossover === 'BULLISH') {
    if (closes[last] > trend[last] && rsi14 >= 50 && rsi14 <= 68) {
      direction = 'BUY'; reason = 'Bullish EMA 9/21 crossover confirmed above EMA 50 with RSI(14) between 50 and 68.';
    } else {
      reason = `Bullish crossover detected, but filters did not confirm it (close must be above EMA 50 and RSI 50–68; current RSI ${rsi14.toFixed(1)}). WAIT.`;
    }
  } else if (crossover === 'BEARISH') {
    if (closes[last] < trend[last] && rsi14 <= 50 && rsi14 >= 32) {
      direction = 'SELL'; reason = 'Bearish EMA 9/21 crossover confirmed below EMA 50 with RSI(14) between 32 and 50.';
    } else {
      reason = `Bearish crossover detected, but filters did not confirm it (close must be below EMA 50 and RSI 32–50; current RSI ${rsi14.toFixed(1)}). WAIT.`;
    }
  }

  const trueRanges = [];
  for (let i = 1; i < candles.length; i++) {
    const c = candles[i], previousClose = candles[i - 1].close;
    trueRanges.push(Math.max(c.high - c.low, Math.abs(c.high - previousClose), Math.abs(c.low - previousClose)));
  }
  const atrWindow = trueRanges.slice(-14);
  const atr = atrWindow.length ? atrWindow.reduce((sum, v) => sum + v, 0) / atrWindow.length : 0;
  const entry = closes[last], stopDistance = atr * 1.5;
  const decimals = /JPY/.test(symbol) ? 3 : 5;
  let stopLoss = null, takeProfit = null, riskDistance = null, rewardDistance = null;
  if ((direction === 'BUY' || direction === 'SELL') && Number.isFinite(stopDistance) && stopDistance > 0) {
    riskDistance = stopDistance; rewardDistance = stopDistance * riskReward;
    stopLoss = direction === 'BUY' ? entry - stopDistance : entry + stopDistance;
    takeProfit = direction === 'BUY' ? entry + rewardDistance : entry - rewardDistance;
  }
  return {
    symbol, interval, direction, crossover, reason,
    price: Number(entry.toFixed(decimals)),
    ema9: Number(fast[last].toFixed(decimals)), ema21: Number(slow[last].toFixed(decimals)),
    ema50: Number(trend[last].toFixed(decimals)), rsi14: Number(rsi14.toFixed(1)),
    atr14: Number(atr.toFixed(decimals)),
    entryPrice: (direction === 'BUY' || direction === 'SELL') ? Number(entry.toFixed(decimals)) : null,
    stopLoss: stopLoss === null ? null : Number(stopLoss.toFixed(decimals)),
    takeProfit: takeProfit === null ? null : Number(takeProfit.toFixed(decimals)),
    riskReward, stopAtrMultiplier: 1.5,
    riskDistance: riskDistance === null ? null : Number(riskDistance.toFixed(decimals)),
    rewardDistance: rewardDistance === null ? null : Number(rewardDistance.toFixed(decimals)),
    candleTime: candles[last].time, candlesUsed: candles.length,
    strategy: 'EMA 9/21 crossover + EMA 50 trend + RSI(14) filter + ATR(14) risk levels',
    generatedAt: new Date().toISOString(), dataSource: 'Twelve Data',
    disclaimer: 'Experimental rule-based signal, not a profitability guarantee. Costs, spread, slippage, news, and execution can change outcomes. Backtest out-of-sample and paper-trade first.'
  };
}

app.get('/api/health', (_req, res) => {
  res.json({ ok: true, service: 'Forever Forex Signals API', apiKeyConfigured: Boolean(API_KEY), timestamp: new Date().toISOString() });
});

app.get('/api/signals', async (req, res) => {
  if (!API_KEY || API_KEY === 'put_your_key_here') {
    return res.status(503).json({ error: 'API key not configured. Copy .env.example to .env and add your Twelve Data key there.' });
  }
  const symbol = String(req.query.symbol || 'EUR/USD').toUpperCase();
  const interval = String(req.query.interval || '15min').toLowerCase();
  const requestedRR = Number(req.query.rr || 2);
  const riskReward = [1, 1.5, 2, 3].includes(requestedRR) ? requestedRR : 2;
  if (!allowedPairs.has(symbol)) return res.status(400).json({ error: 'Unsupported pair. Choose a pair from the website list.' });
  if (!allowedIntervals.has(interval)) return res.status(400).json({ error: 'Unsupported interval. Choose 5min, 15min, 30min, 1h, 4h, or 1day.' });

  const key = `${symbol}|${interval}|rr:${riskReward}|strategy:v2`; 
  const cached = cache.get(key);
  if (cached && Date.now() - cached.savedAt < CACHE_SECONDS * 1000) {
    return res.json({ ...cached.payload, cached: true, cacheAgeSeconds: Math.floor((Date.now() - cached.savedAt) / 1000) });
  }

  try {
    const url = new URL('https://api.twelvedata.com/time_series');
    url.searchParams.set('symbol', symbol);
    url.searchParams.set('interval', interval);
    url.searchParams.set('outputsize', '100');
    url.searchParams.set('timezone', 'UTC');
    const upstream = await fetch(url, {
      headers: { Authorization: `apikey ${API_KEY}`, Accept: 'application/json' },
      signal: AbortSignal.timeout(12_000)
    });
    const body = await upstream.json();
    if (!upstream.ok || body.status === 'error' || body.code) {
      const status = body.code === 429 ? 429 : 502;
      return res.status(status).json({ error: body.message || 'Twelve Data could not return market data. Check your plan, symbol, and API usage.' });
    }
    if (!Array.isArray(body.values)) return res.status(502).json({ error: 'Unexpected response from market-data provider.' });
    const payload = makeSignal(symbol, interval, body.values, riskReward);
    cache.set(key, { savedAt: Date.now(), payload });
    return res.json({ ...payload, cached: false, cacheSeconds: CACHE_SECONDS });
  } catch (error) {
    const message = error.name === 'TimeoutError' || error.name === 'AbortError'
      ? 'Market-data request timed out. Please try again shortly.'
      : (error.message || 'Unable to retrieve market data.');
    return res.status(502).json({ error: message });
  }
});


// The Python bridge binds only to localhost and refuses non-demo accounts.
const MT5_BRIDGE_URL = 'http://127.0.0.1:8765';
async function proxyMt5(req, res, endpoint) {
  try {
    const response = await fetch(`${MT5_BRIDGE_URL}${endpoint}`, {
      method: req.method,
      headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
      body: req.method === 'POST' ? JSON.stringify(req.body || {}) : undefined,
      signal: AbortSignal.timeout(8000)
    });
    const data = await response.json();
    res.status(response.status).json(data);
  } catch {
    res.status(503).json({ error: 'Local MT5 bridge is offline. Start mt5_demo_bridge.py in another terminal and ensure MT5 is open and logged into a demo account.' });
  }
}
app.get('/api/mt5/status', (req, res) => proxyMt5(req, res, '/status'));
function localOnly(req, res, next) {
  const origin = req.get('origin');
  if (origin && !['http://localhost:3000', 'http://127.0.0.1:3000'].includes(origin)) {
    return res.status(403).json({ error: 'MT5 demo actions are allowed only from the local website.' });
  }
  next();
}
app.post('/api/mt5/preview', localOnly, (req, res) => proxyMt5(req, res, '/preview'));
app.post('/api/mt5/execute', localOnly, (req, res) => proxyMt5(req, res, '/execute'));

app.get('*', (_req, res) => res.sendFile(path.join(__dirname, 'public', 'index.html')));
app.listen(PORT, '127.0.0.1', () => {
  console.log(`Forever Forex Signals running at http://localhost:${PORT}`);
  if (!API_KEY || API_KEY === 'put_your_key_here') console.warn('Twelve Data key missing: add it to .env (never to public/ files).');
});
