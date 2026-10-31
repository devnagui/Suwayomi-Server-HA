import assert from 'node:assert/strict';
import { chromium } from 'playwright';

const browser = await chromium.launch({ headless: true });
try {
    for (const [index, address] of process.argv.slice(2).entries()) {
        const base = new URL(address);
        const credentials = index === 1
            ? Buffer.from(process.env.SUWAYOMI_TEST_AUTH, 'base64').toString('utf8').split(':')
            : null;
        const context = await browser.newContext(credentials
            ? { httpCredentials: { username: credentials[0], password: credentials.slice(1).join(':') } }
            : {});
        const page = await context.newPage();
        const errors = [];
        const consoleErrors = [];
        const externalFonts = new Set();
        const assets = new Set();
        page.on('pageerror', error => errors.push(error.message));
        page.on('console', message => {
            if (message.type() === 'error') consoleErrors.push(message.text());
        });
        page.on('websocket', socket => {
            const url = new URL(socket.url());
            if (!url.pathname.startsWith(base.pathname)) errors.push(`WebSocket escaped ingress: ${url.pathname}`);
        });
        page.on('requestfailed', request => {
            const url = new URL(request.url());
            if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
                externalFonts.add(`${url.hostname}: ${request.failure()?.errorText}`);
            } else {
                errors.push(`${url.hostname}${url.pathname}: ${request.failure()?.errorText}`);
            }
        });
        page.on('response', response => {
            const url = new URL(response.url());
            if (url.origin !== base.origin) return;
            if (response.status() >= 400) errors.push(`HTTP ${response.status()} ${url.pathname}`);
            if (/\.(js|css|woff2?|svg|png)$/.test(url.pathname)) assets.add(url.pathname);
        });
        for (const route of ['', 'library', 'browse', 'downloads', 'updates', 'settings']) {
            await page.goto(new URL(route, base).href, { waitUntil: 'networkidle' });
            try {
                await page.locator('#root').waitFor({ state: 'visible' });
            } catch (error) {
                console.error(JSON.stringify({ route, errors, consoleErrors, appAssets: [...assets] }));
                throw error;
            }
            assert.equal(await page.locator('base').getAttribute('href'), base.pathname);
            const links = await page.locator('a[href]').evaluateAll(elements => elements.map(element => element.href));
            for (const href of links) {
                const target = new URL(href);
                if (target.origin === base.origin) {
                    assert.ok(target.pathname.startsWith(base.pathname), `Link escaped ingress: ${target.pathname}`);
                }
            }
            assert.ok(await page.locator('svg').count() > 0, `No rendered icons on ${route}`);
            const result = await page.evaluate(async () => {
                const endpoint = new URL('api/graphql', document.baseURI);
                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ query: '{ __typename }' }),
                });
                return { status: response.status, body: await response.json() };
            });
            assert.deepEqual(result, { status: 200, body: { data: { __typename: 'Query' } } });
        }
        assert.ok(assets.size >= 10, 'Too few real browser assets were exercised');
        assert.deepEqual(errors, [], 'Browser, HTTP or asset failures');
        await page.evaluate(() => new Promise((resolve, reject) => {
            const endpoint = new URL('api/graphql', document.baseURI);
            endpoint.protocol = endpoint.protocol === 'https:' ? 'wss:' : 'ws:';
            const socket = new WebSocket(endpoint, 'graphql-transport-ws');
            const timeout = setTimeout(() => {
                socket.close();
                reject(new Error('Browser GraphQL WebSocket did not acknowledge'));
            }, 10000);
            socket.onopen = () => socket.send(JSON.stringify({ type: 'connection_init' }));
            socket.onmessage = event => {
                if (JSON.parse(event.data).type === 'connection_ack') {
                    clearTimeout(timeout);
                    socket.close();
                    resolve();
                }
            };
            socket.onerror = () => {
                clearTimeout(timeout);
                reject(new Error('Browser GraphQL WebSocket failed'));
            };
        }));
        await page.goto(base.href, { waitUntil: 'networkidle' });
        const registration = await page.evaluate(async () => {
            const ready = await Promise.race([
                navigator.serviceWorker.ready,
                new Promise((_, reject) => setTimeout(() => reject(new Error('Service worker did not activate')), 10000)),
            ]);
            return { scope: new URL(ready.scope).pathname, script: new URL(ready.active.scriptURL).pathname };
        });
        assert.equal(registration.scope, base.pathname);
        assert.equal(registration.script, base.pathname + 'sw.js');
        if (index === 0) {
            await page.setContent(`<iframe title="HA ingress test" style="width:100%;height:900px;border:0" src="${base.href}"></iframe>`);
            const iframe = page.frameLocator('iframe');
            await iframe.locator('#root svg:visible').first().waitFor({ state: 'visible' });
            assert.equal(await iframe.locator('base').getAttribute('href'), base.pathname);
            const style = await iframe.locator('body').evaluate(element => {
                const computed = getComputedStyle(element);
                return { fontFamily: computed.fontFamily, backgroundColor: computed.backgroundColor };
            });
            assert.ok(style.fontFamily && style.backgroundColor, 'Iframe styles did not load');
        }
        assert.deepEqual(errors, [], 'Browser, iframe or asset failures');
        if (externalFonts.size) {
            console.warn(`WARNING: upstream optional Google Fonts unavailable: ${[...externalFonts].join(', ')}; app-owned assets remain required`);
        }
        console.log(`PASS: ${index === 0 ? 'simulated ingress including iframe' : 'authenticated direct'} browser, six routes, ${assets.size} assets, icons, API/WebSockets and scoped service worker`);
        await context.close();
    }
} finally {
    await browser.close();
}
