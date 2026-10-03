/**
 * ChatConnection / ChatOutbox — P4 T33 shared client module.
 *
 * Connection state machine:
 *   disconnected → connecting → online
 *        ↑              │ (close)
 *        └── reconnecting (exponential backoff + jitter, cap < 30s)
 *   auth_required / unsupported — terminal, no reconnect.
 * Heartbeat: ping every heartbeatInterval; no pong within pongTimeout
 * forces the socket closed so the backoff loop takes over.
 *
 * Outbox message status machine (T33):
 *   sending → sent → delivered → read        (forward-only receipts)
 *   sending → failed → sending | sent        (retry keeps the SAME
 *   client_message_id; a late ACK revives a timed-out entry)
 *
 * UMD: usable from the browser (window.ChatConnection) and Node (tests).
 */
(function (root, factory) {
  if (typeof module !== "undefined" && module.exports) {
    module.exports = factory();
  } else {
    root.ChatConnection = factory();
  }
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var CONNECTION_STATES = {
    DISCONNECTED: "disconnected",
    CONNECTING: "connecting",
    ONLINE: "online",
    RECONNECTING: "reconnecting",
    AUTH_REQUIRED: "auth_required",
    UNSUPPORTED: "unsupported",
  };

  var MESSAGE_STATES = {
    SENDING: "sending",
    SENT: "sent",
    DELIVERED: "delivered",
    READ: "read",
    FAILED: "failed",
  };

  // Forward-only receipt ordering. failed is a side state: it may go back to
  // sending (retry) or straight to sent (late ACK), and it may only be
  // entered from sending — a server-persisted (sent) message never fails.
  var MESSAGE_TRANSITIONS = {
    sending: { sent: true, failed: true },
    failed: { sending: true, sent: true },
    sent: { delivered: true, read: true },
    delivered: { read: true },
    read: {},
  };

  function canTransitionMessage(from, to) {
    if (from === to) return true;
    return !!(MESSAGE_TRANSITIONS[from] && MESSAGE_TRANSITIONS[from][to]);
  }

  function defaultTimers() {
    return {
      set: function (fn, ms) { return setTimeout(fn, ms); },
      clear: function (id) { clearTimeout(id); },
    };
  }

  function createConnection(opts) {
    var o = Object.assign(
      {
        url: "",
        heartbeatInterval: 25000,
        pongTimeout: 10000,
        baseBackoff: 500,
        maxBackoff: 25000,
        socketFactory: null,
        onState: function () {},
        onEvent: function () {},
        onSendPing: null, // return the ping envelope to send
        timers: defaultTimers(),
        now: function () { return Date.now(); },
        random: Math.random,
      },
      opts || {}
    );

    var state = CONNECTION_STATES.DISCONNECTED;
    var socket = null;
    var attempts = 0;
    var reconnectTimer = null;
    var heartbeatTimer = null;
    var pongTimer = null;
    var intentionallyClosed = false;
    var pendingOutgoing = null; // heartbeat ping while awaiting pong

    function setState(next, info) {
      if (state === next) return;
      state = next;
      try { o.onState(next, info || {}); } catch (e) { /* observer errors */ }
    }

    function clearHeartbeat() {
      if (heartbeatTimer) { o.timers.clear(heartbeatTimer); heartbeatTimer = null; }
      if (pongTimer) { o.timers.clear(pongTimer); pongTimer = null; }
      pendingOutgoing = null;
    }

    function armHeartbeat() {
      clearHeartbeat();
      heartbeatTimer = o.timers.set(function tick() {
        if (!socket || socket.readyState !== 1 /* OPEN */) return;
        var ping = { event: "connection.ping", request_id: "hb-" + o.now() };
        if (o.onSendPing) ping = o.onSendPing(ping) || ping;
        try { socket.send(JSON.stringify(ping)); } catch (e) { return; }
        pendingOutgoing = ping;
        pongTimer = o.timers.set(function () {
          // No pong inside the window: kill the socket; the close handler
          // starts the backoff loop.
          try { socket.terminate && socket.terminate(); } catch (e) {}
          try { socket.close(); } catch (e) {}
        }, o.pongTimeout);
      }, o.heartbeatInterval);
    }

    function scheduleReconnect() {
      setState(CONNECTION_STATES.RECONNECTING, { attempts: attempts });
      var base = Math.min(o.baseBackoff * Math.pow(2, attempts - 1), o.maxBackoff);
      var jitter = base * 0.25 * (o.random() * 2 - 1);
      // Cap AFTER jitter so the worst case never exceeds maxBackoff.
      var delay = Math.min(Math.max(100, Math.round(base + jitter)), o.maxBackoff);
      reconnectTimer = o.timers.set(function () { connect(); }, delay);
    }

    function connect() {
      if (reconnectTimer) { o.timers.clear(reconnectTimer); reconnectTimer = null; }
      if (state === CONNECTION_STATES.ONLINE) return;

      intentionallyClosed = false;
      setState(CONNECTION_STATES.CONNECTING);
      attempts += 1;

      socket = o.socketFactory ? o.socketFactory(o.url) : new WebSocket(o.url);

      socket.onopen = function () {
        attempts = 0;
        setState(CONNECTION_STATES.ONLINE);
        armHeartbeat();
      };
      socket.onmessage = function (evt) {
        var envelope = evt && evt.data;
        if (typeof envelope === "string") {
          try { envelope = JSON.parse(envelope); } catch (e) { return; }
        }
        if (envelope && envelope.event === "connection.pong") {
          clearHeartbeat();
          armHeartbeat();
        }
        try { o.onEvent(envelope); } catch (e) { /* observer errors */ }
      };
      socket.onclose = function (evt) {
        clearHeartbeat();
        var code = evt && evt.code;
        if (code === 4401) {
          setState(CONNECTION_STATES.AUTH_REQUIRED, { code: code });
          return;
        }
        if (code === 4003) {
          setState(CONNECTION_STATES.UNSUPPORTED, { code: code });
          return;
        }
        if (intentionallyClosed) {
          setState(CONNECTION_STATES.DISCONNECTED, { code: code });
          return;
        }
        scheduleReconnect();
      };
      socket.onerror = function () { /* close handler owns recovery */ };
    }

    return {
      STATES: CONNECTION_STATES,
      connect: connect,
      send: function (obj) {
        if (!socket || socket.readyState !== 1) return false;
        try {
          socket.send(JSON.stringify(obj));
          return true;
        } catch (e) {
          return false;
        }
      },
      disconnect: function () {
        intentionallyClosed = true;
        clearHeartbeat();
        if (reconnectTimer) { o.timers.clear(reconnectTimer); reconnectTimer = null; }
        try { socket && socket.close(); } catch (e) {}
        setState(CONNECTION_STATES.DISCONNECTED);
      },
      networkUp: function () {
        // Immediate attempt when the network or the tab comes back.
        if (state === CONNECTION_STATES.RECONNECTING ||
            state === CONNECTION_STATES.DISCONNECTED) {
          if (reconnectTimer) { o.timers.clear(reconnectTimer); reconnectTimer = null; }
          connect();
        }
      },
      isOnline: function () { return state === CONNECTION_STATES.ONLINE; },
      state: function () { return state; },
      socket: function () { return socket; },
    };
  }

  function createOutbox(opts) {
    var o = Object.assign(
      {
        ackTimeout: 10000,
        maxAttempts: 5,
        timers: defaultTimers(),
        now: function () { return Date.now(); },
        onDeliver: null,  // (envelope) -> bool  (ws send); required
        onStatus: null,   // (entry, nextStatus)
      },
      opts || {}
    );

    var entries = {}; // client_message_id -> entry

    function setStatus(entry, next) {
      if (!canTransitionMessage(entry.status, next)) return false;
      entry.status = next;
      if (o.onStatus) {
        try { o.onStatus(entry, next); } catch (e) { /* observer errors */ }
      }
      return true;
    }

    function armAckTimer(entry) {
      if (entry.ackTimer) o.timers.clear(entry.ackTimer);
      entry.ackTimer = o.timers.set(function () {
        // Unknown outcome (ACK lost): keep the envelope for retry with the
        // SAME client_message_id; a late accepted() revives it to sent.
        setStatus(entry, MESSAGE_STATES.FAILED);
        entry.ackTimedOut = true;
      }, o.ackTimeout);
    }

    return {
      STATES: MESSAGE_STATES,
      canTransitionMessage: canTransitionMessage,
      track: function (clientMessageId, envelope, meta) {
        // Creates/updates the entry WITHOUT arming the ACK timer - the
        // timer is armed only when the envelope reaches a transport (the
        // ws path calls armAck; the http path resolves via accepted()).
        var entry = entries[clientMessageId];
        if (!entry) {
          entry = {
            id: clientMessageId,
            envelope: envelope,
            meta: meta || {},
            status: MESSAGE_STATES.SENDING,
            attempts: 0,
            createdAt: o.now(),
          };
          entries[clientMessageId] = entry;
        }
        setStatus(entry, MESSAGE_STATES.SENDING);
        return entry;
      },
      armAck: function (clientMessageId) {
        var entry = entries[clientMessageId];
        if (entry) {
          entry.attempts += 1; // the initial transport attempt
          armAckTimer(entry);
        }
      },
      accepted: function (clientMessageId, payload) {
        var entry = entries[clientMessageId];
        if (!entry) return false;
        if (entry.ackTimer) { o.timers.clear(entry.ackTimer); entry.ackTimer = null; }
        entry.ackTimedOut = false;
        setStatus(entry, MESSAGE_STATES.SENT);
        return true;
      },
      failed: function (clientMessageId, error) {
        var entry = entries[clientMessageId];
        if (!entry) return false;
        if (entry.ackTimer) { o.timers.clear(entry.ackTimer); entry.ackTimer = null; }
        entry.error = error || entry.error;
        return setStatus(entry, MESSAGE_STATES.FAILED);
      },
      receipt: function (clientMessageId, status) {
        var entry = entries[clientMessageId];
        if (!entry) return false;
        return setStatus(entry, status);
      },
      // Re-deliver every entry that has not been confirmed, with the SAME
      // client_message_id (server-side idempotency makes this safe).
      resendPending: function () {
        var resent = [];
        Object.keys(entries).forEach(function (id) {
          var entry = entries[id];
          if (entry.status === MESSAGE_STATES.SENT ||
              entry.status === MESSAGE_STATES.DELIVERED ||
              entry.status === MESSAGE_STATES.READ) return;
          if (entry.attempts >= o.maxAttempts) return;
          if (o.onDeliver && o.onDeliver(entry.envelope, entry)) {
            entry.attempts += 1;
            setStatus(entry, MESSAGE_STATES.SENDING);
            armAckTimer(entry);
            resent.push(entry);
          }
        });
        return resent;
      },
      get: function (id) { return entries[id]; },
      all: function () { return Object.keys(entries).map(function (k) { return entries[k]; }); },
    };
  }

  // Serialized message-application queue: realtime pushes and sync items
  // funnel through one chain so a slow decrypt cannot reorder messages.
  function createApplyQueue() {
    var chain = Promise.resolve();
    return {
      enqueue: function (taskFn) {
        var run = chain.then(taskFn);
        chain = run.catch(function () {});
        return run;
      },
    };
  }

  // Sync walk: applies pages in order and only advances the stored cursor
  // after every item of a page was applied. An item that fails stops the
  // walk with the PREVIOUS cursor intact (at-least-once); an expired-cursor
  // error clears storage and re-snapshots once; pages with no visible items
  // still advance the walk while the server reports has_more.
  // Per-conversation seen-message registry shared by the sync walker and
  // the realtime handlers, so a message delivered on BOTH channels is only
  // counted once for background conversations (unread badges).
  function createSeenRegistry(maxPerConversation) {
    var byConv = {};
    var cap = maxPerConversation || 500;
    return {
      seen: function (convId, messageId) {
        var set = byConv[convId];
        return !!(set && set.has(messageId));
      },
      mark: function (convId, messageId) {
        var set = byConv[convId];
        if (!set) { set = new Set(); byConv[convId] = set; }
        if (set.size >= cap) {
          var first = set.values().next().value;
          set.delete(first);
        }
        set.add(messageId);
      },
    };
  }

  // Insertion index that keeps a chronologically ordered list stable even
  // when a paused sync walk resumes and applies older messages after newer
  // realtime ones (review round 6). ISO-8601 strings compare chronologically.
  function timelineInsertIndex(list, item) {
    // Stable key: (created_at, id) — matches the history API ordering and
    // keeps same-timestamp messages in id order regardless of arrival.
    var idx = list.length;
    while (idx > 0) {
      var prev = list[idx - 1];
      if (String(prev.created_at) > String(item.created_at)) { idx -= 1; continue; }
      if (String(prev.created_at) < String(item.created_at)) break;
      if ((prev.id || 0) > (item.id || 0)) { idx -= 1; continue; }
      break;
    }
    return idx;
  }

  // Move the COMPLETE message row (not just the bubble child) of newId
  // before the row of followingId (review round 6: moving the bubble child
  // nested old messages inside the newer message's row, so context menus
  // and selection targeted the wrong message).
  function tagMessageRows() {
    // Give every bubble-bearing row a stable row-level id so recalled and
    // decrypt-failed rows (no bubble) never break row lookups. Rows without
    // any bubble stay untagged; callers tag those directly on append.
    var rows = document.querySelectorAll(".message-row:not([data-row-message-id])");
    rows.forEach(function (row) {
      var bubble = row.querySelector("[data-message-id]");
      if (bubble) row.dataset.rowMessageId = bubble.getAttribute("data-message-id");
    });
  }

  function repositionMessageRow(newId, followingId) {
    if (typeof document === "undefined") return false;
    tagMessageRows();
    var row = document.querySelector('[data-row-message-id="' + newId + '"]');
    if (!row) return false;
    var nextRow = followingId != null
      ? document.querySelector('[data-row-message-id="' + followingId + '"]')
      : null;
    if (nextRow) {
      if (row === nextRow) return false;
      nextRow.parentNode.insertBefore(row, nextRow); // before successor
    } else {
      // No successor (or unlocatable): move the row to the container tail.
      row.parentNode.appendChild(row);
    }
    return true;
  }

  function createSyncWalker(opts) {
    var o = Object.assign(
      {
        fetchPage: function () {},
        applyItem: function () { return true; },
        storage: { get: function () { return ""; }, set: function () {}, remove: function () {} },
        applyQueue: null,
        maxPages: 100,
      },
      opts || {}
    );

    function run() {
      // The ENTIRE walk runs as one apply-queue task: pages of the same
      // snapshot can never be interleaved with realtime pushes (review
      // round 3: a newer realtime message used to slip between pages).
      var start = function () { return walkFrom(o.storage.get() || ""); };
      return o.applyQueue ? o.applyQueue.enqueue(start) : start();
    }

    function walkFrom(initialCursor) {
      var cursor = initialCursor;
      var pages = 0;
      var expiredRetry = false;

      function step() {
        if (pages >= o.maxPages) {
          return Promise.resolve({ status: "paused", pages: pages });
        }
        pages += 1;
        return Promise.resolve()
          .then(function () { return o.fetchPage(cursor); })
          .then(function (page) {
            if (page.errorCode === "sync_cursor_expired" && !expiredRetry) {
              // Snapshot lost: clear the stored token and re-snapshot once.
              o.storage.remove();
              cursor = "";
              expiredRetry = true;
              pages -= 1;
              return step();
            }
            if (page.errorCode) {
              return { status: "error", errorCode: page.errorCode, pages: pages };
            }
            var index = 0;
            function applyNext() {
              if (index >= page.items.length) return Promise.resolve(true);
              var item = page.items[index];
              index += 1;
              return Promise.resolve()
                .then(function () { return o.applyItem(item); })
                .then(function (ok) {
                  if (ok === false) return false;
                  return applyNext();
                });
            }
            // NOTE: no page-level enqueue here - the whole walk already
            // occupies one apply-queue task, and re-enqueueing a page into
            // the SAME queue deadlocks (walk waits for page, page waits
            // for walk). Realtime pushes enqueued meanwhile land AFTER the
            // entire walk, which preserves timeline order.
            return applyNext().then(function (allApplied) {
              if (!allApplied) {
                // Keep the PREVIOUS cursor: the failing item is re-fetched
                // and re-applied on the next run (at-least-once).
                return { status: "stopped", pages: pages };
              }
              o.storage.set(page.nextCursor);
              cursor = page.nextCursor;
              if (!page.hasMore) return { status: "caught-up", pages: pages };
              return step();
            });
          });
      }
      return step();
    }

    return { run: run };
  }

  return {
    STATES: CONNECTION_STATES,
    MESSAGE_STATES: MESSAGE_STATES,
    MESSAGE_TRANSITIONS: MESSAGE_TRANSITIONS,
    canTransitionMessage: canTransitionMessage,
    createConnection: createConnection,
    createOutbox: createOutbox,
    createSyncWalker: createSyncWalker,
    createApplyQueue: createApplyQueue,
    createSeenRegistry: createSeenRegistry,
    timelineInsertIndex: timelineInsertIndex,
    repositionMessageRow: repositionMessageRow,
  };
});
