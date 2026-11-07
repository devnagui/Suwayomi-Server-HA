import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { trackAppAssets, trackAppNavigation, trackQueryReplacements } from './browser_ready.mjs';

const browser = await chromium.launch({ headless: true });
const expectAuthFailure = process.argv.includes('--expect-ingress-auth-failure');
try {
    for (const [index, address] of process.argv.slice(2).filter(value => !value.startsWith('--')).entries()) {
        const base = new URL(address);
        const context = await browser.newContext({
            locale: 'en-US',
        });
        const page = await context.newPage();
        const navigation = trackAppNavigation(page, base);
        const isReplacedQuery = trackQueryReplacements(page, base, navigation.documentId);
        const waitForAppReady = trackAppAssets(page, base);
        assert.equal(await page.evaluate(() => navigator.language), 'en-US');
        if (expectAuthFailure) {
            const messages = [];
            page.on('console', message => messages.push(message.text()));
            const denied = page.waitForResponse(response => response.url().endsWith('/__ha_auth/session') && response.status() === 503);
            await navigation.goto(base.href, { waitUntil: 'domcontentloaded' });
            await denied;
            await page.locator('#ha-ingress-auth-failure').waitFor({ state: 'visible' });
            assert.match(await page.locator('#ha-ingress-auth-failure').innerText(), /authentication failed.*redacted app logs/i);
            assert.ok(messages.some(message => message.includes('HA ingress token refresh failed')));
            assert.equal(await page.locator('a[href$="/library"]').count(), 0, 'Failed broker session rendered an authenticated library');
            console.log('PASS: HA iframe frontend denies failed native refresh and logs an explicit redacted authentication failure');
            await context.close();
            continue;
        }
        await navigation.goto(base.href);
        let session;
        if (index === 1) {
            await page.getByLabel(/^Username$/i).fill(process.env.SUWAYOMI_TEST_USERNAME);
            await page.getByLabel(/^Password$/i).fill('wrong-password');
            let loginResponse = page.waitForResponse(response => response.url().endsWith('/api/graphql') &&
                response.request().postData()?.includes('login'));
            await page.getByRole('button', { name: /^Log in$/i }).click();
            assert.ok((await (await loginResponse).json()).errors, 'Wrong password was accepted');
            await page.getByLabel(/^Password$/i).fill(process.env.SUWAYOMI_TEST_PASSWORD);
            loginResponse = page.waitForResponse(response => response.url().endsWith('/api/graphql') &&
                response.request().postData()?.includes('login'));
            await page.getByRole('button', { name: /^Log in$/i }).click();
            session = (await (await loginResponse).json()).data.login;
            assert.ok(session.accessToken && session.refreshToken);
        } else {
            assert.equal(await page.getByLabel(/^Username$/i).count(), 0, 'HA administrator was asked to log in again');
            session = await page.evaluate(async () => {
                const response = await fetch(new URL('__ha_auth/session', document.baseURI), {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
                });
                if (!response.ok) throw new Error('Ingress broker unavailable');
                return (await response.json()).data.refreshToken;
            });
            assert.ok(session.accessToken);
        }
        await page.waitForURL(url => !url.pathname.includes('/login'), { timeout: 30000 });
        await page.getByLabel(/^Username$/i).waitFor({ state: 'hidden' });
        await waitForAppReady();
        const errors = [];
        const consoleErrors = [];
        const externalFonts = new Set();
        const assets = new Set();
        const nativeRefreshes = [];
        const cancelledQueries = [];
        const assertNoBrowserErrors = async message => {
            for (const request of cancelledQueries.splice(0)) {
                if (!await isReplacedQuery(request)) {
                    const url = new URL(request.url());
                    const body = request.postData() || '';
                    const operation = /"operationName"\s*:\s*"([A-Za-z_][A-Za-z_0-9]*)"/.exec(body)?.[1] ||
                        /\b(?:query|mutation)\s+([A-Za-z_][A-Za-z_0-9]*)/.exec(body)?.[1] || 'unnamed';
                    errors.push(`${url.hostname}${url.pathname} (${operation}): ${request.failure()?.errorText}`);
                }
            }
            assert.deepEqual(errors, [], message);
        };
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
            if (navigation.isNavigationAbort(request)) return;
            if (url.origin === base.origin && url.pathname === base.pathname + 'api/graphql' &&
                request.method() === 'POST' && request.failure()?.errorText === 'net::ERR_ABORTED') {
                cancelledQueries.push(request);
                return;
            }
            if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
                externalFonts.add(`${url.hostname}: ${request.failure()?.errorText}`);
            } else {
                errors.push(`${url.hostname}${url.pathname}: ${request.failure()?.errorText}`);
            }
        });
        page.on('response', async response => {
            const url = new URL(response.url());
            if (url.origin !== base.origin) return;
            if (response.status() >= 400) errors.push(`HTTP ${response.status()} ${url.pathname}`);
            if (/\.(js|css|woff2?|svg|png)$/.test(url.pathname)) assets.add(url.pathname);
            if (url.pathname.endsWith('/__ha_auth/session') ||
                (url.pathname.endsWith('/api/graphql') && response.request().postData()?.includes('refreshToken'))) {
                let result;
                try {
                    result = await response.json();
                } catch {
                    if (!navigation.isNavigationAbort(response.request())) {
                        errors.push(`Session response could not be decoded: ${url.pathname}`);
                    }
                    return;
                }
                if (result.data?.refreshToken?.accessToken) nativeRefreshes.push(result.data.refreshToken.accessToken);
            }
        });
        for (const route of ['', 'library', 'browse', 'downloads', 'updates', 'settings']) {
            await navigation.goto(new URL(route, base).href);
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
            if (route === 'library') {
                await page.getByText('HA authentication fixture', { exact: true }).first().waitFor({ state: 'visible' });
            }
            const result = await page.evaluate(async token => {
                const endpoint = new URL('api/graphql', document.baseURI);
                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                    body: JSON.stringify({ query: '{ user { id } }' }),
                });
                return { status: response.status, body: await response.json() };
            }, session.accessToken);
            assert.deepEqual(result, { status: 200, body: { data: { user: { id: 1 } } } });
            await waitForAppReady();
        }
        assert.ok(assets.size >= 10, 'Too few real browser assets were exercised');
        assert.ok(nativeRefreshes.length >= 6, 'Native WebUI did not refresh its session on route reloads');
        session.accessToken = nativeRefreshes.at(-1);
        await assertNoBrowserErrors('Browser, HTTP or asset failures');
        const subscribe = token => page.evaluate(token => new Promise((resolve, reject) => {
            const endpoint = new URL('api/graphql', document.baseURI);
            endpoint.protocol = endpoint.protocol === 'https:' ? 'wss:' : 'ws:';
            if (token) endpoint.searchParams.set('token', token);
            const socket = new WebSocket(endpoint, 'graphql-transport-ws');
            const timeout = setTimeout(() => {
                socket.close();
                reject(new Error('Browser authenticated GraphQL subscription did not respond'));
            }, 10000);
            socket.onopen = () => socket.send(JSON.stringify({ type: 'connection_init' }));
            socket.onmessage = event => {
                const message = JSON.parse(event.data);
                if (message.type === 'connection_ack') {
                    socket.send(JSON.stringify({
                        id: 'auth-check', type: 'subscribe',
                        payload: { query: 'subscription { webUIUpdateStatusChange { state } }' },
                    }));
                } else if (message.type === 'next' || message.type === 'error') {
                    clearTimeout(timeout);
                    socket.close();
                    resolve(message);
                }
            };
            socket.onerror = () => {
                clearTimeout(timeout);
                reject(new Error('Browser GraphQL WebSocket failed'));
            };
        }), token);
        const authenticated = await subscribe(session.accessToken);
        assert.equal(authenticated.type, 'next');
        assert.ok(authenticated.payload.data.webUIUpdateStatusChange.state);
        const anonymous = await subscribe(null);
        assert.ok(anonymous.type === 'error' || anonymous.payload?.errors, 'Anonymous WebSocket received protected subscription data');
        await navigation.goto(base.href);
        await waitForAppReady();
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
            const host = await context.newPage();
            host.on('pageerror', error => errors.push(error.message));
            host.on('response', response => {
                const url = new URL(response.url());
                if (url.origin === base.origin && response.status() >= 400) errors.push(`Iframe HTTP ${response.status()} ${url.pathname}`);
            });
            const iframe = host.frameLocator('iframe');
            const waitForIframeReady = trackAppAssets(host, base, iframe.locator('#root svg:visible').first());
            await host.goto(base.origin + '/', { waitUntil: 'domcontentloaded' });
            await iframe.locator('#root svg:visible').first().waitFor({ state: 'visible' });
            assert.equal(await iframe.getByLabel(/^Username$/i).count(), 0, 'HA iframe requested a second login');
            assert.equal(await iframe.locator('base').getAttribute('href'), base.pathname);
            const style = await iframe.locator('body').evaluate(element => {
                const computed = getComputedStyle(element);
                return { fontFamily: computed.fontFamily, backgroundColor: computed.backgroundColor };
            });
            assert.ok(style.fontFamily && style.backgroundColor, 'Iframe styles did not load');
            await waitForIframeReady();
            await host.close();
        }
        await assertNoBrowserErrors('Browser, iframe or asset failures');
        const refreshes = await page.evaluate(() => Object.keys(localStorage).filter(key => key.includes('auth-refresh-token')));
        assert.equal(refreshes.length, 1, 'Native UI did not persist its scoped refresh session');
        if (index === 0) {
            assert.equal(await page.evaluate(key => localStorage.getItem(key), refreshes[0]), 'ha-ingress-session',
                'Ingress exposed the private refresh JWT');
        }
        await page.evaluate(keys => keys.forEach(key => localStorage.removeItem(key)), refreshes);
        await context.clearCookies();
        await navigation.goto(base.href);
        if (index === 1) await page.getByLabel(/^Username$/i).waitFor({ state: 'visible' });
        else assert.equal(await page.getByLabel(/^Username$/i).count(), 0, 'Fresh HA session lost automatic login');
        if (index === 1) {
            await page.evaluate(key => localStorage.setItem(key, 'invalid-refresh-token'), refreshes[0]);
            await navigation.goto(base.href);
            await page.getByLabel(/^Username$/i).waitFor({ state: 'visible' });
        }
        if (externalFonts.size) {
            console.warn(`WARNING: upstream optional Google Fonts unavailable: ${[...externalFonts].join(', ')}; app-owned assets remain required`);
        }
        console.log(`PASS: ${index === 0 ? 'HA administrator automatic native JWT login including iframe' : 'direct native UI form login/logout and invalid refresh'}, six routes, ${assets.size} assets, token refresh, icons, API/WebSockets and scoped service worker`);
        await context.close();
    }
} finally {
    await browser.close();
}
