from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("AH_ON_DEMAND_HISTORY_SYNC", "0")

import server
from src.live_monitor import LiveMonitor

# Seed the live store from bundled daily data without a network request.
LiveMonitor()
focus = server.bootstrap()["focus_company_id"]
market = server.market_quotes(limit=250)
market_focus = market["rows"][0]["company_id"] if market["rows"] else focus
responses = {
    "/api/bootstrap": server.bootstrap(),
    "/api/summary": server.summary(),
    "/api/screener": server.screener(quality="可分析", limit=250),
    "/api/watchlist": server.get_watchlist(),
    "/api/data-quality": server.data_quality(),
    "/api/live/status": server.live_status(),
    "/api/live/refresh-policy": server.get_refresh_policy_api(),
    "/api/market/quotes": market,
    f"/api/market/{market_focus}/daily": server.market_daily(market_focus, days=0),
    f"/api/company/{focus}": server.company_detail(focus),
    f"/api/company/{focus}/history": server.company_history(focus, days=780),
    f"/api/company/{focus}/analogs": server.company_analogs(focus),
    "/api/health": server.health(),
    "/api/universe/status": server.api_universe_status(),
    "/api/companies/search": server.company_search(q="", limit=20),
}

html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
css = (ROOT / "web" / "assets" / "app.css").read_text(encoding="utf-8")
js = (ROOT / "web" / "assets" / "app.js").read_text(encoding="utf-8")
html = re.sub(r'<link rel="stylesheet" href="/assets/app\.css(?:\?v=[^"]+)?">', f"<style>\n{css}\n</style>", html)
html = re.sub(r'<script src="/assets/app\.js(?:\?v=[^"]+)?"></script>', "", html)
mock = f"""
<script>
const __PREVIEW_DATA__ = {json.dumps(responses, ensure_ascii=False)};
const __MARKET_FOCUS__ = {json.dumps(market_focus)};
window.fetch = async function(url, options={{}}) {{
  const raw = typeof url === 'string' ? url : url.url;
  const parsed = new URL(raw, 'http://preview.local');
  let key = parsed.pathname;
  const method = (options.method || 'GET').toUpperCase();
  if (key.startsWith('/api/screener')) key = '/api/screener';
  if (key.startsWith('/api/companies/search')) key = '/api/companies/search';
  if (key.startsWith('/api/market/quotes')) key = '/api/market/quotes';
  if (key.startsWith('/api/market/') && key.endsWith('/daily')) {{
    key = '/api/market/' + __MARKET_FOCUS__ + '/daily';
    const base = __PREVIEW_DATA__[key];
    const rawDays = parseInt(parsed.searchParams.get('days') || '260', 10);
    const days = rawDays === 0 ? 0 : Math.max(5, Math.min(5000, rawDays));
    if (base) {{
      const rows = days === 0 ? (base.rows || []) : (base.rows || []).slice(-days);
      const data = {{...base, rows, count: rows.length, from: rows[0]?.date || null, to: rows[rows.length-1]?.date || null, range: days === 0 ? 'all' : 'window', requested_days: days === 0 ? null : days}};
      return new Response(JSON.stringify(data), {{status:200, headers:{{'Content-Type':'application/json'}}}});
    }}
  }}
  if (key === '/api/market/crawl-now' && method === 'POST') return new Response(JSON.stringify({{started:true,message:'预览模式：已模拟启动行情抓取'}}), {{status:200, headers:{{'Content-Type':'application/json'}}}});
  if (key === '/api/universe/sync' && method === 'POST') return new Response(JSON.stringify({{synced:true,status:__PREVIEW_DATA__['/api/universe/status'],message:'预览模式：公司池保持 '+(__PREVIEW_DATA__['/api/universe/status']?.active_count??'当前')+' 家'}}), {{status:200, headers:{{'Content-Type':'application/json'}}}});
  if (key === '/api/live/refresh-policy' && method === 'POST') {{
    const body = JSON.parse(options.body || '{{}}');
    const market = {{...__PREVIEW_DATA__['/api/bootstrap'].market, watchlist_seconds: body.watchlist_seconds||5, priority_seconds:body.priority_seconds||15, universe_seconds:body.universe_seconds||60, status_seconds:body.status_seconds||30, custom_refresh_enabled:!!body.enabled}};
    __PREVIEW_DATA__['/api/live/refresh-policy'] = body;
    __PREVIEW_DATA__['/api/bootstrap'].market = market;
    __PREVIEW_DATA__['/api/live/status'].market = market;
    __PREVIEW_DATA__['/api/market/quotes'].market = market;
    return new Response(JSON.stringify({{saved:true,policy:body,market}}), {{status:200,headers:{{'Content-Type':'application/json'}}}});
  }}
  if (key.endsWith('/history')) key = {json.dumps(f'/api/company/{focus}/history')};
  if (key.endsWith('/analogs')) key = {json.dumps(f'/api/company/{focus}/analogs')};
  if (key.startsWith('/api/company/') && !key.endsWith('/history') && !key.endsWith('/analogs') && !key.endsWith('/report')) key = {json.dumps(f'/api/company/{focus}')};
  if (key === '/api/watchlist' && method === 'POST') {{
    const body = JSON.parse(options.body || '{{"company_ids":[]}}');
    __PREVIEW_DATA__['/api/watchlist'].company_ids = body.company_ids;
    __PREVIEW_DATA__['/api/watchlist'].mode = 'custom';
    __PREVIEW_DATA__['/api/watchlist'].selection_basis = '用户自定义';
    return new Response(JSON.stringify({{company_ids: body.company_ids, saved: true, mode:'custom'}}), {{status:200, headers:{{'Content-Type':'application/json'}}}});
  }}
  if (key.endsWith('/report')) return new Response('# Preview research card', {{status:200,headers:{{'Content-Type':'text/markdown'}}}});
  const data = __PREVIEW_DATA__[key];
  if (data !== undefined) return new Response(JSON.stringify(data), {{status:200, headers:{{'Content-Type':'application/json'}}}});
  return new Response(JSON.stringify({{detail:'Preview route unavailable: '+key}}), {{status:404, headers:{{'Content-Type':'application/json'}}}});
}};
</script>
<script>
{js}
</script>
"""
html = html.replace("</body>", mock + "\n</body>")
(ROOT / "PREVIEW.html").write_text(html, encoding="utf-8")
print(ROOT / "PREVIEW.html")
