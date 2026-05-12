#!/usr/bin/env node
/**
 * render_share_cards.js
 *
 * Renders share-card PNGs for every profile by running the existing
 * drawShareCard() JavaScript function from index.html in a headless browser.
 *
 * Strategy:
 *   1. Launch headless Chromium via Playwright
 *   2. Load file:///path/to/index.html
 *   3. Wait for all SPA data to finish loading (PROFILES_DATA, BIOGUIDE, etc.)
 *   4. For each profile:
 *      a. Set window.curModal = { person, ptype } so buildShareData() works
 *      b. Build the data object
 *      c. Load the photo into an <img> element (with onload wait)
 *      d. Call drawShareCard(data, imgEl)
 *      e. Read the canvas via canvas.toDataURL('image/png')
 *      f. Decode the base64 and write to assets/share-cards/{slug}.png
 *   5. Close the browser
 *
 * Why this approach:
 *   Uses the SAME code that produces user-downloadable cards. No CSS port,
 *   no second implementation to maintain. The rendered PNG is byte-identical
 *   (modulo browser version drift) to what users download.
 *
 * Requirements:
 *   - Node.js 18+
 *   - npm packages: playwright
 *   - Photos already cached locally (run cache_photos.py first)
 *   - index.html in repo root
 *
 * Usage:
 *   node scripts/render_share_cards.js
 *   node scripts/render_share_cards.js --only "Donald Trump"
 *   node scripts/render_share_cards.js --limit 5
 *   node scripts/render_share_cards.js --output assets/share-cards
 */
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');

// ─── CLI args ───────────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const getArg = (flag, defaultValue) => {
  const i = args.indexOf(flag);
  if (i >= 0 && i + 1 < args.length) return args[i + 1];
  return defaultValue;
};
const hasFlag = (flag) => args.includes(flag);

const REPO_ROOT     = path.resolve(__dirname, '..');
const INDEX_HTML    = path.resolve(REPO_ROOT, 'index.html');
const OUTPUT_DIR    = path.resolve(REPO_ROOT, getArg('--output', 'assets/share-cards'));
const PHOTOS_DIR    = path.resolve(REPO_ROOT, 'assets/photos');
const ONLY_NAME     = getArg('--only', null);
const LIMIT         = parseInt(getArg('--limit', '0'), 10);
const VERBOSE       = hasFlag('--verbose');
const DRY_RUN       = hasFlag('--dry-run');
// SPA URL: by default, we expect a local HTTP server serving the repo root
// at port 8080. The browser blocks file:// fetch() so file:// doesn't work
// for SPAs that load data via fetch(). The workflow starts a python http.server.
const SPA_URL       = getArg('--url', 'http://localhost:8080/');

// ─── Helpers ────────────────────────────────────────────────────────────────
function slugify(name) {
  return (name || '').toLowerCase()
    .replace(/['\u2018\u2019]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
}

function log(...a) { console.log('[render-cards]', ...a); }

async function main() {
  if (!fs.existsSync(INDEX_HTML)) {
    console.error(`ERROR: index.html not found at ${INDEX_HTML}`);
    process.exit(1);
  }

  if (!DRY_RUN) {
    fs.mkdirSync(OUTPUT_DIR, { recursive: true });
  }

  log(`Launching headless Chromium...`);
  const browser = await chromium.launch({ headless: true });
  // Match the share card's 1080x1080 dimension and a bit of headroom
  const page = await browser.newPage({ viewport: { width: 1400, height: 1400 } });

  // Forward browser console messages when verbose
  if (VERBOSE) {
    page.on('console', msg => console.log(`  [browser ${msg.type()}] ${msg.text()}`));
    page.on('pageerror', err => console.error(`  [browser ERROR] ${err.message}`));
  }

  log(`Loading ${SPA_URL} ...`);
  try {
    await page.goto(SPA_URL, { waitUntil: 'load', timeout: 30000 });
  } catch (e) {
    console.error(`ERROR: failed to load ${SPA_URL}: ${e.message}`);
    console.error(`Make sure a local HTTP server is running at that URL.`);
    console.error(`For example: python3 -m http.server 8080`);
    await browser.close();
    process.exit(1);
  }

  // Wait for SPA data to populate. We added window.PROFILES_DATA in the recent
  // index.html updates so this is reliable.
  log('Waiting for SPA data to load...');
  try {
    await page.waitForFunction(
      () => window.PROFILES_DATA && Object.keys(window.PROFILES_DATA).length > 0
            && window.SENATE_DATA && window.HOUSE_DATA && window.COURT_DATA && window.CABINET_DATA,
      { timeout: 30000 }
    );
  } catch (e) {
    console.error('ERROR: SPA data did not load within 30s. Is index.html healthy?');
    await browser.close();
    process.exit(1);
  }
  log('SPA data loaded.');

  // Build the list of all profiles by combining the four people-list files
  const allPeople = await page.evaluate(() => {
    const result = [];
    const addAll = (list, ptype) => {
      const records = Array.isArray(list) ? list : Object.values(list || {});
      for (const rec of records) {
        if (rec && rec.name) result.push({ person: rec, ptype });
      }
    };
    addAll(window.SENATE_DATA,  'congress');
    addAll(window.HOUSE_DATA,   'congress');
    addAll(window.COURT_DATA,   'court');
    addAll(window.CABINET_DATA, 'cabinet');
    return result;
  });

  log(`Found ${allPeople.length} people across senate/house/court/cabinet`);

  // Cabinet allowlist: most cabinet members have no FEC donor history because
  // they were never elected officials, so their cards would render with empty
  // "No major corporate PAC donations on record" text that reads like an
  // endorsement. Trump/Vance/Rubio have prior elected-office FEC histories,
  // so their cards have real data. Must match the same allowlist in index.html
  // around the Share Profile button.
  const CABINET_CARD_ALLOWLIST = new Set(['Donald Trump', 'JD Vance', 'Marco Rubio']);
  const cabinetSkipped = allPeople.filter(
    x => x.ptype === 'cabinet' && !CABINET_CARD_ALLOWLIST.has(x.person.name)
  );
  if (cabinetSkipped.length > 0) {
    log(`Skipping ${cabinetSkipped.length} cabinet members without sufficient FEC data:`);
    for (const { person } of cabinetSkipped) {
      log(`  - ${person.name}`);
    }
  }
  let workList = allPeople.filter(
    x => x.ptype !== 'cabinet' || CABINET_CARD_ALLOWLIST.has(x.person.name)
  );

  // Filter to --only if specified
  if (ONLY_NAME) {
    workList = workList.filter(x => x.person.name === ONLY_NAME);
    if (workList.length === 0) {
      console.error(`ERROR: no person matching --only "${ONLY_NAME}" (or excluded by cabinet allowlist)`);
      await browser.close();
      process.exit(1);
    }
  }
  if (LIMIT > 0) {
    workList = workList.slice(0, LIMIT);
  }

  if (DRY_RUN) {
    for (const { person, ptype } of workList) {
      console.log(`  [dry] would render ${person.name} (${ptype}) -> ${slugify(person.name)}.png`);
    }
    await browser.close();
    return;
  }

  // For each profile: simulate modal open, build data, load photo, draw, save
  let nSuccess = 0;
  let nFailed = 0;
  const failures = [];
  const startTime = Date.now();

  for (let i = 0; i < workList.length; i++) {
    const { person, ptype } = workList[i];
    const slug = slugify(person.name);

    try {
      const dataUrl = await page.evaluate(async ({ person, ptype }) => {
        // Call buildShareData with the explicit person (bypasses curModal which
        // is a script-scope let, not a window property, so we can't set it
        // from page.evaluate).
        const data = window.buildShareData(person);
        if (!data) throw new Error('buildShareData returned null');

        // Determine the photo URL using the same logic as the SPA's avHTML().
        // Over HTTP these resolve correctly against the page origin.
        const overrides = window.WIKI_PHOTO_OVERRIDES || {};
        const bioguide  = window.BIOGUIDE || {};
        const slugify = (n) => (n || '').toLowerCase()
          .replace(/['\u2018\u2019]/g, '')
          .replace(/[^a-z0-9]+/g, '-')
          .replace(/^-|-$/g, '');

        let photoUrl = null;
        if (overrides[person.name]) {
          photoUrl = overrides[person.name];
        } else if (bioguide[person.name]) {
          // After cache_photos.py runs, bioguide-photo'd people live at
          // /assets/photos/{slug}.{ext}, not at the bioguide ID location.
          photoUrl = `/assets/photos/${slugify(person.name)}.jpg`;
        }

        // Load the photo, then call drawShareCard
        const imgEl = await new Promise((resolve, reject) => {
          if (!photoUrl) {
            resolve(null);
            return;
          }
          const img = new Image();
          img.crossOrigin = 'anonymous';
          img.onload = () => resolve(img);
          img.onerror = () => resolve(null); // graceful: fall through to initials
          img.src = photoUrl;
        });

        const canvas = window.drawShareCard(data, imgEl);
        if (!canvas) throw new Error('drawShareCard returned null');
        return canvas.toDataURL('image/png');
      }, { person, ptype });

      // Strip the data URL prefix and decode
      const base64 = dataUrl.replace(/^data:image\/png;base64,/, '');
      const buf = Buffer.from(base64, 'base64');
      const outPath = path.join(OUTPUT_DIR, `${slug}.png`);
      fs.writeFileSync(outPath, buf);
      nSuccess++;
      if (VERBOSE || (i + 1) % 50 === 0 || i + 1 === workList.length) {
        const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
        log(`[${i + 1}/${workList.length}] ${person.name} -> ${slug}.png (${(buf.length / 1024).toFixed(0)}KB, ${elapsed}s elapsed)`);
      }
    } catch (err) {
      nFailed++;
      failures.push({ name: person.name, error: err.message });
      console.error(`  [${i + 1}/${workList.length}] FAIL ${person.name}: ${err.message}`);
    }
  }

  await browser.close();

  log(`\nDone. Success: ${nSuccess}, Failed: ${nFailed}`);
  if (failures.length > 0 && failures.length <= 20) {
    log('Failures:');
    failures.forEach(f => log(`  ${f.name}: ${f.error}`));
  }

  // Cleanup: remove any pre-existing PNGs in the output directory that no
  // longer correspond to a person on the work list. This catches cabinet
  // members removed from the allowlist, people who have left office, etc.
  // Only runs if we processed the full set (not --only or --limit), to avoid
  // deleting cards during a partial render.
  if (!ONLY_NAME && LIMIT === 0) {
    const expectedSlugs = new Set(workList.map(x => slugify(x.person.name)));
    const existing = fs.readdirSync(OUTPUT_DIR).filter(f => f.endsWith('.png'));
    let nDeleted = 0;
    for (const filename of existing) {
      const slug = filename.replace(/\.png$/, '');
      if (!expectedSlugs.has(slug)) {
        try {
          fs.unlinkSync(path.join(OUTPUT_DIR, filename));
          nDeleted++;
          log(`  removed stale card: ${filename}`);
        } catch (err) {
          console.error(`  failed to delete ${filename}: ${err.message}`);
        }
      }
    }
    if (nDeleted > 0) {
      log(`Cleaned up ${nDeleted} stale share-card PNGs`);
    }
  }

  // Exit with error if too many failed
  if (nFailed > nSuccess / 4) {
    console.error(`ERROR: too many failures (${nFailed}/${workList.length})`);
    process.exit(1);
  }
}

main().catch(err => {
  console.error('FATAL:', err);
  process.exit(1);
});
