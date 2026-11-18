// Retired after migration to Japan host: no Blob import, read, or write.
export default async function handler(req, res) {
  res.setHeader("Cache-Control", "no-store");
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") return res.status(405).json({ok:false,err:"method"});
  const token=(req.headers.authorization||"").replace(/^Bearer\s+/i,"");
  if (!process.env.INGEST_TOKEN || token!==process.env.INGEST_TOKEN) return res.status(401).json({ok:false,err:"unauthorized"});
  // Acknowledge retirement without touching storage. Original trade process need not restart.
  return res.status(200).json({ok:true,retired:true,stored:false});
}
