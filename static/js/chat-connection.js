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
      var delay = Math.max(100, Math.round(base + jitter));
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
        entry.attempts += 1;
        setStatus(entry, MESSAGE_STATES.SENDING);
        armAckTimer(entry);
        return entry;
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

  return {
    STATES: CONNECTION_STATES,
    MESSAGE_STATES: MESSAGE_STATES,
    MESSAGE_TRANSITIONS: MESSAGE_TRANSITIONS,
    canTransitionMessage: canTransitionMessage,
    createConnection: createConnection,
    createOutbox: createOutbox,
  };
});
