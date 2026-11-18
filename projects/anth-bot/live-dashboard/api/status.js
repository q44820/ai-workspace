// Vercel status storage retired. Japanese host reads its authenticated local file.
export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store, max-age=0");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "GET") return res.status(405).json({ok:false,err:"method"});
  return res.status(410).json({ok:false,retired:true,err:"实盘看板已迁至日本服务器，请从整合网站进入",entry:"https://dashboard.foyou448.eu.cc"});
}
