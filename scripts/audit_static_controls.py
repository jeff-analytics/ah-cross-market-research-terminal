from pathlib import Path
from html.parser import HTMLParser
import json,sys

ROOT=Path(__file__).resolve().parents[1]
html=(ROOT/'web/index.html').read_text(encoding='utf-8')
js=(ROOT/'web/assets/app.js').read_text(encoding='utf-8')

class ButtonParser(HTMLParser):
    def __init__(self):
        super().__init__(); self.buttons=[]; self.stack=[]
    def handle_starttag(self,tag,attrs):
        if tag=='button':
            d=dict(attrs); item={'attrs':d,'text':[]}; self.buttons.append(item); self.stack.append(item)
        elif self.stack:
            self.stack.append(None)
    def handle_endtag(self,tag):
        if self.stack:
            if tag=='button':
                while self.stack:
                    x=self.stack.pop()
                    if x is not None: break
            else:
                self.stack.pop()
    def handle_data(self,data):
        for x in reversed(self.stack):
            if x is not None:
                x['text'].append(data.strip()); break

p=ButtonParser(); p.feed(html)
GROUP_ATTRS={
    'data-page':'nav page routing',
    'data-watch-view':'watchlist view routing',
    'data-company-tab':'company research tabs',
    'data-quality-filter':'quality filtering',
    'data-layout':'dashboard layout selection',
    'data-market-scope':'market quote scope routing',
    'data-market-view':'market A/H/A/H-side view routing',
    'data-daily-mode':'daily market chart mode routing',
    'data-refresh-preset':'refresh preset routing',
    'data-close-modal':'modal closing',
    'data-close-drawer':'drawer closing',
}
results=[]
for i,item in enumerate(p.buttons,1):
    a=item['attrs']; bid=a.get('id'); label=' '.join(x for x in item['text'] if x)
    bound=False; mechanism=''
    if bid and bid in js:
        bound=True; mechanism=f'id:{bid}'
    if not bound:
        for attr,desc in GROUP_ATTRS.items():
            if attr in a and attr in js:
                bound=True; mechanism=desc; break
    if not bound and 'data-days' in a and 'data-days' in js:
        bound=True; mechanism='chart range routing'
    if not bound and 'data-mode' in a and 'data-mode' in js:
        bound=True; mechanism='chart mode routing'
    results.append({'index':i,'id':bid or '', 'label':label, 'bound':bound,'mechanism':mechanism})
failed=[x for x in results if not x['bound']]
out={'total_buttons':len(results),'bound_buttons':len(results)-len(failed),'unbound_buttons':len(failed),'results':results}
(ROOT/'STATIC_CONTROL_AUDIT.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(f"STATIC BUTTON BINDING AUDIT: {len(results)-len(failed)}/{len(results)} bound")
if failed:
    for x in failed: print('UNBOUND',x)
    sys.exit(1)
