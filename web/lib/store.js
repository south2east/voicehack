'use strict';

// レシピの保存先 (Vercel Blob, 非公開)。recipes/<id>.json に 1 件 1 ファイルで置く。

const { put, get, list } = require('@vercel/blob');
const { isValidId } = require('./recipe');

const PREFIX = 'recipes/';

function pathname(id) {
  if (!isValidId(id)) throw Object.assign(new Error('invalid recipe id'), { status: 400 });
  return `${PREFIX}${id}.json`;
}

async function save(doc) {
  await put(pathname(doc.id), JSON.stringify(doc), {
    access: 'private',
    contentType: 'application/json',
    addRandomSuffix: false,
    allowOverwrite: true,
  });
  return doc;
}

async function load(id) {
  const res = await get(pathname(id), { access: 'private', useCache: false });
  if (!res) throw Object.assign(new Error('recipe not found'), { status: 404 });
  return JSON.parse(await new Response(res.stream).text());
}

async function listAll() {
  const { blobs } = await list({ prefix: PREFIX });
  const docs = await Promise.all(
    blobs.map((b) => load(b.pathname.slice(PREFIX.length, -'.json'.length)).catch(() => null))
  );
  return docs.filter(Boolean).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

module.exports = { save, load, listAll };
