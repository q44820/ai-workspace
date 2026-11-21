"""Serve a privately installed, read-only LP page (no wallet configuration in Git)."""
from pathlib import Path
import base64
import hashlib
from html.parser import HTMLParser

RPC_HOSTS = ('bsc-dataseed.bnbchain.org','bsc-dataseed1.binance.org','bsc-rpc.publicnode.com',
             'bsc-dataseed2.defibit.io','bsc-dataseed1.ninicoin.io','56.rpc.thirdweb.com',
             'public-bsc.nownodes.io','api.geckoterminal.com')

class InlineScripts(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.inside=False
        self.hashes=[]
    def handle_starttag(self, tag, attrs):self.inside=tag=='script'
    def handle_endtag(self, tag):
        if tag=='script':self.inside=False
    def handle_data(self, data):
        if self.inside:self.hashes.append("'sha256-"+base64.b64encode(hashlib.sha256(data.encode()).digest()).decode()+"'")

def serve(handler,head):
    try:body=(Path(__file__).resolve().parent/'private-pages'/'bncb-lp.html').read_bytes()
    except OSError:return handler.response(b'{"error":"lp_page_unavailable"}',503,head=head)
    scripts=InlineScripts();scripts.feed(body.decode('utf-8').replace('\r\n','\n').replace('\r','\n'));scripts.close()
    policy="default-src 'none'; script-src "+' '.join(scripts.hashes)+"; style-src 'unsafe-inline'; connect-src "+' '.join('https://'+h for h in RPC_HOSTS)+"; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    handler.send_response(200)
    for key,val in {'Content-Type':'text/html; charset=utf-8','Content-Length':str(len(body)), 'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Content-Security-Policy':policy}.items():handler.send_header(key,val)
    handler.end_headers()
    if not head:handler.wfile.write(body)
