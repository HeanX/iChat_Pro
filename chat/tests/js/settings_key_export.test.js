// P4 T43/T08 follow-up (#170) remediation -- WEB-04: the standalone settings
// page must not claim a backup exists when the export never happened.
//
// The code under test is read out of templates/components/settings_panel.html
// at runtime and executed as-is; nothing here re-implements the handler. The
// real `_kmgr()` / `_showKeyMsg()` of that template block are used, so a fake
// success message cannot be produced by this test file.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const root = path.resolve(__dirname, '../../..');
const templatePath = path.join(root, 'templates/components/settings_panel.html');
const templateSource = fs.readFileSync(templatePath, 'utf8').replace(/\r\n/g, '\n');
const chatSource = fs.readFileSync(path.join(root, 'static/js/chat.js'), 'utf8').replace(/\r\n/g, '\n');

// The standalone settings page (templates/pages/settings.html) includes this
// component and never loads chat.js, so this inline block is the handler that
// actually runs for the user.
const inlineScripts = [...templateSource.matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)]
  .map(match => match[1]);
const panelScript = inlineScripts.find(script => /function\s+keyMgrExport\s*\(/.test(script));

test('the export handler is extracted from the real settings component', () => {
  assert.ok(
    panelScript,
    'templates/components/settings_panel.html must declare keyMgrExport in an inline script'
  );
  assert.match(panelScript, /function\s+_kmgr\s*\(/, 'the extracted block is the real component script');
  assert.match(panelScript, /key-mgmt-message/, 'the extracted block owns the real message element');
  assert.match(panelScript, /function\s+keyMgrImport\s*\(/, 'the extracted block is the full key-management UI');

  // Guard against silently testing the chat.js copy instead: that one already
  // awaited, and it is not loaded on the standalone page.
  const successCopy = panelScript.match(/_showKeyMsg\('Backup downloaded\. Keep it safe!?'\)/);
  assert.ok(successCopy, 'the component keeps its own success wording');
  assert.equal(
    successCopy[0].includes("Keep it safe!'"),
    true,
    'the standalone component wording (with !) is the one under test'
  );
  assert.equal(
    /_showKeyMsg\('Backup downloaded\. Keep it safe\.'\)/.test(chatSource),
    true,
    'chat.js keeps the separate variant that a standalone page never loads'
  );
});

const SUCCESS_TEXT = 'Backup downloaded. Keep it safe!';
const DEFAULT_LABEL = 'Export Backup';

function createPanel(options = {}) {
  const messages = [];
  const elements = new Map();

  // A real className-backed classList: the component styles its message by
  // adding/removing Tailwind classes, so a no-op stub would hide a real success
  // being shown with error styling or vice versa.
  function classListOf(el) {
    const tokens = () => el.className.split(/\s+/).filter(Boolean);
    const write = list => {
      el.className = list.join(' ');
    };
    return {
      add: (...names) => {
        const list = tokens();
        for (const name of names) if (!list.includes(name)) list.push(name);
        write(list);
      },
      remove: (...names) => write(tokens().filter(token => !names.includes(token))),
      contains: name => tokens().includes(name),
      toggle: (name, force) => {
        const has = tokens().includes(name);
        const shouldHave = force === undefined ? !has : Boolean(force);
        if (shouldHave && !has) classListOf(el).add(name);
        if (!shouldHave && has) classListOf(el).remove(name);
        return shouldHave;
      },
      replace: (from, to) => {
        const list = tokens();
        const index = list.indexOf(from);
        if (index === -1) return false;
        list[index] = to;
        write(list);
        return true;
      },
    };
  }

  const messageEl = {
    className: 'hidden text-xs p-2.5 rounded-custom-md border',
    _text: '',
    get textContent() {
      return this._text;
    },
    set textContent(value) {
      this._text = String(value);
      messages.push(String(value));
    },
  };
  messageEl.classList = classListOf(messageEl);
  elements.set('key-mgmt-message', messageEl);

  const exportButton = {
    id: 'btn-key-export',
    disabled: false,
    textContent: DEFAULT_LABEL,
    labels: [],
  };
  elements.set('btn-key-export', exportButton);

  const calls = { loadCurrentRecord: 0, exportBackup: 0 };

  const context = vm.createContext({
    console,
    setTimeout: () => 0,
    clearTimeout: () => {},
    document: {
      getElementById: id => elements.get(id) || null,
    },
    window: {
      iChatKeyManager: options.kmgr
        ? {
            loadCurrentRecord: () => {
              calls.loadCurrentRecord += 1;
              return options.kmgr.loadCurrentRecord();
            },
            exportBackup: () => {
              calls.exportBackup += 1;
              return options.kmgr.exportBackup();
            },
          }
        : null,
    },
  });

  // Execute the extracted component script itself, not a transcription of it.
  vm.runInContext(panelScript, context, { filename: templatePath });

  return {
    context,
    messages,
    messageEl,
    exportButton,
    calls,
    isErrorShown: () => messageEl.className.includes('text-red-700'),
    isSuccessShown: () => messageEl.className.includes('text-green-700'),
  };
}

const record = { user_id: '7', key_version: 2, key_fingerprint: 'AAAA' };

test('a completed export shows the success message only after it resolves', async () => {
  let settle;
  let resolved = false;
  const panel = createPanel({
    kmgr: {
      loadCurrentRecord: () => record,
      exportBackup: () =>
        new Promise(resolve => {
          settle = () => {
            resolved = true;
            resolve();
          };
        }),
    },
  });

  const pendingCall = panel.context.keyMgrExport();

  assert.equal(panel.calls.exportBackup, 1, 'the real handler calls exportBackup');
  assert.equal(resolved, false);
  assert.deepEqual(panel.messages, [], 'no message is shown while the export is still running');
  assert.equal(panel.exportButton.disabled, true, 'the button is disabled during the export');
  assert.notEqual(panel.exportButton.textContent, DEFAULT_LABEL, 'the button reports progress');

  settle();
  await pendingCall;

  assert.equal(panel.messages.length, 1);
  assert.equal(panel.messages[0], SUCCESS_TEXT, 'the existing success wording is preserved');
  assert.equal(panel.isSuccessShown(), true);
  assert.equal(panel.exportButton.disabled, false, 'the button is restored in finally');
  assert.equal(panel.exportButton.textContent, DEFAULT_LABEL);
});

test('an asynchronous rejection reports failure and never reports success', async () => {
  const failure = new Error(
    'This device stores its private key as non-extractable. A JSON backup can only be exported immediately after key creation, import, or legacy migration.'
  );
  const panel = createPanel({
    kmgr: {
      loadCurrentRecord: () => record,
      exportBackup: () => Promise.reject(failure),
    },
  });

  await panel.context.keyMgrExport();

  assert.equal(panel.messages.includes(SUCCESS_TEXT), false, 'a rejected export must not claim a download');
  assert.equal(panel.messages.length, 1);
  assert.match(panel.messages[0], /^Export failed: /);
  assert.match(panel.messages[0], /non-extractable/, 'the reason the user must act on is kept');
  assert.match(
    panel.messages[0],
    /use the backup you saved/i,
    'the user is told how to recover instead of trusting a backup that does not exist'
  );
  assert.equal(panel.isErrorShown(), true);
  assert.equal(panel.exportButton.disabled, false, 'the button is re-enabled after a failure');
  assert.equal(panel.exportButton.textContent, DEFAULT_LABEL);
});

test('a synchronous throw reports failure and never reports success', async () => {
  const panel = createPanel({
    kmgr: {
      loadCurrentRecord: () => record,
      exportBackup: () => {
        throw new Error('Blob is not supported in this context.');
      },
    },
  });

  await panel.context.keyMgrExport();

  assert.equal(panel.messages.includes(SUCCESS_TEXT), false);
  assert.equal(panel.messages.length, 1);
  assert.match(panel.messages[0], /^Export failed: Blob is not supported/);
  assert.equal(panel.isErrorShown(), true);
  assert.equal(panel.exportButton.disabled, false);
  assert.equal(panel.exportButton.textContent, DEFAULT_LABEL);
});

test('a missing key manager never exports and never reports success', async () => {
  const noManager = createPanel({});
  await noManager.context.keyMgrExport();
  assert.equal(noManager.calls.exportBackup, 0);
  assert.equal(noManager.messages.includes(SUCCESS_TEXT), false);
  assert.equal(noManager.isErrorShown(), true);
  assert.match(noManager.messages[0], /Key manager is not available/);
});

test('an implementation refusal is reported by the implementation, not a duplicate gate', async () => {
  // Round-2 review M-3: the panel used to refuse whenever loadCurrentRecord()
  // was empty, which is narrower than exportBackup()'s real precondition
  // (`pendingBackup || record.private_key`). The panel must defer to the
  // implementation and surface its reason.
  const reason = new Error(
    'This device stores its private key as non-extractable. A JSON backup can only be exported immediately after key creation, import, or legacy migration.'
  );
  const noRecord = createPanel({
    kmgr: {
      loadCurrentRecord: () => null,
      exportBackup: () => Promise.reject(reason),
    },
  });
  await noRecord.context.keyMgrExport();

  assert.equal(noRecord.calls.exportBackup, 1, 'the implementation decides, not a local gate');
  assert.equal(noRecord.messages.includes(SUCCESS_TEXT), false);
  assert.equal(noRecord.isErrorShown(), true);
  assert.match(noRecord.messages[0], /non-extractable/, 'the implementation reason reaches the user');
  assert.match(noRecord.messages[0], /use the backup you saved/i);
});

test('a pending-backup-only device can export through the panel', async () => {
  // Round-2 review M-3: the local record was pruned but the tab still holds the
  // one-shot pending backup. exportBackup() succeeds in that state, so the
  // panel entry point must reach it instead of refusing.
  let downloads = 0;
  const panel = createPanel({
    kmgr: {
      loadCurrentRecord: () => null,
      exportBackup: async () => {
        downloads += 1;
        return undefined;
      },
    },
  });

  await panel.context.keyMgrExport();

  assert.equal(panel.calls.exportBackup, 1, 'the panel reached the implementation');
  assert.equal(downloads, 1, 'and the export actually produced its download');
  assert.equal(panel.messages[0], SUCCESS_TEXT, 'so success is now truthful');
  assert.equal(panel.isSuccessShown(), true);
});

test('the panel gate does not contradict the real key-manager precondition', () => {
  // Keep the two sources of truth from drifting again: assert that the panel
  // no longer pre-empts exportBackup() with its own record check, and that
  // key-manager.js still allows a pending backup with no local record.
  const managerSource = fs.readFileSync(
    path.join(root, 'static/js/key-manager.js'),
    'utf8'
  ).replace(/\r\n/g, '\n');
  assert.match(
    managerSource,
    /if \(!pendingBackup && \(!record \|\| !record\.private_key\)\)/,
    'exportBackup() precondition is pendingBackup || record.private_key'
  );
  assert.match(
    managerSource,
    /const backupRecord = pendingBackup \|\| record;/,
    'and a pending backup alone is exported'
  );
  assert.equal(
    /if \(!kmgr \|\| \(kmgr\.loadCurrentRecord && !kmgr\.loadCurrentRecord\(\)\)\)/.test(panelScript),
    false,
    'the panel must not re-implement the precondition (round-2 M-3)'
  );
  assert.match(
    panelScript,
    /await kmgr\.exportBackup\(\)/,
    'it awaits the implementation instead'
  );
});

test('a second click during an in-flight export cannot start a parallel export', async () => {
  let settle;
  const panel = createPanel({
    kmgr: {
      loadCurrentRecord: () => record,
      exportBackup: () => new Promise(resolve => { settle = resolve; }),
    },
  });

  const first = panel.context.keyMgrExport();
  const second = panel.context.keyMgrExport();
  assert.equal(panel.exportButton.disabled, true);

  settle();
  await Promise.all([first, second]);

  assert.equal(panel.calls.exportBackup, 1, 'the in-flight guard keeps the export single-shot');
  assert.equal(panel.messages.filter(text => text === SUCCESS_TEXT).length, 1);
  assert.equal(panel.exportButton.disabled, false);
  assert.equal(panel.exportButton.textContent, DEFAULT_LABEL);
});
