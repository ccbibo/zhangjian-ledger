"""Build identical routes for the connected app, standalone web page and inline preview."""
from pathlib import Path
import base64
import json
import re
import sys

ROOT = Path(__file__).resolve().parent
DIST = ROOT / 'dist'
core = (DIST / 'core.js').read_text()
workspace = (DIST / 'workspace.js').read_text()
workspace += '\n' + (DIST / 'financial-summary.js').read_text() + '\n' + (DIST / 'market-charts.js').read_text() + '\n' + (DIST / 'market-settings.js').read_text()
demo = (DIST / 'demo.js').read_text()
boot = (DIST / 'bootstrap.js').read_text()
scripts = '\n'.join([core, workspace, demo, boot])
(DIST / 'connected.js').write_text(scripts)
seed = (DIST / 'demo-seed.json').read_text().replace('<', '\\u003c')
(DIST / 'demo-seed.js').write_text('window.LEDGER_DEMO_SEED=' + seed + ';\n')
template = '''<!doctype html><html lang="zh-Hant"><head><base href="/"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="帳見個人財務工作區：帳單匯入、交易、預算、現金流、投資、負債與目標。"><title>帳見 LEDGER｜拼出你的財務全貌</title><link rel="stylesheet" href="/connected.css"><link rel="stylesheet" href="/brick-theme.css"><link rel="stylesheet" href="/workspace.css"><link rel="stylesheet" href="/market-charts.css"></head><body>
<header><div class="brand"><div class="brandmark" aria-hidden="true"><i></i><i></i><i></i><i></i></div><div class="logo">帳見 LEDGER.</div><small class="eyebrow">BUILD YOUR FINANCIAL CLARITY.</small></div><span class="ws-status" id="connection" role="status">正在開啟工作區…</span></header><div class="workspace"><aside><div class="eyebrow">YOUR WORKSPACE</div><nav aria-label="財務工作區" id="navigation"></nav><div class="aside-foot"><p>少一點整理，<br>多一點生活的底氣。</p><div class="version mono">LEDGER / V0.5<br>ONE WORKSPACE. MORE CLARITY.</div></div></aside><main id="content"><div class="empty">正在開啟工作區…</div></main></div><dialog id="dialog" aria-label="工作區對話框"></dialog><input type="file" id="uploadFiles" multiple accept=".pdf,.csv" hidden><div class="toast" hidden role="status" id="notice"></div><script src="/demo-seed.js"></script><script src="/connected.js"></script></body></html>'''
(DIST / 'connected.html').write_text(template)
(DIST / 'home.html').write_text(template)
styles = '\n'.join((DIST / f).read_text() for f in ['connected.css', 'brick-theme.css', 'workspace.css', 'market-charts.css'])
scene = 'data:image/webp;base64,' + base64.b64encode((DIST / 'assets/brick-finance-team-preview.webp').read_bytes()).decode()
standalone = re.sub(r'<link rel="stylesheet"[^>]+>', '', template).replace('<base href="/">', '')
standalone = standalone.replace('</head>', '<style>' + styles + '</style></head>')
standalone = standalone.replace('<script src="/demo-seed.js"></script><script src="/connected.js"></script>',
    '<script>window.LEDGER_DEMO=true;window.LEDGER_DEMO_SEED=' + seed + ';\n' + scripts.replace('assets/brick-finance-team-preview.webp', scene) + '</script>')
(DIST / 'index.html').write_text(standalone)

# This response-only preview uses exactly the maintained routes and browser driver;
# no backend fetch function is included. It is not saved in the Library package.
root_id = 'ledger-complete-workspace'
inline_core = re.sub(r'async function api\(url,data\)\{.*?\nfunction notice',
                     'async function api(url,data){return demoAPI(url,data)}\nfunction notice', core, count=1, flags=re.S)
inline_core = inline_core.replace('const el=s=>document.querySelector(s)',
                                  "const appRoot=document.getElementById('" + root_id + "'),el=s=>appRoot.querySelector(s)")
inline_scripts = '\n'.join([inline_core, workspace, demo, boot]).replace('document.querySelectorAll(', 'appRoot.querySelectorAll(').replace("document.body.classList.toggle(", "appRoot.classList.toggle(")
inline_scripts = inline_scripts.replace('assets/brick-finance-team-preview.webp', scene)
body = template.split('<body>')[1].split('<script src=')[0]
inline_style = styles.replace(':root', ':scope').replace('body.dark', ':scope.dark').replace('body', ':scope')
fragment = '<style>@scope (#' + root_id + ') {\n' + inline_style + '\n.workspace{min-height:0}.toast{position:sticky;bottom:0;left:0;transform:none}aside .aside-foot{padding-top:35px}\n}</style>\n'
fragment += '<div id="' + root_id + '">' + body + '</div>\n<script>window.LEDGER_DEMO=true;window.LEDGER_MEMORY_ONLY=true;window.LEDGER_DEMO_SEED=' + seed + ';\n' + inline_scripts + '</script>\n'
if len(fragment.encode()) >= 1024 * 1024 or 'fetch(' in fragment or 'XMLHttpRequest' in fragment:
    raise RuntimeError('Inline size or network contract failed')
preview = ROOT / 'preview' / 'ledger-complete.html'
if '--no-preview' not in sys.argv:
    preview.parent.mkdir(parents=True, exist_ok=True)
    preview.write_text(fragment)
print(json.dumps({'web_page': str(DIST / 'index.html'), 'web_bytes': len(standalone.encode()), 'preview_bytes': None if '--no-preview' in sys.argv else len(fragment.encode()), 'routes': 14}))
