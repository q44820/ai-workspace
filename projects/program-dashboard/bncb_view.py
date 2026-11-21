"""All BNCB bookmarks, including legacy detail URLs, serve one reconciled view."""
from pathlib import Path

def serve(handler,head):
    if not (Path(__file__).resolve().parent/'private-pages'/'bncb-snapshot.json').is_file():
        return handler.response(b'{"error":"lp_snapshot_unavailable"}',503,head=head)
    from bncb_fast import serve as single
    return single(handler,head)
