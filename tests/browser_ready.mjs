export function trackAppNavigation(page, base) {
    const abortedRequests = new WeakSet();
    const activeRequests = new Set();
    let previousDocumentRequests = new Set();
    let navigating = false;
    page.on('request', request => activeRequests.add(request));
    page.on('requestfinished', request => activeRequests.delete(request));
    page.on('requestfailed', request => {
        activeRequests.delete(request);
        const url = new URL(request.url());
        if (navigating && previousDocumentRequests.has(request) && url.origin === base.origin &&
            url.pathname === base.pathname + 'api/graphql' &&
            request.method() === 'POST' &&
            request.failure()?.errorText === 'net::ERR_ABORTED') {
            abortedRequests.add(request);
        }
    });
    return {
        isNavigationAbort: request => abortedRequests.has(request),
        async goto(address, options = { waitUntil: 'domcontentloaded' }) {
            previousDocumentRequests = new Set(activeRequests);
            navigating = true;
            try {
                return await page.goto(address, options);
            } finally {
                navigating = false;
                previousDocumentRequests.clear();
            }
        },
    };
}

export function trackAppAssets(page, base, ready = page.locator('#root svg:visible').first()) {
    const pending = new Set();
    const listeners = new Set();
    const notify = () => listeners.forEach(listener => listener());
    page.on('request', request => {
        if (new URL(request.url()).origin === base.origin &&
            ['script', 'stylesheet', 'image', 'font'].includes(request.resourceType())) {
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
