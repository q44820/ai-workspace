"""Generate CSP-safe static dashboard from the shared Vercel page; no private configuration."""
from pathlib import Path
import re
HERE=Path(__file__).resolve().parent

def build():
    source=(HERE/'index.html').read_text()
    css_match=re.search(r'<style>(.*?)</style>',source,re.S)
    js_match=re.search(r'<script>(.*?)</script>',source,re.S)
    if css_match is None or js_match is None:raise ValueError('Dashboard source missing CSS or script')
    css=css_match.group(1)
    js=js_match.group(1).replace('fetch("/api/status",','fetch("api/status",')
    source=re.sub(r'\s*<div class="alert" id="migrationNotice">.*?</div>','',source,flags=re.S)
    html=re.sub(r'<style>.*?</style>','<link rel="stylesheet" href="dashboard.css">',source,flags=re.S)
    html=html.replace('https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js','chart.umd.min.js')
    html=re.sub(r'<script>.*?</script>','<script src="dashboard.js"></script>',html,flags=re.S)
    html=html.replace('href="/anth-paper/paper.html#volume"','href="../anth-paper/#volume"')
    html=html.replace('服务器约30秒推送','服务器本地约30秒刷新').replace('定时推送到这块看板','在本机生成快照供看板读取（不使用Vercel Blob）')
    for name,data in [('japan.html',html),('dashboard.css',css),('dashboard.js',js)]:
        (HERE/name).write_text(data)

if __name__=='__main__':build()
