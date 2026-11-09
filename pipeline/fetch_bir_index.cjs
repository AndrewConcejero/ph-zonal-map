// Refresh data/bir_rdo_index.csv from BIR's zonal values page (https://www.bir.gov.ph/zonal-values).
//
// The page is a JavaScript app: each Revenue Region's list (RDO, current Department Order, LGUs, workbook link) is
// loaded when the region is opened. This opens every region in a headless browser, the way a visitor would, and
// keeps the lists the page receives. Needs Playwright with Chromium (npm i -g playwright; npx playwright install chromium).
//
// Usage: node pipeline/fetch_bir_index.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const PAGE = 'https://www.bir.gov.ph/zonal-values';
const OUT = path.join(__dirname, '..', 'data', 'bir_rdo_index.csv');

const strip = (html) => (html || '').replace(/<[^>]+>/g, '\n').replace(/&nbsp;/g, ' ').replace(/&ntilde;/g, 'ñ')
  .replace(/&amp;/g, '&').replace(/&#39;/g, "'").replace(/\s*\n\s*/g, ' | ').replace(/^[ |]+|[ |]+$/g, '');
const csvCell = (v) => /[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const lists = {};
  page.on('response', async (r) => {
    const m = r.url().match(/\/templates\/(\d+)\/datasets/);
    if (m && r.status() === 200) { try { lists[m[1]] = JSON.parse(await r.text()); } catch (e) { /* not JSON */ } }
  });
  await page.goto(PAGE, { waitUntil: 'networkidle', timeout: 120000 });
  const regions = await page.$$eval('*', (els) => [...new Set(els
    .filter((e) => e.children.length === 0 && /^Revenue Region \S+ - /.test((e.textContent || '').trim()))
    .map((e) => e.textContent.trim()))]);
  for (const name of regions) {
    await page.getByText(name, { exact: true }).first().click();
    await page.waitForTimeout(3500);
  }
  await page.waitForTimeout(3000);
  await browser.close();

  const fetched = new Date().toISOString().slice(0, 10);
  const rows = [];
  for (const list of Object.values(lists)) {
    for (const it of list.data || []) {
      const c = it.content || {};
      const files = (c['Files Collection'] || [c.File || '']).map((f) => (f || '').split('|')[0]).filter(Boolean);
      rows.push({
        region: (it.name || '').replace(/^.*?(Revenue Region)/, '$1'),
        rdo_label: (c.RDO || '').trim(),
        province: (c.Province || '').replace('Province:', '').trim(),
        current_do: c['Department Order'] || '',
        workbook_url: files[0] || '',
        lgus_listed: strip(c.Municities),
        fetched,
        source_page: PAGE,
      });
    }
  }
  if (regions.length < 22 || rows.length < 100) {
    console.error(`only ${regions.length} regions / ${rows.length} RDOs loaded; not overwriting ${OUT}`);
    process.exit(1);
  }
  rows.sort((a, b) => a.region.localeCompare(b.region, 'en', { numeric: true }) || a.rdo_label.localeCompare(b.rdo_label, 'en', { numeric: true }));
  const cols = Object.keys(rows[0]);
  fs.writeFileSync(OUT, [cols.join(','), ...rows.map((r) => cols.map((k) => csvCell(String(r[k]))).join(','))].join('\n') + '\n');
  console.log(`${regions.length} regions, ${rows.length} RDOs -> ${OUT}`);
})();
