from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')
JS=(ROOT/'web/assets/app.js').read_text(encoding='utf-8')
IDS={'sidebarToggle','commandSearchButton','autoRefreshButton','refreshButton','notificationButton','settingsButton','dashboardLayoutButton','openScreenerButton','watchlistViewButton','addWatchButton','focusStarButton','downloadReportButton','openCompanyButton','priorityViewButton','priorityColumnsButton','newScreenButton','saveScreenButton','exportScreenButton','addFilterButton','screenColumnsButton','clearFiltersButton','screenSearch','screenSelect','bulkAddWatch','bulkOpenFirst','bulkClear','companyStarButton','companyAlertButton','companyReportButton','refreshAnalogsButton','addWatchPageButton','saveWatchViewButton','watchlistSearch','historyCompanyButton','historyRefreshButton','qualityRefreshButton','qualitySearch','sourceRefreshButton','saveSettingsButton','applyFilterButton','resetFilterButton','applyColumnsButton','resetColumnsButton','confirmSaveViewButton','saveAlertButton','removeAlertButton','addWatchSearch','selectCompanySearch'}
def test_controls_exist():
    for x in IDS: assert f'id="{x}"' in HTML,x
def test_controls_bound():
    for x in IDS: assert x in JS,x
def test_group_controls_bound():
    for x in ['data-page','data-watch-view','data-company-tab','data-quality-filter','data-layout','#dashboardRange [data-days]','#companyRange [data-days]','#chartMode [data-mode]','#companyPriceMode [data-mode]','data-close-modal','data-close-drawer']: assert x in JS,x
def test_auto_refresh_real():
    for x in ['watchlist_seconds','priority_seconds','universe_seconds','autoTick','setInterval(autoTick,1000)']: assert x in JS,x
def test_launcher_starts_monitor():
    launcher = (ROOT / 'scripts' / 'start_terminal.py').read_text(encoding='utf-8')
    assert 'ensure_live_monitor.py' in launcher
    assert 'ensure_daily_market_data.py' in launcher
    assert 'uvicorn' in launcher and 'server:app' in launcher
    wrappers = [p for p in [ROOT/'START_TERMINAL.bat', ROOT/'START_TERMINAL.cmd', ROOT/'START_TERMINAL.command'] if p.exists()]
    assert wrappers
    for path in wrappers:
        t=path.read_text(encoding='utf-8-sig')
        assert 'scripts/start_terminal.py' in t or 'scripts\\start_terminal.py' in t
