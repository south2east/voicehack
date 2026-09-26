'use strict';

// Vercel Functions の共通処理: エラーを JSON で返す
function handle(methods) {
  return async (req, res) => {
    const fn = methods[req.method];
    if (!fn) return res.status(405).json({ error: 'method not allowed' });
    try {
      res.status(200).json(await fn(req));
    } catch (err) {
      console.error(err);
      res.status(err.status || 500).json({ error: err.message });
    }
  };
}

async function readRaw(req) {
  const chunks = [];
  for await (const c of req) chunks.push(c);
  return Buffer.concat(chunks);
}

module.exports = { handle, readRaw };
