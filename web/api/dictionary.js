'use strict';

const store = require('../lib/store');
const { handle } = require('../lib/http');

module.exports = handle({
  // この家の分量の辞書 (息子の一覧ページに表示する)
  async GET() {
    const { entries = [] } = await store.loadDictionary();
    return entries.map(({ ingredient, expression, original, amount, sustainSec, recipeTitle, count }) =>
      ({ ingredient, expression, original, amount, sustainSec, recipeTitle, count }));
  },
});
