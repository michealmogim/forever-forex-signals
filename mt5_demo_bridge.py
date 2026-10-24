"""Local-only MT5 demo bridge for Forever Forex Signals.
Requires MetaTrader 5 desktop terminal logged into a DEMO account and `pip install MetaTrader5`.
Binds to 127.0.0.1 only. Never expose this service to the internet.
"""
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse
from datetime import datetime, time
from zoneinfo import ZoneInfo
import json, math, secrets, threading, time as time_module

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

HOST, PORT = '127.0.0.1', 8765
RISK_PERCENT = 0.5
DAILY_LOSS_LIMIT_PERCENT = 2.0
MAX_VOLUME = 0.10
MAX_OPEN_POSITIONS = 2
ALLOWED = {'EUR/USD':'EURUSD','GBP/USD':'GBPUSD','USD/JPY':'USDJPY','USD/CHF':'USDCHF','AUD/USD':'AUDUSD','USD/CAD':'USDCAD','NZD/USD':'NZDUSD','EUR/GBP':'EURGBP','EUR/JPY':'EURJPY','GBP/JPY':'GBPJPY','AUD/JPY':'AUDJPY','EUR/AUD':'EURAUD','GBP/CHF':'GBPCHF'}
PREVIEWS = {}
LOCK = threading.Lock()

def account_state():
    if mt5 is None:
        raise RuntimeError('MetaTrader5 Python package is missing. Run: py -m pip install MetaTrader5')
    if not mt5.initialize():
        raise RuntimeError(f'MT5 initialize failed: {mt5.last_error()}. Open MT5 and log in to your demo account first.')
    account = mt5.account_info()
    terminal = mt5.terminal_info()
    if account is None or terminal is None:
        raise RuntimeError('MT5 account/terminal information unavailable. Check MT5 is open and logged in.')
    demo_mode = getattr(mt5, 'ACCOUNT_TRADE_MODE_DEMO', 0)
    if account.trade_mode != demo_mode:
        raise PermissionError('Safety lock: this account is NOT marked DEMO by MT5. No orders are allowed.')
    if not terminal.connected:
        raise RuntimeError('MT5 terminal is not connected to the broker.')
    if not terminal.trade_allowed or not account.trade_allowed:
        raise RuntimeError('Trading is disabled in MT5 terminal/account settings.')
    if account.currency != 'USD':
        raise RuntimeError(f'Safety lock: this bridge is configured for USD accounts only; account currency is {account.currency}.')
    return account

def daily_closed_pnl():
    # Fail closed if broker history is unavailable; include profit, commission and swap.
    now = datetime.now().astimezone()
    start = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    deals = mt5.history_deals_get(start, now)
    if deals is None:
        raise RuntimeError('Cannot read today\'s deal history; daily-loss safety check failed closed.')
    return sum(float(d.profit) + float(d.commission) + float(d.swap) + float(getattr(d, 'fee', 0) or 0) for d in deals)

def round_down_volume(info, volume):
    step = float(info.volume_step or 0.01)
    minv = float(info.volume_min or step)
    maxv = min(float(info.volume_max or MAX_VOLUME), MAX_VOLUME)
    v = min(maxv, math.floor((volume + 1e-12) / step) * step)
    digits = max(0, min(8, len(str(step).split('.')[-1].rstrip('0')) if '.' in str(step) else 0))
    v = round(v, digits)
    return v if v >= minv else 0.0

def send_json(handler, code, data):
    raw = json.dumps(data).encode()
    handler.send_response(code)
    handler.send_header('Content-Type','application/json; charset=utf-8')
    handler.send_header('Cache-Control','no-store')
    handler.send_header('Content-Length',str(len(raw)))
    handler.end_headers(); handler.wfile.write(raw)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def read_json(self):
        n=int(self.headers.get('Content-Length','0'))
        if n > 4096: raise ValueError('Request too large.')
        return json.loads(self.rfile.read(n) or b'{}')
    def do_GET(self):
        if urlparse(self.path).path != '/status': return send_json(self,404,{'error':'Not found'})
        try:
            a=account_state()
            send_json(self,200,{'connected':True,'demo':True,'currency':a.currency,'balance':a.balance,'equity':a.equity,'server':a.server,'company':a.company,'maxVolume':MAX_VOLUME,'riskPercent':RISK_PERCENT,'dailyLossLimitPercent':DAILY_LOSS_LIMIT_PERCENT})
        except Exception as e:
            send_json(self,503,{'connected':False,'demo':False,'error':str(e)})
        finally:
            if mt5: mt5.shutdown()
    def do_POST(self):
        route=urlparse(self.path).path
        if route not in ('/preview','/execute'): return send_json(self,404,{'error':'Not found'})
        try:
            body=self.read_json()
            if route=='/preview': return self.preview(body)
            return self.execute(body)
        except PermissionError as e: return send_json(self,403,{'error':str(e)})
        except Exception as e: return send_json(self,400,{'error':str(e)})
        finally:
            if mt5: mt5.shutdown()
    def preview(self,b):
        a=account_state()
        symbol_key=str(b.get('symbol','')).upper()
        if symbol_key not in ALLOWED: raise ValueError('Currency pair is not allowed by this demo bridge.')
        direction=str(b.get('direction','')).upper()
        if direction not in ('BUY','SELL'): raise ValueError('Direction must be BUY or SELL.')
        stop_dist=float(b.get('stopDistance',0)); reward_dist=float(b.get('rewardDistance',0))
        if not (math.isfinite(stop_dist) and math.isfinite(reward_dist) and stop_dist>0 and reward_dist>=stop_dist): raise ValueError('Invalid stop-loss/take-profit distances. Scan a fresh signal first.')
        sym=ALLOWED[symbol_key]
        if not mt5.symbol_select(sym,True): raise RuntimeError(f'Broker symbol {sym} is unavailable. Check Market Watch / broker suffix.')
        info=mt5.symbol_info(sym); tick=mt5.symbol_info_tick(sym)
        if info is None or tick is None or tick.bid<=0 or tick.ask<=0: raise RuntimeError('No valid broker quote for this symbol.')
        if (datetime.now().timestamp()-tick.time)>120: raise RuntimeError('Broker quote is older than 120 seconds; refusing to prepare an order.')
        price=float(tick.ask if direction=='BUY' else tick.bid)
        digits=int(info.digits)
        sl=price-stop_dist if direction=='BUY' else price+stop_dist
        tp=price+reward_dist if direction=='BUY' else price-reward_dist
        sl=round(sl,digits); tp=round(tp,digits)
        if direction=='BUY' and not (sl<price<tp): raise ValueError('BUY stop-loss/take-profit sides are invalid.')
        if direction=='SELL' and not (tp<price<sl): raise ValueError('SELL stop-loss/take-profit sides are invalid.')
        stops_level=float(info.trade_stops_level or 0)*float(info.point)
        if abs(price-sl)<stops_level or abs(tp-price)<stops_level: raise ValueError('Stop-loss or take-profit is closer than the broker minimum stop distance.')
        closed_pnl=daily_closed_pnl()
        if closed_pnl <= -(float(a.balance)*DAILY_LOSS_LIMIT_PERCENT/100): raise RuntimeError('Daily loss lockout active: realized loss has reached the 2% daily limit.')
        positions=mt5.positions_get()
        if positions is None: raise RuntimeError('Could not verify open positions; refusing to prepare an order.')
        if len(positions)>=MAX_OPEN_POSITIONS: raise RuntimeError('Open-position limit reached (2 positions maximum).')
        risk_cash=float(a.balance)*RISK_PERCENT/100
        one_lot_profit=mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if direction=='BUY' else mt5.ORDER_TYPE_SELL,sym,1.0,price,sl)
        if one_lot_profit is None or one_lot_profit==0: raise RuntimeError('Broker could not calculate stop-loss risk for this order.')
        raw_volume=risk_cash/abs(float(one_lot_profit))
        volume=round_down_volume(info,raw_volume)
        if volume<=0: raise RuntimeError('Calculated safe volume is below the broker minimum lot size; order blocked.')
        estimated_loss=abs(float(mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if direction=='BUY' else mt5.ORDER_TYPE_SELL,sym,volume,price,sl) or 0))
        estimated_profit=float(mt5.order_calc_profit(mt5.ORDER_TYPE_BUY if direction=='BUY' else mt5.ORDER_TYPE_SELL,sym,volume,price,tp) or 0)
        if estimated_loss > risk_cash*1.03: raise RuntimeError('Estimated risk exceeds the 0.5% limit after volume rounding; order blocked.')
        order_type=mt5.ORDER_TYPE_BUY if direction=='BUY' else mt5.ORDER_TYPE_SELL
        request={'action':mt5.TRADE_ACTION_DEAL,'symbol':sym,'volume':volume,'type':order_type,'price':price,'sl':sl,'tp':tp,'deviation':20,'magic':261024,'comment':'ForeverForex DEMO','type_time':mt5.ORDER_TIME_GTC,'type_filling':info.filling_mode}
        margin=mt5.order_calc_margin(order_type,sym,volume,price)
        if margin is None or margin>a.margin_free: raise RuntimeError('Insufficient free margin or margin estimate unavailable.')
        check=mt5.order_check(request)
        if check is None or getattr(check,'retcode',None) not in (0,mt5.TRADE_RETCODE_DONE):
            # Some MT5 builds use retcode 0 for successful order_check; fail closed otherwise.
            raise RuntimeError(f'MT5 order_check rejected the request: {getattr(check,"comment","no response") if check else mt5.last_error()}')
        preview_id=secrets.token_urlsafe(24)
        item={'previewId':preview_id,'createdAt':time_module.time(),'symbol':sym,'displaySymbol':symbol_key,'direction':direction,'entryPrice':round(price,digits),'quoteSide':'ASK' if direction=='BUY' else 'BID','stopLoss':sl,'takeProfit':tp,'volume':volume,'currency':a.currency,'balance':a.balance,'riskPercent':RISK_PERCENT,'estimatedLoss':round(estimated_loss,2),'estimatedProfit':round(estimated_profit,2),'marginRequired':round(float(margin),2),'openPositions':len(positions),'request':request}
        with LOCK:
            PREVIEWS.clear() if len(PREVIEWS)>100 else None
            PREVIEWS[preview_id]=item
        public={k:v for k,v in item.items() if k not in ('request','createdAt')}
        return send_json(self,200,public)
    def execute(self,b):
        if b.get('confirm') is not True: raise ValueError('Explicit confirmation is required.')
        preview_id=str(b.get('previewId',''))
        with LOCK: item=PREVIEWS.pop(preview_id,None)
        if not item: raise ValueError('Preview expired or already used. Prepare a new order.')
        if time_module.time()-item['createdAt']>60: raise ValueError('Preview expired after 60 seconds. Prepare a fresh order.')
        a=account_state()
        if a.currency!='USD': raise RuntimeError('USD account check failed; order blocked.')
        tick=mt5.symbol_info_tick(item['symbol'])
        if tick is None: raise RuntimeError('Broker quote unavailable at confirmation.')
        current=float(tick.ask if item['direction']=='BUY' else tick.bid)
        if abs(current-item['entryPrice']) > max(item['entryPrice']*0.0005,0.0005): raise RuntimeError('Price moved materially since preview. No order sent; prepare a fresh preview.')
        if a.balance<=0: raise RuntimeError('Invalid account balance.')
        closed_pnl=daily_closed_pnl()
        if closed_pnl <= -(float(a.balance)*DAILY_LOSS_LIMIT_PERCENT/100): raise RuntimeError('Daily loss lockout active; order blocked.')
        positions=mt5.positions_get()
        if positions is None or len(positions)>=MAX_OPEN_POSITIONS: raise RuntimeError('Open-position limit reached or position check failed.')
        request=item['request'].copy(); request['price']=current
        check=mt5.order_check(request)
        if check is None or getattr(check,'retcode',None) not in (0,mt5.TRADE_RETCODE_DONE): raise RuntimeError(f'Final MT5 order_check rejected: {getattr(check,"comment","no response") if check else mt5.last_error()}')
        result=mt5.order_send(request)
        if result is None: raise RuntimeError(f'MT5 order_send returned no result: {mt5.last_error()}')
        if result.retcode not in (mt5.TRADE_RETCODE_DONE, getattr(mt5,'TRADE_RETCODE_PLACED',10008)):
            raise RuntimeError(f'MT5 rejected order ({result.retcode}): {result.comment}')
        return send_json(self,200,{'ok':True,'message':'Demo order accepted by MT5','ticket':getattr(result,'order',None) or getattr(result,'deal',None),'retcode':result.retcode,'volume':item['volume'],'symbol':item['symbol'],'direction':item['direction']})

if __name__=='__main__':
    print(f'Forever Forex MT5 DEMO bridge listening on http://{HOST}:{PORT}')
    print('Safety: localhost only; DEMO mode and USD currency required; 0.5% risk target; 0.10-lot cap; 2% daily loss lockout; max 2 open positions.')
    HTTPServer((HOST,PORT),Handler).serve_forever()
