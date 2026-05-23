from __future__ import annotations
import os, json, zipfile, math, uuid, importlib.util, sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
import pandas as pd
import numpy as np

CACHE_DIR = '/mnt/data/wealth_cache/eod'
OUT_DIR = '/mnt/data/wealth_v3_research_results'
os.makedirs(OUT_DIR, exist_ok=True)

START = pd.Timestamp('2022-01-03').date()
END = pd.Timestamp('2026-05-22').date()
INITIAL = 4000.0

def load_payload(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def normalize(payload, ticker):
    if isinstance(payload, dict) and 'historical' in payload:
        payload = payload['historical']
    df = pd.DataFrame(payload)
    if df.empty:
        return df
    df = df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
    req = ['date','Open','High','Low','Close','Volume']
    for c in req:
        if c not in df.columns:
            return pd.DataFrame()
    df['date'] = pd.to_datetime(df['date'], errors='coerce').dt.date
    for c in ['Open','High','Low','Close','Volume']:
        df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df.dropna(subset=req).sort_values('date').drop_duplicates('date', keep='last').reset_index(drop=True)
    df['ticker'] = ticker
    return df

def rsi(series, period=14):
    delta = series.diff(); gain = delta.clip(lower=0).rolling(period).mean(); loss = -delta.clip(upper=0).rolling(period).mean().replace(0, 1e-9)
    rs = gain / loss
    return 100 - (100/(1+rs))

def atr(df, period=14):
    tr = pd.concat([(df['High']-df['Low']), (df['High']-df['Close'].shift()).abs(), (df['Low']-df['Close'].shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def add_indicators(df):
    df = df.copy()
    df['PrevClose'] = df['Close'].shift(1)
    for n in [10,20,50,63,100,126,150,200,252]:
        df[f'MA{n}'] = df['Close'].rolling(n).mean()
    df['RSI14'] = rsi(df['Close'])
    df['ATR14'] = atr(df,14)
    df['ATR50'] = atr(df,50)
    df['ATRpct'] = df['ATR14'] / df['Close']
    df['ATRRatio14_50'] = df['ATR14'] / df['ATR50']
    df['AvgVol20'] = df['Volume'].rolling(20).mean()
    df['AvgVol50'] = df['Volume'].rolling(50).mean()
    df['DollarVol20'] = df['AvgVol20'] * df['Close']
    df['Ret1'] = df['Close'].pct_change(1)
    for n in [5,10,21,42,63,126,189,252]:
        df[f'ROC{n}'] = df['Close'].pct_change(n)
    df['Vol63'] = df['Ret1'].rolling(63).std() * np.sqrt(252)
    df['High20'] = df['Close'].shift(1).rolling(20).max()
    df['High55'] = df['Close'].shift(1).rolling(55).max()
    df['Low20'] = df['Low'].shift(1).rolling(20).min()
    df['Range20'] = (df['High'].shift(1).rolling(20).max() - df['Low'].shift(1).rolling(20).min()) / df['Close']
    bb_mid = df['Close'].rolling(20).mean(); bb_std = df['Close'].rolling(20).std()
    df['BBWidth20'] = ((bb_mid + 2*bb_std) - (bb_mid - 2*bb_std)) / bb_mid
    df['BBWidthRank100'] = df['BBWidth20'].rolling(100).rank(pct=True)
    df['CloseLoc'] = ((df['Close']-df['Low'])/(df['High']-df['Low']).replace(0,np.nan)).clip(0,1).fillna(0.5)
    df['DailyMovePct'] = (df['Close']/df['PrevClose']-1)*100
    return df

# Load cache
all_data: Dict[str,pd.DataFrame] = {}
for fn in os.listdir(CACHE_DIR):
    if not fn.endswith('.json'): continue
    ticker = fn[:-5]
    df = normalize(load_payload(os.path.join(CACHE_DIR, fn)), ticker)
    if df.empty or len(df) < 260: continue
    df = add_indicators(df)
    df = df.set_index('date', drop=False)
    all_data[ticker] = df
print('loaded', len(all_data), 'tickers')

# dates
all_dates = sorted([d for d in all_data['SPY'].index if START <= d <= END])

def price(t, d, col='Close'):
    df = all_data.get(t)
    if df is None or d not in df.index: return np.nan
    return float(df.loc[d, col])

def max_dd(equity: pd.Series):
    rm = equity.cummax(); dd = equity/rm - 1
    return float(dd.min()*100), float((equity-rm).min())

def cagr(initial, final, start, end):
    years = (pd.Timestamp(end)-pd.Timestamp(start)).days/365.25
    return (final/initial)**(1/years)-1 if initial>0 and final>0 and years>0 else np.nan

def annual_summary(eq_df):
    df=eq_df.copy(); df['year']=pd.to_datetime(df['date']).dt.year
    rows=[]
    for y,g in df.groupby('year'):
        start_eq=float(g['equity'].iloc[0]); end_eq=float(g['equity'].iloc[-1])
        mdd,_=max_dd(g.set_index('date')['equity'])
        rows.append({'year':int(y),'start_equity':round(start_eq,2),'end_equity':round(end_eq,2),'year_return_pct':round((end_eq/start_eq-1)*100,2),'max_dd_pct':round(mdd,2)})
    return pd.DataFrame(rows)

def summarize(name, eq_df, trades=None, exposure=None):
    final=float(eq_df['equity'].iloc[-1]); ret=(final/INITIAL-1)*100; mdd, mddd=max_dd(eq_df.set_index('date')['equity'])
    daily=eq_df.set_index('date')['equity'].pct_change().dropna()
    sharpe=(daily.mean()/daily.std()*np.sqrt(252)) if len(daily)>2 and daily.std()>0 else np.nan
    row={'name':name,'start':str(eq_df['date'].iloc[0]),'end':str(eq_df['date'].iloc[-1]),'final_equity':round(final,2),'net_profit':round(final-INITIAL,2),'return_pct':round(ret,2),'cagr_pct':round(cagr(INITIAL,final,eq_df['date'].iloc[0],eq_df['date'].iloc[-1])*100,2),'max_dd_pct':round(mdd,2),'max_dd_dollars':round(mddd,2),'sharpe':round(float(sharpe),3) if not pd.isna(sharpe) else None}
    if trades is not None:
        tdf=pd.DataFrame(trades)
        row['trades']=len(tdf)
        if len(tdf):
            row['win_rate_pct']=round((tdf['profit']>0).mean()*100,2)
            gp=tdf.loc[tdf['profit']>0,'profit'].sum(); gl=-tdf.loc[tdf['profit']<0,'profit'].sum()
            row['profit_factor']=round(gp/gl,3) if gl>0 else None
            row['avg_profit']=round(tdf['profit'].mean(),2)
        else:
            row['win_rate_pct']=None; row['profit_factor']=None; row['avg_profit']=None
    if exposure is not None:
        row['exposure_pct']=round(exposure,2)
    return row

# Regime helpers
SAFE = {'BIL','SGOV','SHY'}
CASHLIKE = {'BIL','SGOV'}

def market_score(d):
    score=0
    for t in ['SPY','QQQ']:
        df=all_data[t]
        if d not in df.index: continue
        r=df.loc[d]
        if r['Close']>r['MA50']: score+=1
        if r['MA20']>r['MA50']: score+=1
        try:
            ix=df.index.get_loc(d)
            if ix>=10 and df.iloc[ix]['MA50']>df.iloc[ix-10]['MA50']: score+=1
        except: pass
    for t in ['IWM','SMH']:
        df=all_data[t]
        if d in df.index and df.loc[d,'Close']>df.loc[d,'MA50']: score+=1
    return score

def market_regime(d):
    s=market_score(d)
    if s>=6: return 'BULL'
    if s<=2: return 'BEAR'
    return 'UNCERTAIN'

# Rotation backtester

def rotation_backtest(name, universe, top_n=3, rebalance='M', trend=True, safe_universe=None, rank_style='balanced', slippage_bps=10, risk_off_mode=False, min_score=None):
    uni=[t for t in universe if t in all_data]
    if safe_universe is None: safe_universe=['BIL'] if 'BIL' in all_data else []
    safe_universe=[t for t in safe_universe if t in all_data]
    dates=all_dates
    equity=INITIAL; holdings={}; rows=[]; turnovers=[]
    last_month=None; last_quarter=None
    for i,d in enumerate(dates):
        # apply daily close-to-close returns from previous day holdings
        if i>0:
            prev=dates[i-1]
            daily_ret=0.0
            for t,w in holdings.items():
                if t in all_data and d in all_data[t].index and prev in all_data[t].index:
                    pc=float(all_data[t].loc[prev,'Close']); cc=float(all_data[t].loc[d,'Close'])
                    if pc>0: daily_ret += w*(cc/pc-1)
            equity *= (1+daily_ret)
        # rebalance at first trading day of month/quarter
        pd_d=pd.Timestamp(d)
        do=False
        if rebalance=='M':
            if last_month != (pd_d.year,pd_d.month): do=True; last_month=(pd_d.year,pd_d.month)
        elif rebalance=='Q':
            q=(pd_d.month-1)//3+1
            if last_quarter != (pd_d.year,q): do=True; last_quarter=(pd_d.year,q)
        if do:
            candidates=[]
            reg=market_regime(d)
            for t in uni:
                df=all_data[t]
                if d not in df.index: continue
                r=df.loc[d]
                if pd.isna(r['ROC126']) or pd.isna(r['ROC63']) or pd.isna(r['ROC21']) or pd.isna(r['Vol63']): continue
                if trend and t not in SAFE and (pd.isna(r['MA200']) or r['Close'] <= r['MA200']):
                    continue
                if risk_off_mode and reg=='BEAR' and t not in set(safe_universe+['GLD','IAU','SHY','IEF','TLT','XLU','XLV','XLP']):
                    continue
                if rank_style=='balanced':
                    score=0.45*r['ROC126']+0.35*r['ROC63']+0.20*r['ROC21']-0.18*r['Vol63']
                elif rank_style=='longterm':
                    score=0.50*r['ROC252']+0.30*r['ROC126']+0.20*r['ROC63']-0.15*r['Vol63'] if not pd.isna(r['ROC252']) else np.nan
                elif rank_style=='defensive':
                    dd = r['Close']/df.loc[:d,'Close'].tail(252).max()-1 if len(df.loc[:d])>20 else 0
                    score=0.45*r['ROC63']+0.25*r['ROC21']-0.35*r['Vol63']+0.25*dd
                else:
                    score=0.45*r['ROC126']+0.35*r['ROC63']+0.20*r['ROC21']-0.18*r['Vol63']
                if pd.isna(score): continue
                if min_score is not None and score < min_score: continue
                candidates.append((score,t))
            if not candidates and safe_universe:
                candidates=[(0,t) for t in safe_universe]
            candidates=sorted(candidates, reverse=True)[:top_n]
            new={t:1/len(candidates) for score,t in candidates} if candidates else {}
            turnover=0.0
            keys=set(holdings)|set(new)
            for t in keys: turnover += abs(new.get(t,0)-holdings.get(t,0))
            equity *= (1 - turnover*slippage_bps/10000)
            turnovers.append({'date':d,'turnover':turnover,'holdings':','.join(new.keys()),'regime':reg})
            holdings=new
        rows.append({'date':d,'equity':equity,'holdings':','.join(holdings.keys()),'positions':len(holdings),'regime':market_regime(d)})
    eq=pd.DataFrame(rows)
    return eq, pd.DataFrame(turnovers)

# Swing speculative engine
@dataclass
class SwingConfig:
    name: str
    universe: List[str]
    risk_pct: float=0.005
    max_positions: int=3
    max_position_pct: float=0.10
    vol_ratio: float=1.8
    min_move: float=3.0
    max_move: float=14.0
    close_loc: float=0.70
    atr_stop: float=2.0
    trail_early: float=3.0
    trail_late: float=2.3
    partial_r: float=1.25
    partial_pct: float=0.12
    partial_fraction: float=0.4
    breakeven_r: float=1.0
    time_stop_days: int=6
    time_stop_min_r: float=0.25
    max_hold_days: int=20
    slippage_bps: float=25
    require_regime_score: int=5
    min_price: float=5.0
    min_dollar_vol: float=20_000_000
    max_atr_pct: float=0.18
    min_atr_pct: float=0.02

CRYPTO={'COIN','HOOD','MSTR','MARA','RIOT','CLSK','IREN','WULF','HUT','BITF'}

def swing_backtest(cfg: SwingConfig):
    dates=all_dates; cash=INITIAL; positions={}; trades=[]; rows=[]; pending=[]
    for i,d in enumerate(dates):
        # execute pending at open
        todays=pending; pending=[]
        for sig in todays:
            t=sig['ticker']
            if t in positions: continue
            if t not in all_data or d not in all_data[t].index: continue
            entry=float(all_data[t].loc[d,'Open'])*(1+cfg.slippage_bps/10000)
            stop=sig['stop']
            riskps=entry-stop
            if riskps<=0 or stop<=0: continue
            equity=cash+sum(pos['shares']*price(tt,d,'Close') for tt,pos in positions.items())
            shares_by_risk=int(equity*cfg.risk_pct/riskps)
            shares_by_pos=int(equity*cfg.max_position_pct/entry)
            shares_by_cash=int(cash*0.98/entry)
            shares=max(0,min(shares_by_risk,shares_by_pos,shares_by_cash))
            if shares<=0: continue
            # crypto correlation cap
            if t in CRYPTO and any(tt in CRYPTO for tt in positions): continue
            cost=shares*entry
            if cost>cash: continue
            cash-=cost
            positions[t]={'ticker':t,'entry_date':d,'entry_price':entry,'shares':shares,'initial_shares':shares,'stop':stop,'initial_stop':stop,'highest':entry,'atr':sig['atr'],'riskps':riskps,'partial':False,'setup':sig['setup'],'score':sig.get('score',0)}
        # manage
        for t in list(positions.keys()):
            pos=positions[t]
            if d not in all_data[t].index: continue
            r=all_data[t].loc[d]
            high=float(r['High']); low=float(r['Low']); close=float(r['Close'])
            pos['highest']=max(pos['highest'],high)
            trade_r=(close-pos['entry_price'])/pos['riskps']
            # effective stop
            eff=pos['stop']
            mult=cfg.trail_late if close>=pos['entry_price']*1.05 else cfg.trail_early
            trail=pos['highest']-mult*pos['atr']
            if trail>eff: eff=trail
            if trade_r>=cfg.breakeven_r and eff<pos['entry_price']: eff=pos['entry_price']
            # time stop
            days_held=(pd.Timestamp(d)-pd.Timestamp(pos['entry_date'])).days
            reason=None; fill=None; shares_exit=0
            if low<=eff:
                reason='stop'; fill=eff*(1-cfg.slippage_bps/10000); shares_exit=pos['shares']
            elif cfg.time_stop_days and days_held>=cfg.time_stop_days and trade_r<cfg.time_stop_min_r:
                reason='time_stop'; fill=close*(1-cfg.slippage_bps/10000); shares_exit=pos['shares']
            elif cfg.max_hold_days and days_held>=cfg.max_hold_days:
                reason='max_hold'; fill=close*(1-cfg.slippage_bps/10000); shares_exit=pos['shares']
            elif (not pos['partial']) and pos['shares']>1 and (trade_r>=cfg.partial_r or close>=pos['entry_price']*(1+cfg.partial_pct)):
                reason='partial'; fill=close*(1-cfg.slippage_bps/10000); shares_exit=max(1,int(pos['shares']*cfg.partial_fraction))
            if reason:
                profit=(fill-pos['entry_price'])*shares_exit
                cash += shares_exit*fill
                trades.append({'ticker':t,'entry_date':pos['entry_date'],'exit_date':d,'entry_price':round(pos['entry_price'],4),'exit_price':round(fill,4),'shares':shares_exit,'profit':profit,'r_multiple':(fill-pos['entry_price'])/pos['riskps'],'reason':reason,'setup':pos['setup'],'days':days_held})
                pos['shares']-=shares_exit
                if pos['shares']<=0 or reason!='partial':
                    del positions[t]
                else:
                    pos['partial']=True; pos['stop']=max(eff,pos['entry_price'])
                    positions[t]=pos
            else:
                pos['stop']=eff; positions[t]=pos
        # equity
        eq=cash+sum(pos['shares']*price(t,d,'Close') for t,pos in positions.items() if not np.isnan(price(t,d,'Close')))
        rows.append({'date':d,'equity':eq,'cash':cash,'positions':len(positions)})
        if i>=len(dates)-1: continue
        # signal generation at close, for next open
        if market_score(d)<cfg.require_regime_score: continue
        if len(positions)+len(pending)>=cfg.max_positions: continue
        candidates=[]
        for t in cfg.universe:
            if t not in all_data or t in positions: continue
            df=all_data[t]
            if d not in df.index: continue
            row=df.loc[d]
            if pd.isna(row['MA50']) or pd.isna(row['AvgVol20']) or pd.isna(row['ATR14']) or pd.isna(row['High20']): continue
            close=float(row['Close'])
            if close<cfg.min_price: continue
            if row['DollarVol20']<cfg.min_dollar_vol: continue
            atrp=float(row['ATRpct'])
            if atrp<cfg.min_atr_pct or atrp>cfg.max_atr_pct: continue
            if close<=row['MA50'] or row['MA20']<=row['MA50']: continue
            breakout = close>row['High20'] or close>row['High55']
            if not breakout: continue
            vol_ratio=float(row['Volume']/row['AvgVol20']) if row['AvgVol20']>0 else 0
            if vol_ratio<cfg.vol_ratio: continue
            move=float(row['DailyMovePct'])
            if move<cfg.min_move or move>cfg.max_move: continue
            if row['CloseLoc']<cfg.close_loc: continue
            # relative strength vs QQQ 20d
            rs=0
            if not pd.isna(row['ROC21']) and d in all_data['QQQ'].index and not pd.isna(all_data['QQQ'].loc[d,'ROC21']):
                rs=float(row['ROC21']-all_data['QQQ'].loc[d,'ROC21'])
                if rs < -0.02: continue
            score=vol_ratio*10 + row['ROC21']*100 + row['CloseLoc']*10 + (5 if close>row['High55'] else 0) + rs*100
            stop=max(close - cfg.atr_stop*float(row['ATR14']), float(row['Low20']) if not pd.isna(row['Low20']) else close-cfg.atr_stop*float(row['ATR14']))
            # choose lower/wider of structure and ATR stop for long
            stop=min(close - cfg.atr_stop*float(row['ATR14']), float(row['Low20']) if not pd.isna(row['Low20']) else close-cfg.atr_stop*float(row['ATR14']))
            candidates.append({'ticker':t,'date':d,'atr':float(row['ATR14']),'stop':stop,'setup':'spec_breakout','score':score})
        candidates=sorted(candidates,key=lambda x:x['score'], reverse=True)
        slots=cfg.max_positions-len(positions)-len(pending)
        pending.extend(candidates[:max(0,slots)])
    # liquidate final
    final=dates[-1]
    for t,pos in list(positions.items()):
        fill=price(t,final,'Close')*(1-cfg.slippage_bps/10000)
        profit=(fill-pos['entry_price'])*pos['shares']; cash+=pos['shares']*fill
        trades.append({'ticker':t,'entry_date':pos['entry_date'],'exit_date':final,'entry_price':round(pos['entry_price'],4),'exit_price':round(fill,4),'shares':pos['shares'],'profit':profit,'r_multiple':(fill-pos['entry_price'])/pos['riskps'],'reason':'end','setup':pos['setup'],'days':(pd.Timestamp(final)-pd.Timestamp(pos['entry_date'])).days})
        del positions[t]
    eq=pd.DataFrame(rows)
    eq.loc[eq.index[-1],'equity']=cash
    return eq, trades

# Buy-hold benchmark
def buyhold(t):
    rows=[]; sh=None; cash=0
    first=None
    for d in all_dates:
        if d in all_data[t].index:
            if first is None:
                first=d; sh=INITIAL/price(t,d,'Close')
            rows.append({'date':d,'equity':sh*price(t,d,'Close'),'holdings':t})
    return pd.DataFrame(rows)

# Run rotation sweeps
core_universe=['SPY','QQQ','VTI','VOO','IWM','SMH','SOXX','XLK','XLV','XLE','XLF','XLI','XLY','XLC','GLD','IAU','SLV','BIL','SGOV','SHY','IEF','TLT','DBC','DBB','CPER']
defensive=['BIL','SGOV','SHY','IEF','TLT','GLD','IAU','XLP','XLV','XLU']
quality=['MSFT','NVDA','META','AMZN','GOOGL','AVGO','AAPL','LLY','COST','V','MA','JPM','CAT','GE','ETN','ISRG','PANW','CRWD','NOW','PLTR','VRT','NFLX','ADBE','CRM','SHOP','AMD','LRCX','ASML','QCOM','ANET','CDNS','SNPS','APP','TTD','URI','PWR','CEG','VST','MELI','UBER']
metals=['GLD','IAU','SLV','GDX','GDXJ','SIL','SILJ','NEM','AEM','GOLD','KGC','WPM','FNV','FCX','SCCO','CPER','DBB','AA','BHP','RIO','VALE','CLF']
spec=['COIN','HOOD','MSTR','MARA','RIOT','CLSK','AFRM','SOFI','UPST','ROKU','SNOW','AI','RBLX','CVNA','HIMS','DUOL','RDDT','SMCI','SOUN','RKLB','IONQ','BILL','DKNG','CELH','ENVX','FUBO','BMBL']
crypto=['COIN','HOOD','MSTR','MARA','RIOT','CLSK','IREN','WULF','HUT','BITF']

summaries=[]; equity_files={}; annual_files={}
# benchmarks
for t in ['SPY','QQQ','SMH','GLD','BIL']:
    if t in all_data:
        eq=buyhold(t); summaries.append(summarize('BUYHOLD_'+t, eq)); eq.to_csv(os.path.join(OUT_DIR,f'equity_BUYHOLD_{t}.csv'),index=False)

rot_variants=[]
for top in [2,3,4,5]:
    for sl in [5,10,25,50]:
        rot_variants.append(('CORE_ROT_TOP%d_%dbps'%(top,sl),core_universe,top,'M',True,['BIL','SGOV'], 'balanced', sl, True))
for top in [5,8,10]:
    for sl in [5,10,25]:
        rot_variants.append(('QUALITY_ROT_TOP%d_%dbps'%(top,sl),quality,top,'M',True,['BIL'], 'longterm', sl, False))
for top in [1,2,3]:
    for sl in [5,10,25]:
        rot_variants.append(('METALS_ROT_TOP%d_%dbps'%(top,sl),metals,top,'M',True,['BIL'], 'balanced', sl, False))
for top in [1,2,3]:
    for sl in [5,10,25]:
        rot_variants.append(('DEFENSIVE_ROT_TOP%d_%dbps'%(top,sl),defensive,top,'M',False,['BIL','SGOV'], 'defensive', sl, False))

for name,uni,top,reb,trend,safe,style,sl,riskoff in rot_variants:
    eq,turn=rotation_backtest(name,uni,top_n=top,rebalance=reb,trend=trend,safe_universe=safe,rank_style=style,slippage_bps=sl,risk_off_mode=riskoff)
    summaries.append(summarize(name,eq,exposure=float((eq['positions']>0).mean()*100)))
    eq.to_csv(os.path.join(OUT_DIR,f'equity_{name}.csv'),index=False)
    turn.to_csv(os.path.join(OUT_DIR,f'turnover_{name}.csv'),index=False)
    annual_summary(eq).to_csv(os.path.join(OUT_DIR,f'annual_{name}.csv'),index=False)

# swing variants
swing_variants=[]
for risk in [0.003,0.005,0.0075]:
  for vol in [1.6,1.8,2.2]:
    swing_variants.append(SwingConfig(name=f'SPEC_MOM_risk{risk}_vol{vol}', universe=spec, risk_pct=risk, vol_ratio=vol, slippage_bps=25, max_positions=3, require_regime_score=5))
for risk in [0.003,0.005,0.0075]:
  for vol in [1.5,1.8,2.0]:
    swing_variants.append(SwingConfig(name=f'CRYPTO_TAC_risk{risk}_vol{vol}', universe=crypto, risk_pct=risk, vol_ratio=vol, slippage_bps=25, max_positions=1, require_regime_score=5, max_atr_pct=0.25, min_dollar_vol=15_000_000))

for cfg in swing_variants:
    eq,tr=swing_backtest(cfg)
    summaries.append(summarize(cfg.name,eq,trades=tr,exposure=float((eq['positions']>0).mean()*100)))
    eq.to_csv(os.path.join(OUT_DIR,f'equity_{cfg.name}.csv'),index=False)
    pd.DataFrame(tr).to_csv(os.path.join(OUT_DIR,f'trades_{cfg.name}.csv'),index=False)
    annual_summary(eq).to_csv(os.path.join(OUT_DIR,f'annual_{cfg.name}.csv'),index=False)

# exact v2.8 long engine
try:
    spec_mod = importlib.util.spec_from_file_location('v28','/mnt/data/backtest_engine_v2_8_vcp_exit_upgrade.py')
    v28=importlib.util.module_from_spec(spec_mod); spec_mod.loader.exec_module(v28)
    data_v28={}
    for t,df in all_data.items():
        # module expects exact indicator names; use its add_indicators on raw normalized df
        raw=df.reset_index(drop=True)[['date','Open','High','Low','Close','Volume']].copy()
        raw=v28.add_indicators(raw).set_index('date',drop=False)
        data_v28[t]=raw
    cfg=v28.StrategyConfig(initial_capital=INITIAL, slippage_bps=25)
    res=v28.run_backtest_core(data_v28, START, END, cfg, run_dir=os.path.join(OUT_DIR,'exact_v28_25bps'), save_outputs=True)
    summaries.append({**res['summary'], 'name':'EXACT_LONG_V2_8_25bps'})
    res['equity'].to_csv(os.path.join(OUT_DIR,'equity_EXACT_LONG_V2_8_25bps.csv'),index=False)
    res['trades'].to_csv(os.path.join(OUT_DIR,'trades_EXACT_LONG_V2_8_25bps.csv'),index=False)
    annual_summary(res['equity']).to_csv(os.path.join(OUT_DIR,'annual_EXACT_LONG_V2_8_25bps.csv'),index=False)
    cfg50=v28.StrategyConfig(initial_capital=INITIAL, slippage_bps=50)
    res50=v28.run_backtest_core(data_v28, START, END, cfg50, run_dir=os.path.join(OUT_DIR,'exact_v28_50bps'), save_outputs=True)
    summaries.append({**res50['summary'], 'name':'EXACT_LONG_V2_8_50bps'})
    res50['equity'].to_csv(os.path.join(OUT_DIR,'equity_EXACT_LONG_V2_8_50bps.csv'),index=False)
    res50['trades'].to_csv(os.path.join(OUT_DIR,'trades_EXACT_LONG_V2_8_50bps.csv'),index=False)
    annual_summary(res50['equity']).to_csv(os.path.join(OUT_DIR,'annual_EXACT_LONG_V2_8_50bps.csv'),index=False)
except Exception as e:
    print('v28 engine failed',e)

# bear robust daily from previous exact research
try:
    bear_daily=pd.read_csv('/mnt/data/bear_results/robust_vcp_broad_3x_daily.csv')
    bear_daily['date']=pd.to_datetime(bear_daily['date']).dt.date
    bear_daily=bear_daily[(bear_daily['date']>=START)&(bear_daily['date']<=END)].copy()
    # normalize to start 4000 if starting not exact
    factor=INITIAL/float(bear_daily['equity'].iloc[0])
    bear_daily['equity']=bear_daily['equity']*factor
    bear_daily[['date','equity']].to_csv(os.path.join(OUT_DIR,'equity_BEAR_INVERSE_3X_VCP_25bps.csv'),index=False)
    summaries.append(summarize('BEAR_INVERSE_3X_VCP_25bps',bear_daily[['date','equity']],exposure=float((bear_daily['open_positions']>0).mean()*100)))
    annual_summary(bear_daily[['date','equity']]).to_csv(os.path.join(OUT_DIR,'annual_BEAR_INVERSE_3X_VCP_25bps.csv'),index=False)
except Exception as e:
    print('bear daily failed',e)

summ=pd.DataFrame(summaries)
# homogenize summary col names from v28 summary
for col in list(summ.columns):
    pass
summ.to_csv(os.path.join(OUT_DIR,'all_strategy_summary_raw.csv'),index=False)
print('top summaries')
print(summ.sort_values('return_pct', ascending=False)[['name','final_equity','net_profit','return_pct','cagr_pct','max_dd_pct','trades','profit_factor','win_rate_pct','exposure_pct']].head(30).to_string(index=False))
