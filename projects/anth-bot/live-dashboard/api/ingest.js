import { put } from "@vercel/blob";

// 独立只读发布器约30秒推送。相同安全状态20秒内合并，不阻挡正常心跳。
let lastWrite = 0;
let lastKey = "";

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") return res.status(405).json({ ok: false, err: "method" });
  const tok = (req.headers.authorization || "").replace(/^Bearer\s+/i, "");
  const expect = process.env.INGEST_TOKEN || "";
  if (!expect || tok !== expect) return res.status(401).json({ ok: false, err: "unauthorized" });
  const body = req.body;
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return res.status(400).json({ ok: false, err: "invalid" });
  }
  const raw = JSON.stringify(body);
  if (raw.length > 1_500_000) return res.status(413).json({ ok: false, err: "too large" });
  const data = body;
  if (!data.mode || !["live", "paper", "stopped"].includes(data.mode)) {
    return res.status(400).json({ ok: false, err: "invalid mode" });
  }
  const p = data.position || {};
  const lt = (data.trades || []).slice(-1)[0] || {};
  const key = JSON.stringify([data.mode, data.dryRun, data.liveEnabled, data.venuesReady, data.stale, data.alert, data.recoveryExposure, data.exit_pending, (data.trades || []).length, lt.t || "", data.position ? 1 : 0, p.t || "", p.dir || ""]);
  const now = Date.now();
  if (key === lastKey && now - lastWrite < 20_000) return res.status(200).json({ ok: true, skipped: true });
  data.ingestedAt = now;
  try {
    await put("anth-live-status.json", JSON.stringify(data), {
      access: "private",
      allowOverwrite: true,
      addRandomSuffix: false,
      contentType: "application/json",
      cacheControlMaxAge: 60,
    });
    lastWrite = now; lastKey = key;
    return res.status(200).json({ ok: true });
  } catch (e) {
    return res.status(502).json({ ok: false, err: "blob write failed" });
  }
}
