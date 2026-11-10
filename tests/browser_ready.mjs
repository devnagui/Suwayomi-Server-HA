import { createHash } from 'node:crypto';

function isReadOnlyQuery(source) {
    if (typeof source !== 'string') return false;
    const tokens = source.replace(/"""[\s\S]*?"""|"(?:\\.|[^"\\])*"|#[^\r\n]*/g, ' ')
        .match(/[{}]|[_A-Za-z][_0-9A-Za-z]*/g) || [];
    let depth = 0;
    let definition;
    let query = false;
    for (const token of tokens) {
        if (token === '{') {
            if (depth === 0 && !definition) query = true;
            depth += 1;
        } else if (token === '}') {
            depth -= 1;
            if (depth === 0) definition = undefined;
        } else if (depth === 0 && !definition) {
            if (token === 'mutation' || token === 'subscription') return false;
            if (token === 'query') query = true;
            definition = token;
        }
    }
    return query && depth === 0;
}

export function trackQueryReplacements(page, base, documentId) {
    const requests = new WeakMap();
    const responses = [];
    const subscriptionData = new Set();
    const statusQueries = {
        GET_DOWNLOAD_STATUS: { field: 'downloadStatus', subscription: 'downloadStatusChanged' },
        GET_WEBUI_UPDATE_STATUS: { field: 'getWebUIUpdateStatus', subscription: 'webUIUpdateStatusChange' },
    };
    page.on('websocket', socket => {
        const url = new URL(socket.url());
        url.protocol = url.protocol === 'wss:' ? 'https:' : 'http:';
        if (url.origin !== base.origin || url.pathname !== base.pathname + 'api/graphql') return;
        const document = documentId();
        socket.on('framereceived', frame => {
            let message;
            try {
                message = JSON.parse(frame.payload);
            } catch {
                return;
            }
            if (message.type !== 'next' || message.payload?.errors) return;
            for (const { subscription } of Object.values(statusQueries)) {
                const state = message.payload?.data?.[subscription]?.state;
                if (typeof state === 'string' && /^[A-Z_]+$/.test(state)) {
                    subscriptionData.add(`${document}:${subscription}`);
                }
            }
        });
    });
    page.on('request', request => {
        const url = new URL(request.url());
        if (url.origin !== base.origin || url.pathname !== base.pathname + 'api/graphql' ||
            request.method() !== 'POST') return;
        let body;
        try {
            body = JSON.parse(request.postData() || '{}');
        } catch {
            return;
        }
        if (!isReadOnlyQuery(body.query)) return;
        const signature = createHash('sha256').update(JSON.stringify({
            query: body.query, variables: body.variables || {},
        })).digest('hex');
        const operation = body.operationName || /\bquery\s+([A-Za-z_][A-Za-z_0-9]*)/.exec(body.query)?.[1];
        const status = statusQueries[operation];
        const suppliedBySubscription = status && Object.keys(body.variables || {}).length === 0 &&
            new RegExp(`\\b${status.field}\\s*\\{`).test(body.query) ? status.subscription : undefined;
        requests.set(request, { signature, document: documentId(), suppliedBySubscription });
    });
    page.on('requestfailed', request => {
        const tracked = requests.get(request);
        if (tracked) tracked.aborted = true;
    });
    page.on('response', response => {
        const tracked = requests.get(response.request());
        if (tracked && response.status() === 200) {
            responses.push({ ...tracked, response });
        }
    });
    return async request => {
        const aborted = requests.get(request);
        if (!aborted?.aborted || request.failure()?.errorText !== 'net::ERR_ABORTED') return false;
        if (aborted.suppliedBySubscription &&
            subscriptionData.has(`${aborted.document}:${aborted.suppliedBySubscription}`)) return true;
        for (const replacement of responses) {
            if (replacement.document !== aborted.document || replacement.signature !== aborted.signature) continue;
            let result;
            let timeout;
            try {
                result = await Promise.race([
                    replacement.response.json(),
                    new Promise((_, reject) => {
                        timeout = setTimeout(() => reject(new Error('Query response body did not complete')), 5000);
                    }),
                ]);
            } catch {
                continue;
            } finally {
                clearTimeout(timeout);
            }
            if (result?.data && typeof result.data === 'object' && Object.keys(result.data).length && !result.errors) return true;
        }
        return false;
    };
}

export function trackAppNavigation(page, base) {
    const abortedRequests = new WeakSet();
    const activeRequests = new Set();
    const previousDocumentRequests = new WeakSet();
    let navigationCommitted = false;
    let navigationRequests = new Set();
    let navigating = false;
    let document = 0;
    page.on('request', request => {
        activeRequests.add(request);
        if (navigating && !navigationCommitted) {
            previousDocumentRequests.add(request);
            navigationRequests.add(request);
        }
    });
    page.on('framenavigated', frame => {
        if (navigating && frame === page.mainFrame()) navigationCommitted = true;
    });
    page.on('requestfinished', request => {
        activeRequests.delete(request);
        previousDocumentRequests.delete(request);
    });
    page.on('requestfailed', request => {
        activeRequests.delete(request);
        const url = new URL(request.url());
        const query = url.pathname === base.pathname + 'api/graphql' && request.method() === 'POST';
        const thumbnail = request.method() === 'GET' && url.pathname.startsWith(base.pathname) &&
            /^api\/v1\/manga\/\d+\/thumbnail$/.test(url.pathname.slice(base.pathname.length));
        // A background query/queued image can start after asset readiness, even
        // while goto is awaiting headers. Only the outgoing document may cancel it.
        if (previousDocumentRequests.has(request) && url.origin === base.origin &&
            (query || thumbnail) &&
            request.failure()?.errorText === 'net::ERR_ABORTED') {
            abortedRequests.add(request);
        }
        previousDocumentRequests.delete(request);
    });
    return {
        isNavigationAbort: request => abortedRequests.has(request),
        documentId: () => document,
        async goto(address, options = { waitUntil: 'domcontentloaded' }) {
            document += 1;
            navigationRequests = new Set(activeRequests);
            for (const request of navigationRequests) previousDocumentRequests.add(request);
            navigationCommitted = false;
            navigating = true;
            try {
                return await page.goto(address, options);
            } catch (error) {
                if (!navigationCommitted) {
                    for (const request of navigationRequests) previousDocumentRequests.delete(request);
                }
                throw error;
            } finally {
                navigating = false;
                navigationRequests.clear();
            }
        },
    };
}

export function trackAppAssets(page, base, ready = page.locator('#root svg:visible').first()) {
    const pending = new Set();
    const listeners = new Set();
    const notify = () => listeners.forEach(listener => listener());
    page.on('request', request => {
        const url = new URL(request.url());
        const media = url.pathname.startsWith(base.pathname + 'api/v1/') &&
            /\/(?:thumbnail|page\/\d+)$/.test(url.pathname);
        if (url.origin === base.origin &&
            (['script', 'stylesheet', 'image', 'font'].includes(request.resourceType()) || media)) {
            pending.add(request);
            notify();
        }
    });
    const finished = request => {
        if (pending.delete(request)) notify();
    };
    page.on('requestfinished', finished);
    page.on('requestfailed', finished);
    return async () => {
        await ready.waitFor({ state: 'visible' });
        // SPA redirects can retain an old networkidle load state while importing assets.
        await new Promise((resolve, reject) => {
            let idle;
            const cleanup = () => {
                clearTimeout(idle);
                clearTimeout(deadline);
                listeners.delete(check);
            };
            const check = () => {
                clearTimeout(idle);
                if (!pending.size) idle = setTimeout(() => {
                    cleanup();
                    resolve();
                }, 500);
            };
            const deadline = setTimeout(() => {
                cleanup();
                reject(new Error('App assets did not settle before navigation: ' +
                    [...pending].map(request => new URL(request.url()).pathname).join(', ')));
            }, 30000);
            listeners.add(check);
            check();
        });
    };
}
