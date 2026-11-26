// JavaScript reference/legacy-wrapper tests, NOT a native CEF or Android provider fixture.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const patch = fs.readFileSync(path.join(__dirname, 'fixtures/legacy-android-evaluation-semantics.patch'), 'utf8');
const helperDiff = patch.split('diff --git a/AndroidCompat/src/main/java/xyz/nulldev/androidcompat/webkit/KcefHelper.kt')[1]
  .split('\ndiff --git ')[0];
const removedHelper = helperDiff.split('\n').filter(line => line.startsWith('-') && !line.startsWith('---'))
  .map(line => line.slice(1)).join('\n');
const legacyTemplate = removedHelper.split('internal fun String.toCode(id: String): String =')[1]
  .split('"""')[1];
assert.ok(legacyTemplate.includes('${this.asFunctionBody()}'));

function legacy(script, globals = {}) {
  let captured;
  const body = /[\r\n]/.test(script) || script.trim().startsWith('return') ? script : `return ${script}`;
  const code = legacyTemplate.replace('${this.asFunctionBody()}', body)
    .replaceAll('${QUERY_FN}', '__legacyEvalQuery').replaceAll('$id', 'fixture-id');
  vm.runInNewContext(code, {
    ...globals,
    console: {error() {}},
    window: {...globals.window, __legacyEvalQuery(query) { captured = JSON.parse(query.request).result; }},
  });
  return captured;
}

// ECMAScript script-completion reference for Runtime.evaluate(returnByValue=true).
// This does not call production CDP, the Kotlin converter, or an Android WebView.
function reference(script, globals = {}) {
  try {
    return JSON.stringify(vm.runInNewContext(script, globals)) ?? 'null';
  } catch {
    return 'null';
  }
}

const predicate = 'document.title.includes("Just a moment") ||\n' +
  "document.querySelector('.captcha-overlay--visible') != null";

test('exact AllManga multiline predicate loses both boolean results in removed production wrapper', () => {
  for (const [title, overlay, expected] of [
    ['ordinary page', null, 'false'],
    ['Just a moment', null, 'true'],
    ['ordinary page', {}, 'true'],
  ]) {
    const globals = {document: {title, querySelector: () => overlay}};
    assert.equal(legacy(predicate, globals), 'null');
    assert.equal(reference(predicate, globals), expected);
  }
});

test('script completion covers primitives, quoted strings, null, undefined and exceptions', () => {
  for (const [script, expected] of [
    ['1;\n42', '42'],
    ['false;\ntrue', 'true'],
    ['"first";\n"a\\\"b"', '"a\\"b"'],
    ['null', 'null'],
    ['undefined', 'null'],
    ['throw new Error("fixture")', 'null'],
    ['missing.property', 'null'],
  ]) {
    assert.equal(reference(script), expected);
  }
});

test('multiline IIFE side effects already work in the old implementation', () => {
  const globals = {state: {navigated: false}};
  assert.equal(legacy('(function () {\nstate.navigated = true;\n})();', globals), 'null');
  assert.equal(globals.state.navigated, true);
});

test('JavaScript postMessage hook is checked separately from evaluation return values', () => {
  const received = [];
  const globals = {window: {fixtureBridge: {postMessage: message => received.push(message)}}};
  // Only an in-memory JS hook fixture: not proof that addJavascriptInterface/native dispatch works.
  const script = 'window.fixtureBridge.postMessage("fixture-message");\ntrue';
  assert.equal(legacy(script, globals), 'null');
  assert.equal(reference(script, globals), 'true');
  assert.deepEqual(received, ['fixture-message', 'fixture-message']);
});
