import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { test } from 'node:test';
import { chromium } from 'playwright';
import { trackAppAssets } from './browser_ready.mjs';

test('route checks finish lazy login assets before navigating away', async () => {
    const server = createServer((request, response) => {
        if (request.url === '/assets/library.js') {
            setTimeout(() => {
                response.writeHead(200, { 'Content-Type': 'text/javascript' });
                response.end('globalThis.libraryLoaded = true;');
            }, 1000);
            return;
        }
        response.writeHead(200, { 'Content-Type': 'text/html' });
        response.end(`<div id="root"><svg width="20" height="20"></svg></div>
            <button onclick="history.pushState({}, '', '/library'); import('/assets/library.js')">Log in</button>`);
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const page = await browser.newPage();
        const failures = [];
        page.on('requestfailed', request => failures.push(request.url()));
        const base = `http://127.0.0.1:${server.address().port}`;
        const waitForAppReady = trackAppAssets(page, new URL(base));
        await page.goto(`${base}/login`, { waitUntil: 'networkidle' });
        const asset = page.waitForRequest('**/assets/library.js');
        await page.getByRole('button', { name: 'Log in' }).click();
        await asset;
        await page.waitForURL('**/library');
        assert.equal(await page.evaluate(() => globalThis.libraryLoaded), undefined,
            'Fixture must expose the unfinished SPA import that URL checks miss');
        await waitForAppReady();
        assert.equal(await page.evaluate(() => globalThis.libraryLoaded), true);
        await page.goto(`${base}/settings`, { waitUntil: 'networkidle' });
        assert.deepEqual(failures, []);
    } finally {
        await browser?.close();
        await new Promise(resolve => server.close(resolve));
    }
});

test('asset settling does not hide genuine HTTP or JavaScript failures', async () => {
    const server = createServer((request, response) => {
        if (request.url === '/assets/missing.js') {
            response.writeHead(404);
            response.end();
            return;
        }
        response.writeHead(200, { 'Content-Type': 'text/html' });
        response.end(`<div id="root"><svg width="20" height="20"></svg></div>
            <script type="module">import('/assets/missing.js');</script>`);
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const page = await browser.newPage();
        const base = new URL(`http://127.0.0.1:${server.address().port}/`);
        const waitForAppReady = trackAppAssets(page, base);
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        page.on('response', response => {
            if (response.status() >= 400) errors.push(`HTTP ${response.status()}`);
        });
        await page.goto(base.href, { waitUntil: 'networkidle' });
        await waitForAppReady();
        assert.ok(errors.includes('HTTP 404'));
        assert.ok(errors.some(message => message.includes('dynamically imported module')));
    } finally {
        await browser?.close();
        await new Promise(resolve => server.close(resolve));
    }
});
