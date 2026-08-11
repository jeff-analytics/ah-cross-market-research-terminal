from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'data'

def read_json(path):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except Exception: return {}

print('='*68)
print(' A/H Terminal data status')
print('='*68)
update=read_json(DATA/'update_log.json')
print('Daily history status :', update.get('status','unknown'))
print('Daily data mode      :', update.get('data_mode','unknown'))
print('Real daily companies :', update.get('real_data_companies',0))
print('Analysis refreshed   :', update.get('analysis_refreshed',False))
print('Daily updated at     :', update.get('updated_at','—'))
try:
    import sqlite3
    con=sqlite3.connect(DATA/'live_monitor.db')
    df=pd.read_sql_query('select source, a_source, h_source, fx_source, quality_state, quality_reason, fetched_at, a_quote_time, h_quote_time, fx_quote_time, quote_skew_seconds, stale_flag from realtime_latest',con)
    print('Realtime rows        :',len(df))
    if len(df):
        src=df['source'].fillna('unknown').value_counts().to_dict()
        print('Realtime sources     :',src)
        quality=df.get('quality_state', '').astype(str) if 'quality_state' in df else pd.Series('', index=df.index)
        online=quality.eq('实时可比') if 'quality_state' in df else df['source'].astype(str).str.contains('tencent_http|eastmoney_http', regex=True)
        indicative=quality.isin(['单边指示','竞价指示'])
        reference=quality.isin(['午间快照','上一收盘','收盘口径','单边收盘参考'])
        print('Synchronized rows    :',int(online.sum()))
        print('Indicative rows      :',int(indicative.sum()))
        print('Reference rows       :',int(reference.sum()))
        print('Non-stale synchronized:',int((online & df['stale_flag'].fillna(1).eq(0)).sum()))
        print('Latest fetch         :',df['fetched_at'].max())
    con.close()
except Exception as exc:
    print('Realtime DB error    :',exc)
status=read_json(DATA/'daily_crawler_status.json')
if status:
    print('Daily crawler        :',status.get('state'),'stage=',status.get('stage','—'))
    if status.get('error'): print('Daily crawler error  :',status['error'])
log=DATA/'live_monitor_console.log'
if log.exists():
    lines=log.read_text(encoding='utf-8',errors='ignore').splitlines()
    if lines:
        print('Live monitor last log:',lines[-1][:400])
print('\nInterpretation:')
print('- 实时可比: both markets are in continuous trading and pass the synchronization/freshness gate.')
print('- 单边指示/竞价指示: calculable reference values, but they are not synchronized live premiums and do not feed live anomaly ranking.')
print('- 午间快照/上一收盘/收盘口径: frozen reference states; no new synchronized market information is implied.')
print('- daily_cache / 暂停计算 are never presented as a valid current A/H indicator.')
