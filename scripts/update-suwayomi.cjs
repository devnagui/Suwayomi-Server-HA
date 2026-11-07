const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { githubJson } = require('./github-metadata.cjs');

const defaultApp = path.resolve(__dirname, '..', 'suwayomi');

function currentWebUI(app) {
    const content = fs.readFileSync(path.join(app, 'build.yaml'), 'utf8');
    const revision = /WEBUI_REF: "(r\d+)"/.exec(content);
    const sha256 = /WEBUI_SHA256: "([0-9a-f]{64})"/.exec(content);
    const minimum = /WEBUI_MIN_SERVER: "(\d+)"/.exec(content);
    if (!revision || !sha256 || !minimum) throw new Error('Cannot resolve the packaged WebUI pins');
    return { revision: revision[1], sha256: sha256[1], minimumServer: Number(minimum[1]) };
}

function pin(version, revision, app = defaultApp, webui) {
    if (!/^\d+\.\d+\.\d+$/.test(version || '')) {
        throw new Error('App version must use major.minor.patch');
    }
    if (!/^[0-9a-f]{40}$/.test(revision)) throw new Error('Cannot resolve the upstream server revision');
    const files = ['build.yaml', 'Dockerfile', 'config.yaml'];
    const contents = Object.fromEntries(files.map(name => [name, fs.readFileSync(path.join(app, name), 'utf8')]));
    const current = /^version: "(\d+\.\d+\.\d+)"$/m.exec(contents['config.yaml']);
    if (!current) throw new Error('Cannot resolve the current app version');
    const oldParts = current[1].split('.').map(Number);
    const newParts = version.split('.').map(Number);
    const difference = newParts.map((value, index) => value - oldParts[index]).find(value => value !== 0);
    if (!(difference > 0)) throw new Error('Version must be newer than the current app version');
    function replace(name, pattern, expected, value) {
        if ([...contents[name].matchAll(pattern)].length !== expected) {
            throw new Error(`${name} pin/version format changed; no package files were updated`);
        }
        contents[name] = contents[name].replace(pattern, value);
    }
    replace('build.yaml', /SOURCE_REF: "[0-9a-f]{40}"/g, 1, `SOURCE_REF: "${revision}"`);
    replace('Dockerfile', /ARG SOURCE_REF=[0-9a-f]{40}/g, 2, `ARG SOURCE_REF=${revision}`);
    replace('Dockerfile', /ARG BUILD_VERSION=\d+\.\d+\.\d+/g, 1, `ARG BUILD_VERSION=${version}`);
    replace('config.yaml', /^version: "[^"]+"$/gm, 1, `version: "${version}"`);
    if (webui) {
        if (!/^r\d+$/.test(webui.revision) || !/^[0-9a-f]{64}$/.test(webui.sha256) ||
            !Number.isSafeInteger(webui.minimumServer) || webui.minimumServer < 1) {
            throw new Error('Invalid WebUI revision, digest or server compatibility pin');
        }
        replace('build.yaml', /WEBUI_REF: "r\d+"/g, 1, `WEBUI_REF: "${webui.revision}"`);
        replace('build.yaml', /WEBUI_SHA256: "[0-9a-f]{64}"/g, 1, `WEBUI_SHA256: "${webui.sha256}"`);
        replace('build.yaml', /WEBUI_MIN_SERVER: "\d+"/g, 1, `WEBUI_MIN_SERVER: "${webui.minimumServer}"`);
        replace('Dockerfile', /ARG WEBUI_REF=r\d+/g, 2, `ARG WEBUI_REF=${webui.revision}`);
        replace('Dockerfile', /ARG WEBUI_SHA256=[0-9a-f]{64}/g, 1, `ARG WEBUI_SHA256=${webui.sha256}`);
        replace('Dockerfile', /ARG WEBUI_MIN_SERVER=\d+/g, 1, `ARG WEBUI_MIN_SERVER=${webui.minimumServer}`);
    }
    for (const name of files) {
        fs.writeFileSync(path.join(app, name), contents[name].replace(/\r\n/g, '\n'), 'utf8');
    }
    console.log(`Pinned main Suwayomi server ${revision} as HA app ${version}. Commit/push the app-owned files to build and publish.`);
}

async function compatibleWebUI(serverVersion, fetcher = fetch) {
    const mappingResponse = await githubJson(
        'https://raw.githubusercontent.com/Suwayomi/Suwayomi-WebUI/master/versionToServerVersionMapping.json', fetcher,
    );
    if (!Array.isArray(mappingResponse.data)) throw new Error('Invalid upstream WebUI compatibility mapping');
    const mapping = mappingResponse.data.find(entry =>
        (entry.uiVersion === 'PREVIEW' || /^r\d+$/.test(entry.uiVersion || '')) &&
        /^r\d+$/.test(entry.serverVersion || '') && Number(entry.serverVersion.slice(1)) <= serverVersion);
    if (!mapping) throw new Error('No compatible WebUI is available for the selected server');
    const releasePath = mapping.uiVersion === 'PREVIEW' ? 'latest' : `tags/${mapping.uiVersion}`;
    const { data: release } = await githubJson(
        `https://api.github.com/repos/Suwayomi/Suwayomi-WebUI-preview/releases/${releasePath}`, fetcher,
    );
    if (!/^r\d+$/.test(release.tag_name || '') || !Array.isArray(release.assets)) {
        throw new Error('Invalid upstream preview WebUI release');
    }
    const asset = release.assets.find(value => value.name === `Suwayomi-WebUI-${release.tag_name}.zip`);
    if (!asset || !/^sha256:[0-9a-f]{64}$/.test(asset.digest || '')) {
        throw new Error('WebUI release is missing a verified SHA-256 digest');
    }
    return {
        revision: release.tag_name, sha256: asset.digest.slice(7),
        minimumServer: Number(mapping.serverVersion.slice(1)),
    };
}

async function serverCommitCount(revision, fetcher = fetch) {
    const { data, headers } = await githubJson(
        `https://api.github.com/repos/Suwayomi/Suwayomi-Server/commits?sha=${revision}&per_page=1`, fetcher,
    );
    if (!Array.isArray(data) || data.length !== 1 || data[0].sha !== revision) {
        throw new Error('Cannot verify the selected upstream commit history');
    }
    const last = /[?&]page=(\d+)[^>]*>;\s*rel="last"/.exec(headers.get('link') || '');
    const count = last ? Number(last[1]) : 1;
    if (!Number.isSafeInteger(count) || count < 1) throw new Error('Invalid upstream commit count');
    return count;
}

async function update(version, serverPath = path.resolve(__dirname, '..', '..', 'Suwayomi-Server'), app = defaultApp) {
    function git(...arguments) {
        return execFileSync('git', ['-C', serverPath, ...arguments], { encoding: 'utf8' }).trim();
    }
    if (git('status', '--porcelain')) throw new Error('Server checkout has local changes; preserve them before updating');
    git('pull', '--ff-only');
    const webui = await compatibleWebUI(Number(git('rev-list', '--count', 'HEAD')));
    pin(version, git('rev-parse', 'HEAD'), app, webui);
}

function latestRevision(runner = execFileSync) {
    let remote;
    try {
        remote = runner('git', [
            'ls-remote', 'https://github.com/Suwayomi/Suwayomi-Server.git', 'refs/heads/master',
        ], { encoding: 'utf8', timeout: 30000, killSignal: 'SIGKILL' }).trim();
    } catch (error) {
        if (error.code === 'ETIMEDOUT') {
            throw new Error('Upstream git ls-remote timed out after 30 seconds; package files were not changed', { cause: error });
        }
        throw error;
    }
    const revision = /^([0-9a-f]{40})\s+refs\/heads\/master$/.exec(remote);
    if (!revision) throw new Error('Cannot resolve upstream master; package files were not changed');
    return revision[1];
}

async function latest(app = defaultApp, fetcher = fetch) {
    const current = /^version: "(\d+\.\d+\.\d+)"$/m.exec(fs.readFileSync(path.join(app, 'config.yaml'), 'utf8'));
    const previous = /SOURCE_REF: "([0-9a-f]{40})"/.exec(fs.readFileSync(path.join(app, 'build.yaml'), 'utf8'));
    if (!current || !previous) throw new Error('Cannot resolve the current app source/version pins');
    const revision = latestRevision();
    const webui = await compatibleWebUI(await serverCommitCount(revision, fetcher), fetcher);
    const previousWebUI = currentWebUI(app);
    if (revision === previous[1] && JSON.stringify(webui) === JSON.stringify(previousWebUI)) {
        console.log('Upstream server and compatible WebUI are unchanged; no version bump is needed.');
        return false;
    }
    const parts = current[1].split('.').map(Number);
    parts[2] += 1;
    const version = parts.join('.');
    const changelogPath = path.join(app, 'CHANGELOG.md');
    const changelog = fs.readFileSync(changelogPath, 'utf8');
    if (!/^# Changelog\r?\n/.test(changelog)) throw new Error('Changelog format changed; package files were not updated');
    const updatedChangelog = changelog.replace(
        /^# Changelog\r?\n/,
        `# Changelog\n\n## ${version}\n\n- Automatically repackage [upstream Suwayomi server ${revision.slice(0, 12)}](https://github.com/Suwayomi/Suwayomi-Server/commit/${revision}) with verified compatible WebUI ${webui.revision}.\n- Publish only after amd64 and ARM64 build, ingress/API/browser and USB library-persistence tests pass.\n`,
    );
    pin(version, revision, app, webui);
    fs.writeFileSync(changelogPath, updatedChangelog.replace(/\r\n/g, '\n'), 'utf8');
    return true;
}

if (require.main === module) {
    (async () => { try {
        if (process.argv[2] === '--latest') {
            await latest();
        } else {
            await update(process.argv[2], process.argv[3]);
        }
    } catch (error) {
        console.error(error.message);
        process.exitCode = 1;
    } })();
}
module.exports = { update, pin, latest, latestRevision, compatibleWebUI, serverCommitCount };
