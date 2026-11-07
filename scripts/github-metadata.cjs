const MAX_ATTEMPTS = 3;
const REQUEST_TIMEOUT_MS = 20000;
const MAX_RETRY_DELAY_MS = 10000;
const NETWORK_CODES = new Set([
    'ECONNRESET', 'ECONNREFUSED', 'ETIMEDOUT', 'EAI_AGAIN', 'ENOTFOUND',
    'EPIPE', 'UND_ERR_CONNECT_TIMEOUT', 'UND_ERR_HEADERS_TIMEOUT',
    'UND_ERR_BODY_TIMEOUT', 'UND_ERR_SOCKET',
]);

function transientNetworkError(error) {
    if (error?.name === 'AbortError') return false;
    return error?.name === 'TimeoutError' ||
        NETWORK_CODES.has(error?.code) || NETWORK_CODES.has(error?.cause?.code);
}

function retryDelay(headers, attempt, now = Date.now()) {
    const value = headers?.get('retry-after');
    if (value) {
        if (/^\d+$/.test(value.trim())) return Math.min(MAX_RETRY_DELAY_MS, Number(value) * 1000);
        const delay = Date.parse(value) - now;
        if (Number.isFinite(delay)) return Math.min(MAX_RETRY_DELAY_MS, Math.max(0, delay));
    }
    return attempt * 1000;
}

async function githubJson(url, fetcher = fetch, {
    allowNotFound = false,
    sleep = ms => new Promise(resolve => setTimeout(resolve, ms)),
    warn = message => console.warn(message),
} = {}) {
    const token = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
        let response;
        let failure;
        const signal = AbortSignal.timeout(REQUEST_TIMEOUT_MS);
        try {
            response = await fetcher(url, {
                method: 'GET',
                headers: {
                    Accept: 'application/vnd.github+json',
                    ...(token && new URL(url).hostname === 'api.github.com' ? { Authorization: `Bearer ${token}` } : {}),
                },
                signal,
            });
            if (allowNotFound && response.status === 404) {
                try {
                    await response.body?.cancel();
                } catch {
                    // Disposal errors do not change the release's confirmed 404 status.
                    warn(`Unable to dispose upstream HTTP ${response.status} response body; preserving its original status.`);
                }
                return { status: 404, headers: response.headers };
            }
            if (!response.ok) {
                const error = new Error(`Upstream metadata request failed with HTTP ${response.status}`);
                try {
                    await response.body?.cancel();
                } catch {
                    // Classify the HTTP response, not a socket error while disposing its body.
                    warn(`Unable to dispose upstream HTTP ${response.status} response body; preserving its original status.`);
                }
                if (![408, 429].includes(response.status) && !(response.status >= 500 && response.status <= 599)) {
                    throw error;
                }
                failure = error;
            } else {
                return { data: await response.json(), headers: response.headers, status: response.status };
            }
        } catch (error) {
            // Fetch body reads can report AbortError even when our deadline caused the abort.
            const bodyTimeout = error?.name === 'AbortError' && signal.aborted && signal.reason?.name === 'TimeoutError';
            if (!bodyTimeout && !transientNetworkError(error)) throw error;
            failure = bodyTimeout ? signal.reason : error;
        }
        if (attempt === MAX_ATTEMPTS) {
            throw new Error(`Upstream metadata request failed after ${MAX_ATTEMPTS} attempts: ${failure.message}`, { cause: failure });
        }
        const delay = retryDelay(response?.headers, attempt);
        warn(`Transient upstream metadata failure (${failure.message}); retry ${attempt + 1}/${MAX_ATTEMPTS} in ${delay}ms.`);
        await sleep(delay);
    }
}

async function releaseStatus(url, fetcher = fetch, options = {}) {
    const { status, data } = await githubJson(url, fetcher, { ...options, allowNotFound: true });
    if (status !== 404 && data?.tag_name !== decodeURIComponent(new URL(url).pathname.split('/').pop())) {
        throw new Error('Invalid upstream release metadata: tag does not match the requested HA version');
    }
    return status;
}

if (require.main === module) {
    releaseStatus(process.argv[2])
        .then(status => console.log(status))
        .catch(error => {
            console.error(error.message);
            process.exitCode = 1;
        });
}

module.exports = { githubJson, retryDelay, releaseStatus };
