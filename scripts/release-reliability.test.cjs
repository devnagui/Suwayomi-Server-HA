const assert = require('node:assert/strict');
const { test } = require('node:test');
const http = require('node:http');
const { githubJson, retryDelay, releaseStatus } = require('./github-metadata.cjs');
const { compatibleWebUI, serverCommitCount, latestRevision } = require('./update-suwayomi.cjs');

const URL = 'https://api.github.com/repos/example/example/releases/latest';
const quiet = { sleep: async () => {}, warn: () => {} };
const jsonResponse = data => new Response(JSON.stringify(data), {
    status: 200, headers: { 'content-type': 'application/json' },
});

test('metadata GET has a 20-second timeout and returns valid JSON', async t => {
    const timeouts = [];
    t.mock.method(AbortSignal, 'timeout', ms => {
        timeouts.push(ms);
        return new AbortController().signal;
    });
    const result = await githubJson(URL, async (url, options) => {
        assert.equal(url, URL);
        assert.equal(options.method, 'GET');
        assert.ok(options.signal instanceof AbortSignal);
        return jsonResponse({ tag_name: 'r1' });
    });
    assert.deepEqual(result.data, { tag_name: 'r1' });
    assert.equal(result.status, 200);
    assert.deepEqual(timeouts, [20000]);
});

test('408, 429 and 5xx retry only three times, with bounded delays', async () => {
    for (const status of [408, 429, 500, 502, 503, 504, 599]) {
        let calls = 0;
        const delays = [];
        const warnings = [];
        const bodies = [];
        await assert.rejects(githubJson(URL, async () => {
            calls += 1;
            const response = new Response('unavailable', { status });
            bodies.push(response.body);
            return response;
        }, { sleep: async ms => delays.push(ms), warn: text => warnings.push(text) }),
        new RegExp(`after 3 attempts: .*HTTP ${status}`));
        assert.equal(calls, 3);
        assert.deepEqual(delays, [1000, 2000]);
        assert.equal(warnings.length, 2);
        assert.ok(bodies.every(body => body.locked === false));
    }
});

test('connection failures and request timeouts can recover within three attempts', async () => {
    for (const failure of [
        Object.assign(new Error('reset'), { code: 'ECONNRESET' }),
        new TypeError('fetch failed', { cause: Object.assign(new Error('DNS'), { code: 'EAI_AGAIN' }) }),
        new DOMException('request timed out', 'TimeoutError'),
    ]) {
        let calls = 0;
        const result = await githubJson(URL, async () => {
            if (++calls < 3) throw failure;
            return jsonResponse({ ok: true });
        }, quiet);
        assert.equal(calls, 3);
        assert.deepEqual(result.data, { ok: true });
    }
});

test('persistent connection failure is explicit after three attempts', async () => {
    let calls = 0;
    await assert.rejects(githubJson(URL, async () => {
        calls += 1;
        throw Object.assign(new Error('connection reset'), { code: 'ECONNRESET' });
    }, quiet), /after 3 attempts: connection reset/);
    assert.equal(calls, 3);
});

test('auth, other 4xx, TLS, caller cancellation and invalid JSON fail immediately', async () => {
    for (const status of [400, 401, 403, 404, 409, 422]) {
        let calls = 0;
        await assert.rejects(githubJson(URL, async () => {
            calls += 1;
            return new Response('failure', { status });
        }, quiet), new RegExp(`HTTP ${status}`));
        assert.equal(calls, 1);
    }
    for (const failure of [
        new DOMException('cancelled', 'AbortError'),
        Object.assign(new Error('cancelled'), { name: 'AbortError', code: 'ECONNRESET' }),
        new TypeError('fetch failed', { cause: Object.assign(new Error('TLS'), { code: 'CERT_HAS_EXPIRED' }) }),
        new TypeError('invalid request'),
    ]) {
        let calls = 0;
        await assert.rejects(githubJson(URL, async () => {
            calls += 1;
            throw failure;
        }, quiet), error => error === failure);
        assert.equal(calls, 1);
    }
    let calls = 0;
    await assert.rejects(githubJson(URL, async () => {
        calls += 1;
        return new Response('not JSON');
    }, quiet), SyntaxError);
    assert.equal(calls, 1);
});

test('body transport stalls retry, but malformed metadata never retries', async () => {
    let calls = 0;
    await githubJson(URL, async () => {
        calls += 1;
        if (calls === 1) {
            return { ok: true, status: 200, headers: new Headers(),
                json: async () => { throw new DOMException('body stalled', 'TimeoutError'); } };
        }
        return jsonResponse([]);
    }, quiet);
    assert.equal(calls, 2);
    for (const [operation, data, message] of [
        [fetcher => compatibleWebUI(100, fetcher), {}, /Invalid upstream WebUI compatibility mapping/],
        [fetcher => compatibleWebUI(100, fetcher), [], /No compatible WebUI/],
        [fetcher => serverCommitCount('a'.repeat(40), fetcher), [{ sha: 'b'.repeat(40) }], /Cannot verify/],
    ]) {
        let requests = 0;
        await assert.rejects(operation(async () => {
            requests += 1;
            return jsonResponse(data);
        }), message);
        assert.equal(requests, 1);
    }
});

test('real fetch body deadline retries without treating caller cancellation as transient', async t => {
    let requests = 0;
    const server = http.createServer((request, response) => {
        requests += 1;
        response.writeHead(200, { 'content-type': 'application/json' });
        if (requests === 1) response.write('{"stalled":');
        else response.end('{"ok":true}');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    t.after(async () => {
        server.closeAllConnections();
        await new Promise(resolve => server.close(resolve));
    });
    const timeout = AbortSignal.timeout.bind(AbortSignal);
    t.mock.method(AbortSignal, 'timeout', ms => {
        assert.equal(ms, 20000);
        return timeout(100);
    });
    const result = await githubJson(`http://127.0.0.1:${server.address().port}/metadata`, fetch, quiet);
    assert.deepEqual(result.data, { ok: true });
    assert.equal(requests, 2);
});

test('missing release digest remains a hard failure', async () => {
    let calls = 0;
    await assert.rejects(compatibleWebUI(100, async () => {
        calls += 1;
        return jsonResponse(calls === 1 ? [{ uiVersion: 'PREVIEW', serverVersion: 'r100' }] :
            { tag_name: 'r123', assets: [{ name: 'Suwayomi-WebUI-r123.zip' }] });
    }), /missing a verified SHA-256 digest/);
    assert.equal(calls, 2);
});

test('Retry-After seconds and HTTP dates are honored up to ten seconds', async () => {
    const now = Date.parse('2026-11-07T12:00:00Z');
    for (const [value, expected] of [
        ['2', 2000], ['99999', 10000], ['0', 0],
        ['Sat, 07 Nov 2026 12:00:05 GMT', 5000],
        ['Sat, 07 Nov 2026 12:01:00 GMT', 10000],
        ['Sat, 07 Nov 2026 11:59:00 GMT', 0],
        ['invalid', 1000],
    ]) {
        assert.equal(retryDelay(new Headers({ 'retry-after': value }), 1, now), expected);
    }
    let calls = 0;
    const delays = [];
    await githubJson(URL, async () => ++calls === 1 ?
        new Response('', { status: 429, headers: { 'retry-after': '60' } }) : jsonResponse({}),
    { ...quiet, sleep: async ms => delays.push(ms) });
    assert.deepEqual(delays, [10000]);
});

test('only the release existence probe may accept 404, never auth errors', async () => {
    const missing = await githubJson(URL, async () => new Response('', { status: 404 }),
        { ...quiet, allowNotFound: true });
    assert.equal(missing.status, 404);
    assert.equal(missing.data, undefined);
    let calls = 0;
    await assert.rejects(githubJson(URL, async () => {
        calls += 1;
        return new Response('', { status: 403 });
    }, { ...quiet, allowNotFound: true }), /HTTP 403/);
    assert.equal(calls, 1);
});

test('body disposal failures cannot retry auth errors or a confirmed release 404', async () => {
    for (const status of [401, 403, 404]) {
        let calls = 0;
        let cancellations = 0;
        const warnings = [];
        const options = { ...quiet, warn: message => warnings.push(message) };
        const fetcher = async () => {
            calls += 1;
            return {
                ok: false, status, headers: new Headers(),
                body: { cancel: async () => {
                    cancellations += 1;
                    throw Object.assign(new Error('socket reset during disposal'), { code: 'ECONNRESET' });
                } },
            };
        };
        if (status === 404) {
            assert.equal(await releaseStatus(URL, fetcher, options), 404);
        } else {
            await assert.rejects(releaseStatus(URL, fetcher, options), new RegExp(`HTTP ${status}`));
        }
        assert.equal(calls, 1);
        assert.equal(cancellations, 1);
        assert.deepEqual(warnings, [
            `Unable to dispose upstream HTTP ${status} response body; preserving its original status.`,
        ]);
    }
});

test('release existence probe validates the requested tag, without metadata retries', async () => {
    const url = 'https://api.github.com/repos/example/example/releases/tags/v0.1.9';
    assert.equal(await releaseStatus(url, async () => new Response('', { status: 404 }), quiet), 404);
    assert.equal(await releaseStatus(url, async () => jsonResponse({ tag_name: 'v0.1.9' }), quiet), 200);
    for (const data of [{}, { tag_name: 'v0.1.8' }, null]) {
        let calls = 0;
        await assert.rejects(releaseStatus(url, async () => {
            calls += 1;
            return jsonResponse(data);
        }, quiet), /Invalid upstream release metadata/);
        assert.equal(calls, 1);
    }
});

test('readonly ls-remote has a hard 30-second timeout and validates its result', () => {
    const revision = 'a'.repeat(40);
    assert.equal(latestRevision((command, args, options) => {
        assert.equal(command, 'git');
        assert.deepEqual(args, ['ls-remote', 'https://github.com/Suwayomi/Suwayomi-Server.git', 'refs/heads/master']);
        assert.equal(options.timeout, 30000);
        assert.equal(options.killSignal, 'SIGKILL');
        return `${revision}\trefs/heads/master\n`;
    }), revision);
    assert.throws(() => latestRevision(() => { throw Object.assign(new Error('spawn timed out'), { code: 'ETIMEDOUT' }); }),
        /timed out after 30 seconds; package files were not changed/);
    assert.throws(() => latestRevision(() => 'bad metadata'), /Cannot resolve upstream master/);
    const failure = new Error('git authentication failed');
    assert.throws(() => latestRevision(() => { throw failure; }), error => error === failure);
});
