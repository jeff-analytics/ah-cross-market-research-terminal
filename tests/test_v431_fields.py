from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
JS=(ROOT/'web/assets/app.js').read_text(encoding='utf-8')
HTML=(ROOT/'web/index.html').read_text(encoding='utf-8')

def test_priority_field_apply_rebuilds_priority_table():
    assert "$('#columnsModal').dataset.context=ctx" in JS
    assert "if(ctx==='priority'){st.priorityCols=c;write(STORE.priorityColumns,c);renderPriority()" in JS
    assert 'id="priorityColumnsButton"' in HTML

def test_screener_field_apply_rebuilds_screener_table():
    assert "else{st.visibleCols=c;write(STORE.columns,c);renderScreenTable()" in JS
    assert 'id="screenColumnsButton"' in HTML

def test_data_mode_badge_prevents_cache_from_looking_live():
    assert 'id="dataModeBadge"' in HTML
    assert "'本地缓存'" in JS
    assert "'实时行情 + 本地历史'" in JS
