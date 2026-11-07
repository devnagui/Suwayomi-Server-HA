import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { once } from 'node:events';
import { test } from 'node:test';
import { chromium } from 'playwright';
import { trackAppAssets, trackAppNavigation } from './browser_ready.mjs';

test('only navigation-cancelled same-origin GraphQL POSTs are classified as expected', async () => {
    const listeners = new Map();
    const page = {
        mainFrame: () => 'main-frame',
        on: (event, listener) => listeners.set(event, listener),
        async goto() {
            for (const request of requests) listeners.get('requestfailed')(request);
            return 'navigated';
        },
    };
    const request = (url, method = 'POST', errorText = 'net::ERR_ABORTED') => ({
        url: () => url, method: () => method, failure: () => ({ errorText }),
    });
    const base = new URL('http://localhost/api/hassio_ingress/test/');
    const requests = [
        request(new URL('api/graphql', base).href),
        request(new URL('assets/missing.js', base).href),
        request('http://elsewhere/api/graphql'),
        request(new URL('api/graphql', base).href, 'GET'),
        request(new URL('api/graphql', base).href, 'POST', 'net::ERR_CONNECTION_RESET'),
    ];
    const navigation = trackAppNavigation(page, base);
    for (const request of requests) listeners.get('request')(request);
    assert.equal(await navigation.goto(base.href), 'navigated');
    assert.deepEqual(requests.map(navigation.isNavigationAbort), [true, false, false, false, false]);
    const idleFailure = request(new URL('api/graphql', base).href);
    listeners.get('requestfailed')(idleFailure);
    assert.equal(navigation.isNavigationAbort(idleFailure), false);
    page.goto = async () => { throw new Error('Navigation failed'); };
    await assert.rejects(navigation.goto(base.href), /Navigation failed/);
    const failureAfterNavigation = request(new URL('api/graphql', base).href);
    listeners.get('requestfailed')(failureAfterNavigation);
    assert.equal(navigation.isNavigationAbort(failureAfterNavigation), false);
    const newDocumentFailure = request(new URL('api/graphql', base).href);
    page.goto = async () => {
        listeners.get('framenavigated')(page.mainFrame());
        listeners.get('request')(newDocumentFailure);
        listeners.get('requestfailed')(newDocumentFailure);
    };
    await navigation.goto(base.href);
    assert.equal(navigation.isNavigationAbort(newDocumentFailure), false,
        'A new document failure must not be mistaken for a cancelled old request');
    const lateFailure = request(new URL('api/graphql', base).href);
    listeners.get('request')(lateFailure);
    page.goto = async () => listeners.get('framenavigated')(page.mainFrame());
    await navigation.goto(base.href);
    listeners.get('requestfailed')(lateFailure);
    assert.equal(navigation.isNavigationAbort(lateFailure), true,
        'An old document cancellation may be reported after DOM navigation completes');
});

test('reload cancels background GraphQL without hiding assets or idle request failures', async () => {
    const pendingResponses = new Set();
    const server = createServer((request, response) => {
        if (request.url === '/api/hassio_ingress/test/api/graphql') {
            pendingResponses.add(response);
            response.on('close', () => pendingResponses.delete(response));
            return;
        }
        response.writeHead(200, { 'Content-Type': 'text/html' });
        response.end('<div id="root"><svg width="20" height="20"></svg></div>');
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const page = await browser.newPage();
        const base = new URL(`http://127.0.0.1:${server.address().port}/api/hassio_ingress/test/`);
        const navigation = trackAppNavigation(page, base);
        const failures = [];
        page.on('requestfailed', request => failures.push({
            expected: navigation.isNavigationAbort(request), url: request.url(),
        }));
        await navigation.goto(base.href);
        const started = page.waitForRequest('**/api/graphql');
        await page.evaluate(() => {
            globalThis.controller = new AbortController();
            fetch('api/graphql', { method: 'POST', signal: controller.signal }).catch(() => {});
        });
        await started;
        const cancelled = page.waitForEvent('requestfailed');
        await navigation.goto(base.href);
        await cancelled;
        assert.ok(failures.some(failure => failure.expected), 'Fixture must exercise a real navigation abort');
        const idleRequest = page.waitForRequest('**/api/graphql');
        await page.evaluate(() => {
            globalThis.controller = new AbortController();
            fetch('api/graphql', { method: 'POST', signal: controller.signal }).catch(() => {});
        });
        await idleRequest;
        const idleCancelled = page.waitForEvent('requestfailed');
        await page.evaluate(() => controller.abort());
        await idleCancelled;
        assert.equal(failures.at(-1).expected, false, 'Idle API failures must still fail the gate');
    } finally {
        await browser?.close();
        for (const response of pendingResponses) response.destroy();
        await new Promise(resolve => server.close(resolve));
    }
});

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

test('app readiness is independent of background GraphQL network idleness', async () => {
    const pendingResponses = new Set();
    const server = createServer((request, response) => {
        if (request.url === '/api/graphql') {
            pendingResponses.add(response);
            response.on('close', () => pendingResponses.delete(response));
            return;
        }
        response.writeHead(200, { 'Content-Type': 'text/html' });
        response.end('<div id="root"><svg width="20" height="20"></svg></div><script>fetch("/api/graphql", {method:"POST"}).catch(() => {});</script>');
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const page = await browser.newPage();
        const base = new URL(`http://127.0.0.1:${server.address().port}/`);
        const waitForAppReady = trackAppAssets(page, base);
        const navigation = trackAppNavigation(page, base);
        const started = page.waitForRequest('**/api/graphql');
        await navigation.goto(base.href);
        await started;
        await assert.rejects(page.waitForLoadState('networkidle', { timeout: 200 }), /Timeout/);
        await waitForAppReady();
        assert.equal(pendingResponses.size, 1, 'Fixture must retain actual background API activity');
    } finally {
        await browser?.close();
        for (const response of pendingResponses) response.destroy();
        await new Promise(resolve => server.close(resolve));
    }
});

test('thumbnail blob fetches settle before navigating away', async () => {
    const server = createServer((request, response) => {
        if (request.url.endsWith('/api/v1/manga/1/thumbnail')) {
            setTimeout(() => {
                response.writeHead(200, { 'Content-Type': 'image/png' });
                response.end(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8WsAAAAASUVORK5CYII=', 'base64'));
            }, 1000);
            return;
        }
        response.writeHead(200, { 'Content-Type': 'text/html' });
        response.end('<div id="root"><svg width="20" height="20"></svg></div><script>fetch("api/v1/manga/1/thumbnail").then(response => response.blob()).then(() => globalThis.thumbnailLoaded = true);</script>');
    });
    server.listen(0, '127.0.0.1');
    await once(server, 'listening');
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const page = await browser.newPage();
        const base = new URL(`http://127.0.0.1:${server.address().port}/api/hassio_ingress/test/`);
        const navigation = trackAppNavigation(page, base);
        const waitForAppReady = trackAppAssets(page, base);
        const started = page.waitForRequest('**/thumbnail');
        await navigation.goto(base.href);
        await started;
        assert.equal(await page.evaluate(() => globalThis.thumbnailLoaded), undefined);
        await waitForAppReady();
        assert.equal(await page.evaluate(() => globalThis.thumbnailLoaded), true);
    } finally {
        await browser?.close();
        await new Promise(resolve => server.close(resolve));
    }
});
