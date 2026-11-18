import { get } from "@vercel/blob";

// 状态由独立只读发布器约30秒推送；不允许CDN将旧状态伪装成实时状态。
export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Cache-Control", "no-store, max-age=0");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") return res.status(405).json({ ok: false, err: "method" });
  try {
    const file = await get("anth-live-status.json", { access: "private", useCache: false });
    if (!file) { return res.status(503).json({ ok: false, err: "暂无数据，机器人还没推送状态" }); }
    let text;
    if (typeof file.text === "function") text = await file.text();
    else if (file.stream) text = await new Response(file.stream).text();
    else if (file.body) text = await new Response(file.body).text();
    else text = await new Response(file).text();
    const data = JSON.parse(text);

    return res.status(200).json(data);
  } catch (e) {
    const msg = (e && e.message) || String(e);
    const missing = /not found|404|NoSuchKey|does not exist/i.test(msg);

    return res.status(503).json({ ok: false, err: missing ? "暂无数据，机器人还没推送状态" : "读取状态失败" });
  }
}
