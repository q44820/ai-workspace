import { get } from "@vercel/blob";

// 状态只在开/平仓或状态变化时写入，所以这里用 CDN 缓存 5 分钟，避免频繁读 Blob。
export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") return res.status(405).json({ ok: false, err: "method" });
  try {
    const file = await get("anth-live-status.json", { access: "private" });
    if (!file) { res.setHeader("Cache-Control", "public, s-maxage=120"); return res.status(503).json({ ok: false, err: "暂无数据，机器人还没推送状态" }); }
    let text;
    if (typeof file.text === "function") text = await file.text();
    else if (file.stream) text = await new Response(file.stream).text();
    else if (file.body) text = await new Response(file.body).text();
    else text = await new Response(file).text();
    const data = JSON.parse(text);
    res.setHeader("Cache-Control", "public, max-age=30, s-maxage=300, stale-while-revalidate=600");
    return res.status(200).json(data);
  } catch (e) {
    const msg = (e && e.message) || String(e);
    const missing = /not found|404|NoSuchKey|does not exist/i.test(msg);
    res.setHeader("Cache-Control", "public, s-maxage=120");
    return res.status(503).json({ ok: false, err: missing ? "暂无数据，机器人还没推送状态" : "读取状态失败" });
  }
}
