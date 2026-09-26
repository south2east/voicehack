'use strict';

// 一覧 (?なし) と詳細 (?id=<recipeId>) を 1 ページで切り替える
const listEl = document.getElementById('list');
const detailEl = document.getElementById('detail');
const messageEl = document.getElementById('message');

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined && text !== null) e.textContent = text;
  return e;
}

function formatDate(iso) {
  return new Date(iso).toLocaleDateString('ja-JP', { year: 'numeric', month: 'long', day: 'numeric' });
}

function renderList(recipes) {
  if (!recipes.length) {
    messageEl.textContent = 'まだレシピがありません';
    return;
  }
  messageEl.hidden = true;
  listEl.hidden = false;
  for (const r of recipes) {
    const a = el('a', 'recipe-link');
    a.href = `?id=${encodeURIComponent(r.id)}`;
    a.append(el('span', 'recipe-link-title', r.title));
    if (r.summary) a.append(el('span', 'recipe-link-summary', r.summary));
    a.append(el('span', 'recipe-link-date', formatDate(r.finalizedAt)));
    listEl.append(a);
  }
}

function renderDetail(r) {
  document.title = `${r.title} — おふくろの味`;
  messageEl.hidden = true;
  detailEl.hidden = false;

  const back = el('a', 'back', '← レシピ一覧');
  back.href = './';
  detailEl.append(back, el('h1', 'recipe-title', r.title));
  const meta = [r.servings, `${formatDate(r.finalizedAt)} 記録`].filter(Boolean).join(' ・ ');
  detailEl.append(el('p', 'recipe-meta', meta));
  if (r.summary) detailEl.append(el('p', 'recipe-summary', r.summary));

  detailEl.append(el('h2', null, '材料'));
  const ul = el('ul', 'ingredients');
  for (const ing of r.ingredients) {
    const li = el('li');
    const top = el('div', 'ing-row');
    top.append(el('span', 'ing-name', ing.name), el('span', 'ing-estimate', ing.estimate || ''));
    li.append(top);
    // 母の言った言葉をそのまま残す
    if (ing.original) li.append(el('span', 'ing-original', `母いわく「${ing.original}」`));
    if (ing.note) li.append(el('span', 'ing-note', ing.note));
    ul.append(li);
  }
  detailEl.append(ul);

  detailEl.append(el('h2', null, '作り方'));
  const ol = el('ol', 'steps');
  for (const st of r.steps) {
    const li = el('li');
    li.append(el('p', 'step-text', st.text));
    const meta = [st.heat, st.time].filter(Boolean).join(' / ');
    if (meta) li.append(el('span', 'step-meta', meta));
    if (st.tip) li.append(el('span', 'step-tip', st.tip));
    ol.append(li);
  }
  detailEl.append(ol);

  if (r.tips && r.tips.length) {
    detailEl.append(el('h2', null, 'コツ・隠し味'));
    const tl = el('ul', 'tips');
    for (const t of r.tips) tl.append(el('li', null, t));
    detailEl.append(tl);
  }

  renderQuestions(r);
}

// ---- わからないところを聞く ----

function renderQuestionItem(q) {
  const item = el('div', 'qa');
  item.append(el('p', 'qa-q', `Q. ${q.question}`));
  item.append(el('p', 'qa-a', q.aiAnswer));
  if (q.momAnswer) {
    item.append(el('p', 'qa-mom', `お母さんより: ${q.momAnswer}`));
  } else if (q.needsMom) {
    item.append(el('p', 'qa-wait', 'お母さんに聞いています…'));
  }
  return item;
}

function renderQuestions(r) {
  detailEl.append(el('h2', null, 'わからないところを聞く'));
  const list = el('div', 'qa-list');
  for (const q of r.familyQuestions || []) list.append(renderQuestionItem(q));
  detailEl.append(list);

  const form = el('form', 'ask-form');
  const input = el('textarea', 'ask-input');
  input.rows = 2;
  input.placeholder = '例: 「さーーーっと」ってどれくらい？ / 弱めの火ってどのくらい？';
  const button = el('button', 'ask-btn', '聞く');
  button.type = 'submit';
  const status = el('p', 'ask-status');
  form.append(input, button, status);
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    button.disabled = true;
    status.textContent = '考えています…';
    try {
      const res = await fetch(`/api/recipes/${encodeURIComponent(r.id)}/ask`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      list.append(renderQuestionItem(data));
      input.value = '';
      status.textContent = data.needsMom ? 'お母さんにも届けました。答えが来たらここに表示されます' : '';
    } catch (err) {
      status.textContent = `聞けませんでした: ${err.message}`;
    } finally {
      button.disabled = false;
    }
  });
  detailEl.append(form);
}

async function fetchJson(url) {
  const res = await fetch(url, { cache: 'no-store' });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function main() {
  try {
    const id = new URLSearchParams(location.search).get('id');
    if (id) {
      const r = await fetchJson(`/api/recipes/${encodeURIComponent(id)}?public=1`);
      if (r) renderDetail(r);
      else messageEl.textContent = 'レシピが見つかりません';
    } else {
      renderList(await fetchJson('/api/recipes'));
    }
  } catch (err) {
    messageEl.textContent = `読み込めませんでした: ${err.message}`;
  }
}

main();
