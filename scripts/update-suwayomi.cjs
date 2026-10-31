const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const defaultApp = path.resolve(__dirname, '..', 'suwayomi');

function pin(version, revision, app = defaultApp) {
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
    for (const name of files) {
        fs.writeFileSync(path.join(app, name), contents[name].replace(/\r\n/g, '\n'), 'utf8');
    }
    console.log(`Pinned main Suwayomi server ${revision} as HA app ${version}. Commit/push the app-owned files to build and publish.`);
}

function update(version, serverPath = path.resolve(__dirname, '..', '..', 'Suwayomi-Server'), app = defaultApp) {
    function git(...arguments) {
        return execFileSync('git', ['-C', serverPath, ...arguments], { encoding: 'utf8' }).trim();
    }
    if (git('status', '--porcelain')) throw new Error('Server checkout has local changes; preserve them before updating');
    git('pull', '--ff-only');
    pin(version, git('rev-parse', 'HEAD'), app);
}

function latest(app = defaultApp) {
    const current = /^version: "(\d+\.\d+\.\d+)"$/m.exec(fs.readFileSync(path.join(app, 'config.yaml'), 'utf8'));
    const previous = /SOURCE_REF: "([0-9a-f]{40})"/.exec(fs.readFileSync(path.join(app, 'build.yaml'), 'utf8'));
    if (!current || !previous) throw new Error('Cannot resolve the current app source/version pins');
    const remote = execFileSync('git', [
        'ls-remote', 'https://github.com/Suwayomi/Suwayomi-Server.git', 'refs/heads/master',
    ], { encoding: 'utf8' }).trim();
    const revision = /^([0-9a-f]{40})\s+refs\/heads\/master$/.exec(remote);
    if (!revision) throw new Error('Cannot resolve upstream master; package files were not changed');
    if (revision[1] === previous[1]) {
        console.log('Upstream server is unchanged; no version bump is needed.');
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
        `# Changelog\n\n## ${version}\n\n- Automatically repackage [upstream Suwayomi server ${revision[1].slice(0, 12)}](https://github.com/Suwayomi/Suwayomi-Server/commit/${revision[1]}) with its matching WebUI.\n- Publish only after amd64 and ARM64 build, ingress/API/browser and USB library-persistence tests pass.\n`,
    );
    pin(version, revision[1], app);
    fs.writeFileSync(changelogPath, updatedChangelog.replace(/\r\n/g, '\n'), 'utf8');
    return true;
}

if (require.main === module) {
    try {
        if (process.argv[2] === '--latest') {
            latest();
        } else {
            update(process.argv[2], process.argv[3]);
        }
    } catch (error) {
        console.error(error.message);
        process.exitCode = 1;
    }
}
module.exports = { update, pin, latest };
