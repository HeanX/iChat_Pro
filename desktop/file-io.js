'use strict';
/**
 * P4 T19: native file selection / save / open for the desktop shell.
 *
 * This module owns every decision that could widen filesystem access; main.js
 * only wires it to Electron (dialog.showSaveDialog / shell.openPath) and the
 * IPC channels. Keeping the policy here means it can be exercised under plain
 * Node with real files in a disposable temp directory - no Electron needed.
 *
 * Security model (see docs/phase4/windows-files.md):
 * - A save target is only created from a native save dialog. The renderer can
 *   never name a path, so there is no "write anywhere" primitive.
 * - The grant token is random (128-bit) and bound to the webContents id that
 *   created it; another frame in the same app cannot reuse it.
 * - Writes are append-only and strictly sequential: an offset that does not
 *   match the bytes already written is rejected. The renderer cannot seek, so
 *   it cannot preallocate or punch holes.
 * - The declared total is enforced; a grant that promised N bytes refuses
 *   N + 1. Single chunks are capped independently.
 * - Grants expire, are capped in number, and opening a downloaded file is
 *   keyed by token rather than by a caller-supplied path.
 */
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');

/** Mirrors static/js/file-transfer.js MAX_FILE_SIZE so both sides agree. */
const MAX_FILE_BYTES = 100 * 1024 * 1024;
/** Base64 payload cap for one write-chunk round trip. */
const MAX_CHUNK_BYTES = 2 * 1024 * 1024;
/** Concurrent unexpired grants allowed per app instance. */
const MAX_GRANTS = 8;
/** A download that stalls for this long must be restarted by the user. */
const GRANT_TTL_MS = 30 * 60 * 1000;
const MAX_FILE_NAME_LENGTH = 180;

// Round 2 review (BLOCKER): a *blocklist* is the wrong shape for this. The
// name comes from the peer's encrypted metadata and becomes the save dialog's
// default, so a peer can pick any extension - and Windows Shell executes far
// more than the obvious list: .scf, .url, .pyw, .py, .sh, .pl, .rb, .cpl,
// .js, .jse, .wsf, .hta, .jar, .pif, .msix... Enumerating them is a losing
// game. Only known-safe *document* types are opened automatically; everything
// else is saved normally and the user opens it from Explorer themselves.
// Round 3 review (MAJOR): .svg is NOT on this list, even though it is an
// "image". A local SVG opened by the shell runs its own <script> in a file://
// origin (verified against Electron 39.8.10: a bundled SVG set
// document.title from script). That is not the same as arbitrary code
// execution, but it is active content arriving from a chat peer, so it is not
// something this app launches on the user's behalf. The in-app image preview
// still renders SVG safely inside the chat page.
const OPENABLE_EXTENSIONS = [
  // plain text / data. Round 5 review (F4/P2): .xml is NOT here. An .xml file
  // can carry an XHTML-namespace <script>, and the system association for XML
  // may be a browser - the extension alone does not make the content passive.
  // Same reasoning that removed .svg in round 3. It is still saved normally.
  '.txt', '.log', '.csv', '.tsv', '.json', '.yaml', '.yml', '.md', '.ini', '.cfg', '.conf',
  // documents
  '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp', '.rtf', '.epub',
  // images (passive raster formats only - no SVG, see above)
  '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tif', '.tiff', '.ico', '.heic',
  // audio / video
  '.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma',
  '.mp4', '.mkv', '.mov', '.avi', '.webm', '.wmv', '.flv', '.m4v',
  // archives (opened by the archiver, not executed)
  '.zip', '.7z', '.rar', '.tar', '.gz', '.tgz', '.bz2', '.xz',
];

const RESERVED_FILE_NAMES = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\..*)?$/i;

class FileIoError extends Error {
  constructor(code, detail) {
    super(code);
    this.name = 'FileIoError';
    this.code = code;
    if (detail !== undefined) this.detail = detail;
  }
}

function fail(code, detail) {
  throw new FileIoError(code, detail);
}

/**
 * Strict non-negative integer parse. Numbers with a fraction, NaN, Infinity,
 * negative values and non-decimal strings are all rejected instead of being
 * silently coerced (Number('') === 0 would otherwise create a free pass).
 */
function toByteCount(raw) {
  if (typeof raw === 'number') {
    return Number.isSafeInteger(raw) && raw >= 0 ? raw : null;
  }
  if (typeof raw === 'string' && /^\d{1,12}$/.test(raw)) {
    const value = Number(raw);
    return Number.isSafeInteger(value) ? value : null;
  }
  return null;
}

/**
 * Reduce a peer-supplied display name to something Windows will actually
 * create under the name the user sees. Returns null when nothing usable is
 * left, so callers can fall back to a generic name.
 */
function sanitizeFileName(raw) {
  let name = String(raw === null || raw === undefined ? '' : raw);
  // Drop directory components from either separator family.
  name = name.split(/[\\/]+/).pop() || '';
  // Characters Win32 forbids in a file name (plus control characters).
  // eslint-disable-next-line no-control-regex
  name = name.replace(/[<>:"|?*\u0000-\u001f]/g, '_');
  // Win32 silently strips trailing dots/spaces, which turns "a.exe " into
  // "a.exe" and makes what we report differ from what lands on disk.
  name = name.replace(/[. ]+$/, '');
  if (!name || name === '.' || name === '..') return null;
  if (RESERVED_FILE_NAMES.test(name)) name = '_' + name;
  if (name.length > MAX_FILE_NAME_LENGTH) {
    const dot = name.lastIndexOf('.');
    const extension = dot > 0 && name.length - dot <= 16 ? name.slice(dot) : '';
    name = name.slice(0, MAX_FILE_NAME_LENGTH - extension.length) + extension;
  }
  return name;
}

/** Strict base64: rejects wrong padding, stray characters and empty input. */
function decodeBase64(raw) {
  if (typeof raw !== 'string' || raw.length === 0) fail('invalid_payload', 'data');
  if (raw.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(raw)) fail('invalid_payload', 'data');
  const bytes = Buffer.from(raw, 'base64');
  if (bytes.length === 0) fail('invalid_payload', 'data');
  if (bytes.length > MAX_CHUNK_BYTES) fail('too_large', 'chunk');
  return bytes;
}

/**
 * @param {object} [options]
 * @param {typeof import('node:fs')} [options.fs] - injectable for failure tests.
 * @param {number} [options.maxGrants]
 * @param {number} [options.maxFileBytes]
 * @param {number} [options.grantTtlMs]
 * @param {() => number} [options.now]
 * @param {() => string} [options.createToken]
 * @param {string[]} [options.openableExtensions]
 */
function createFileIo(options = {}) {
  const fileSystem = options.fs || fs;
  const maxGrants = options.maxGrants || MAX_GRANTS;
  const maxFileBytes = options.maxFileBytes || MAX_FILE_BYTES;
  const grantTtlMs = options.grantTtlMs || GRANT_TTL_MS;
  const now = options.now || (() => Date.now());
  const createToken = options.createToken || (() => crypto.randomUUID());
  const openableExtensions = options.openableExtensions || OPENABLE_EXTENSIONS;

  /** token -> grant */
  const grants = new Map();

  function prune() {
    const at = now();
    for (const [token, grant] of grants) {
      if (at >= grant.expiresAt) grants.delete(token);
    }
  }

  /** Count only grants that can still accept bytes. */
  function liveGrantCount() {
    prune();
    let live = 0;
    for (const grant of grants.values()) {
      if (!grant.sealed) live += 1;
    }
    return live;
  }

  /**
   * Identity of a save target for ownership comparisons.
   *
   * Round 5 review (F1/P1): Windows paths are case-insensitive, so ownership
   * has to be compared on a resolved, lower-cased path - otherwise
   * "C:\Dir\a.txt" and "c:\dir\A.TXT" would look like different targets and
   * two downloads could still interleave into one file. Trailing separators are
   * dropped for the same reason. Aliasing through symlinks, hard links or 8.3
   * short names is NOT detected (documented boundary): the dialog hands back
   * the path the user picked, and this check exists to stop two of our own
   * downloads from colliding, not to police the filesystem.
   */
  function targetKeyOf(raw) {
    let resolved;
    try {
      resolved = path.resolve(String(raw));
    } catch (_) {
      resolved = String(raw);
    }
    const trimmed = resolved.replace(/[\\/]+$/, '');
    return process.platform === 'win32' ? trimmed.toLowerCase() : trimmed;
  }

  /**
   * Drop finished grants that own the same target.
   *
   * Called only after the new grant has passed its checks: an older token's
   * "open" affordance would otherwise point at bytes somebody else just wrote.
   * Active grants are never dropped here - they block the new grant instead.
   */
  function releaseGrantsFor(targetKey) {
    for (const [token, grant] of grants) {
      if (grant.sealed && targetKeyOf(grant.filePath) === targetKey) grants.delete(token);
    }
  }

  /** Validate what the renderer asks for BEFORE a native dialog is shown. */
  function validateSaveRequest(payload) {
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      fail('invalid_payload', 'request');
    }
    const fileName = sanitizeFileName(payload.fileName);
    if (!fileName) fail('invalid_payload', 'fileName');
    const totalBytes = toByteCount(payload.totalBytes);
    if (totalBytes === null) fail('invalid_payload', 'totalBytes');
    if (totalBytes > maxFileBytes) fail('too_large', 'totalBytes');
    return { fileName, totalBytes };
  }

  /**
   * Called after the user picked a path in the native dialog. The path is the
   * only input that does not come from the renderer.
   */
  function createSaveGrant(input) {
    const request = validateSaveRequest(input);
    if (typeof input.senderId !== 'number' || !Number.isSafeInteger(input.senderId) || input.senderId <= 0) {
      fail('untrusted sender');
    }
    const target = typeof input.filePath === 'string' ? input.filePath : '';
    if (!target || target.length > 4096 || target.includes('\u0000')) fail('invalid_target');
    if (!path.isAbsolute(target)) fail('invalid_target');
    try {
      if (fileSystem.existsSync(target) && fileSystem.statSync(target).isDirectory()) fail('invalid_target');
    } catch (error) {
      if (error instanceof FileIoError) throw error;
      fail('invalid_target');
    }

    prune();
    // Round 5 review (F1/P1): exactly one download may own a target path.
    // Without this, a second download could pick the same file while the first
    // is still streaming: both truncate it with 'w+', both write at their own
    // offsets, and both seal successfully - leaving a file that is a mix of two
    // downloads (AES-GCM cannot notice, because the mixing happens on disk
    // after decryption).
    //
    // An *active* grant on the path blocks the new one. Everything is checked
    // before anything is mutated, so a rejected request leaves no trace.
    const targetKey = targetKeyOf(target);
    for (const grant of grants.values()) {
      if (targetKeyOf(grant.filePath) === targetKey && !grant.sealed) fail('target_in_use');
    }

    // Review round 1 (BLOCKER): a finished download must not occupy a slot
    // for the rest of its TTL, otherwise the 9th save in 30 minutes fails for
    // a user who did nothing wrong. Only grants still able to accept bytes
    // count against the cap; sealed ones stay resolvable for "open".
    if (liveGrantCount() >= maxGrants) fail('too_many_grants');

    // Taking the path over invalidates any *finished* grant that owned it: its
    // "open" token would otherwise point at bytes this download is replacing.
    releaseGrantsFor(targetKey);

    const token = createToken();
    const grant = {
      token,
      filePath: target,
      targetKey,
      // Round 5 review (F5/P3): the name the user actually confirmed in the
      // dialog is the truth. Reporting the peer's suggested name while the
      // disk holds "chosen-name.txt" makes the success toast a lie.
      fileName: path.basename(String(target).replace(/[\\/]+$/, '')) || request.fileName,
      senderId: input.senderId,
      totalBytes: request.totalBytes,
      writtenBytes: 0,
      sealed: false,
      createdAt: now(),
      expiresAt: now() + grantTtlMs,
    };
    grants.set(token, grant);
    return { token, fileName: grant.fileName, totalBytes: grant.totalBytes };
  }

  function requireGrant(token, senderId) {
    if (typeof token !== 'string' || token.length === 0 || token.length > 128) fail('invalid_payload', 'token');
    const grant = grants.get(token);
    if (!grant) fail('not_found');
    if (now() >= grant.expiresAt) {
      grants.delete(token);
      fail('expired');
    }
    if (grant.senderId !== senderId) fail('wrong_sender');
    return grant;
  }

  /**
   * Give up on a download and free its quota slot.
   *
   * Round 2 review (MAJOR): without this, every failed attempt (disk full,
   * file locked) kept its grant forever, so after 8 failures every save in the
   * next 30 minutes failed with a misleading "too many downloads" message.
   * The partially written file is left on disk (the user may want it) but the
   * token stops working, so the renderer must restart the download.
   */
  function releaseGrant(input) {
    if (!input || typeof input !== 'object') fail('invalid_payload', 'request');
    const token = input.token;
    if (typeof token !== 'string' || token.length === 0 || token.length > 128) fail('invalid_payload', 'token');
    const grant = grants.get(token);
    if (!grant) fail('not_found');
    if (grant.senderId !== input.senderId) fail('wrong_sender');
    grants.delete(token);
    return { released: true, fileName: grant.fileName, discardedBytes: grant.writtenBytes };
  }

  /**
   * Append one chunk. `offset` must equal the bytes already accepted, so the
   * on-disk file and the bookkeeping cannot drift apart.
   */
  function writeChunk(input) {
    if (!input || typeof input !== 'object') fail('invalid_payload', 'request');
    const grant = requireGrant(input.token, input.senderId);
    if (grant.sealed) fail('already_sealed');
    const offset = toByteCount(input.offset);
    if (offset === null) fail('invalid_payload', 'offset');
    if (offset !== grant.writtenBytes) fail('offset_mismatch');
    const bytes = decodeBase64(input.dataBase64);
    const next = grant.writtenBytes + bytes.length;
    if (next > grant.totalBytes) fail('too_large', 'declared_total');
    // Review round 1 (MAJOR): appending blindly is unsafe. If a previous
    // attempt wrote part of a chunk and then threw (disk full, killed), the
    // bookkeeping still points at the old offset, and appending again would
    // duplicate those bytes. Positional writes make a retry idempotent: the
    // same offset is overwritten with the same bytes instead of appended.
    writeAt(grant, bytes, grant.writtenBytes);
    grant.writtenBytes = next;
    return { written: grant.writtenBytes, total: grant.totalBytes };
  }

  /**
   * Write `bytes` at absolute `position`. On a real fd this truncates nothing
   * and seeks explicitly, so a partially written chunk is repaired by the
   * retry rather than duplicated.
   */
  function writeAt(grant, bytes, position) {
    try {
      if (typeof fileSystem.openSync === 'function' &&
          typeof fileSystem.writeSync === 'function' &&
          typeof fileSystem.closeSync === 'function') {
        // 'w+' on the first chunk creates the file and truncates a target the
        // user already agreed to overwrite; later chunks seek with 'r+'.
        const fd = fileSystem.openSync(grant.filePath, position === 0 ? 'w+' : 'r+');
        try {
          let written = 0;
          while (written < bytes.length) {
            const chunk = fileSystem.writeSync(
              fd, bytes, written, bytes.length - written, position + written,
            );
            if (!chunk || chunk <= 0) {
              const error = new Error('short write');
              error.code = 'EIO';
              throw error;
            }
            written += chunk;
          }
        } finally {
          fileSystem.closeSync(fd);
        }
        return;
      }
      // Fallback for an injected/limited fs: first write creates the file.
      if (position === 0) fileSystem.writeFileSync(grant.filePath, bytes);
      else fileSystem.appendFileSync(grant.filePath, bytes);
    } catch (error) {
      // The offset is deliberately NOT advanced, so the same chunk is retried.
      fail('write_failed', error && error.code ? error.code : undefined);
    }
  }

  /**
   * Finish the download: the declared byte count must have really arrived.
   *
   * Round 3 review (MINOR): the truncate that used to live here was dead code -
   * the first chunk opens the target with 'w+', which already discards the old
   * contents - so it is gone rather than kept as an untestable branch.
   */
  function sealGrant(input) {
    if (!input || typeof input !== 'object') fail('invalid_payload', 'request');
    const grant = requireGrant(input.token, input.senderId);
    if (!grant.sealed) {
      if (grant.writtenBytes !== grant.totalBytes) fail('incomplete');
      try {
        if (grant.writtenBytes === 0) {
          // Nothing was written, so 'w+' never ran; create the empty file.
          fileSystem.writeFileSync(grant.filePath, Buffer.alloc(0));
        }
      } catch (error) {
        fail('write_failed', error && error.code ? error.code : undefined);
      }
      grant.sealed = true;
    }
    return { fileName: grant.fileName, bytes: grant.writtenBytes };
  }

  /**
   * Resolve a token to an on-disk path that a grant in this session actually
   * wrote completely. No caller-supplied path is ever trusted.
   */
  function openTarget(input) {
    if (!input || typeof input !== 'object') fail('invalid_payload', 'request');
    const grant = requireGrant(input.token, input.senderId);
    if (!grant.sealed) fail('not_sealed');
    if (grant.writtenBytes !== grant.totalBytes) fail('incomplete');
    // Round 1 (MINOR): Win32 strips trailing dots/spaces, so "evil.exe "
    // lands as "evil.exe" while extname() would report ".exe ". Round 2
    // (BLOCKER): the decision is an allowlist, so anything unusual - including
    // a missing extension or a double extension - simply is not openable.
    const trimmed = String(grant.filePath).replace(/[. ]+$/, '');
    const extension = path.extname(trimmed).toLowerCase();
    if (!openableExtensions.includes(extension)) fail('blocked_type', extension || '(none)');
    try {
      if (!fileSystem.existsSync(grant.filePath)) fail('not_found');
    } catch (error) {
      if (error instanceof FileIoError) throw error;
      fail('not_found');
    }
    return { filePath: grant.filePath, fileName: grant.fileName };
  }

  return {
    validateSaveRequest,
    createSaveGrant,
    writeChunk,
    sealGrant,
    releaseGrant,
    openTarget,
    prune,
    // Introspection for logs/tests; never exposed over IPC.
    activeGrants: () => grants.size,
    constants: { MAX_FILE_BYTES, MAX_CHUNK_BYTES, MAX_GRANTS, GRANT_TTL_MS },
  };
}

module.exports = {
  createFileIo,
  sanitizeFileName,
  toByteCount,
  FileIoError,
  OPENABLE_EXTENSIONS,
  MAX_FILE_BYTES,
  MAX_CHUNK_BYTES,
  MAX_GRANTS,
  GRANT_TTL_MS,
};
