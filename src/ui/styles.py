from __future__ import annotations

APP_CSS = r"""
<style>
:root {
  --bg: #08111f;
  --panel: #0f1b2d;
  --panel-2: #132238;
  --line: #22344d;
  --text: #e8eef8;
  --muted: #8ea0ba;
  --cyan: #42d3ff;
  --green: #36d399;
  --amber: #f6c85f;
  --red: #ff6b7a;
}
html, body, [class*="css"] { font-family: Inter, "Microsoft YaHei", sans-serif; }
.stApp { background: radial-gradient(circle at 25% -10%, #173254 0%, #08111f 34%, #07101c 100%); color: var(--text); }
[data-testid="stHeader"] { background: rgba(8,17,31,.82); backdrop-filter: blur(10px); }
[data-testid="stSidebar"] { background: #0a1525; border-right: 1px solid var(--line); }
[data-testid="stSidebar"] * { color: var(--text); }
.block-container { max-width: 1500px; padding-top: 1.35rem; padding-bottom: 3rem; }
.terminal-header { border: 1px solid var(--line); border-radius: 18px; padding: 20px 22px; background: linear-gradient(135deg, rgba(20,39,66,.96), rgba(10,23,40,.96)); box-shadow: 0 16px 44px rgba(0,0,0,.28); margin-bottom: 16px; }
.terminal-header h1 { margin:0; font-size: 27px; letter-spacing: -.4px; }
.terminal-header p { margin: 6px 0 0; color: var(--muted); font-size: 14px; }
.header-row { display:flex; justify-content:space-between; align-items:center; gap:16px; }
.status-pill { display:inline-flex; align-items:center; gap:7px; padding:7px 11px; border:1px solid #2b4964; border-radius:999px; color:#bcecff; background:#10283d; font-size:12px; white-space:nowrap; }
.status-dot { width:8px; height:8px; border-radius:50%; background:var(--green); box-shadow:0 0 12px rgba(54,211,153,.8); }
.kpi-card { background:linear-gradient(180deg, rgba(18,34,57,.96), rgba(12,25,43,.96)); border:1px solid var(--line); border-radius:14px; padding:15px 17px; min-height:100px; box-shadow:0 10px 28px rgba(0,0,0,.18); }
.kpi-label { color:var(--muted); font-size:12px; letter-spacing:.25px; }
.kpi-value { color:var(--text); font-size:26px; font-weight:720; margin-top:8px; }
.kpi-sub { color:#84a2bf; font-size:11px; margin-top:5px; }
.pair-card { border:1px solid var(--line); border-radius:14px; padding:14px 15px; background:linear-gradient(160deg,#12243b,#0d1b2e); min-height:145px; }
.pair-title { display:flex; justify-content:space-between; gap:8px; font-weight:700; font-size:15px; }
.pair-code { color:var(--muted); font-size:11px; margin-top:3px; }
.pair-premium { font-size:25px; font-weight:760; margin-top:14px; }
.pair-grid { display:grid; grid-template-columns:1fr 1fr; gap:7px; margin-top:9px; color:#aebcd0; font-size:11px; }
.badge { display:inline-block; padding:3px 8px; border-radius:999px; font-size:10px; border:1px solid; }
.badge-high { color:#ffd0d5; background:#431d29; border-color:#7a3444; }
.badge-mid { color:#ffe7ab; background:#3a301a; border-color:#665124; }
.badge-low { color:#bcecff; background:#123047; border-color:#24536e; }
.badge-block { color:#e2c9ff; background:#2c2040; border-color:#533b73; }
.insight-panel { background:#0d1a2c; border:1px solid var(--line); border-left:3px solid var(--cyan); border-radius:12px; padding:15px 17px; }
.insight-title { color:#a8dff1; font-size:12px; text-transform:uppercase; letter-spacing:.8px; }
.insight-main { color:var(--text); font-size:16px; font-weight:650; margin-top:7px; }
.insight-sub { color:var(--muted); font-size:12px; line-height:1.65; margin-top:8px; }
.section-label { color:#9cb2cc; font-size:12px; text-transform:uppercase; letter-spacing:1px; margin:7px 0 11px; }
[data-testid="stMetric"] { background:#0f1d31; border:1px solid var(--line); border-radius:12px; padding:10px 13px; }
[data-testid="stMetricLabel"] { color:var(--muted); }
[data-testid="stMetricValue"] { color:var(--text); }
[data-baseweb="tab-list"] { gap:7px; }
[data-baseweb="tab"] { background:#0d1a2c; border:1px solid var(--line); border-radius:10px 10px 0 0; padding:10px 16px; }
[aria-selected="true"][data-baseweb="tab"] { background:#15304b; color:#c6efff; border-bottom-color:#15304b; }
.stDataFrame { border:1px solid var(--line); border-radius:12px; overflow:hidden; }
.stButton > button, .stDownloadButton > button { border-radius:9px; border:1px solid #31506e; background:#132b43; color:#e7f3ff; }
.stButton > button:hover, .stDownloadButton > button:hover { border-color:var(--cyan); color:white; }
hr { border-color:var(--line); }
.small-note { color:var(--muted); font-size:11px; line-height:1.55; }
@media(max-width:900px){ .header-row{align-items:flex-start;flex-direction:column}.pair-card{min-height:auto} }
</style>
"""
