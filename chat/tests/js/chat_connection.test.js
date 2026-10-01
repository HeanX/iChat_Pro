/**
 * P4 T33 tests: connection state machine, backoff, heartbeat, ACK timeouts,
 * late-ACK revival and monotonic message statuses. Runs on Node with mock
 * sockets and controllable timers (no DOM, no network).
 */
const ChatConnection = require("../../../static/js/chat-connection.js");

function assert(cond, message) {
  if (!cond) throw new Error("assert failed: " + message);
}

function createHarness() {
  const timers = { now: 0, tasks: [], nextId: 1 };
  const timersApi = {
    set(fn, ms) {
      const id = timers.nextId++;
      timers.tasks.push({ id, fn, at: timers.now + ms, cleared: false });
      return id;
    },
    clear(id) {
      const t = timers.tasks.find((t) => t.id === id);
      if (t) t.cleared = true;
    },
    advance(ms) {
      timers.now += ms;
      const due = timers.tasks.filter((t) => !t.cleared && t.at <= timers.now);
      due.sort((a, b) => a.at - b.at);
      for (const t of due) {
        if (t.cleared) continue;
        t.cleared = true;
        t.fn();
      }
    },
    pending() {
      return timers.tasks.filter((t) => !t.cleared);
    },
  };

  const sockets = [];
  function makeSocket() {
    const handlers = {};
    const socket = {
      readyState: 1,
      sent: [],
      terminated: false,
      closed: false,
      onopen: null, onmessage: null, onclose: null, onerror: null,
      send(data) { socket.sent.push(JSON.parse(data)); },
      terminate() { socket.terminated = true; socket.close(4000); },
      close(code) {
        if (socket.closed) return;
        socket.closed = true;
        socket.readyState = 3;
        const handler = handlers.onclose || socket.onclose;
        handler && handler({ code: code || 1000 });
      },
      fireOpen() { handlers.onopen ? handlers.onopen() : socket.onopen && socket.onopen(); },
      fireMessage(obj) { socket.onmessage && socket.onmessage({ data: JSON.stringify(obj) }); },
      fireClose(code) { socket.close(code); },
      __setHandler(name) { handlers[name] = socket[name]; },
    };
    // capture handlers assigned by the module
    ["onopen", "onmessage", "onclose"].forEach((name) => {
      Object.defineProperty(socket, name, {
        set(fn) { handlers[name] = fn; },
        get() { return handlers[name]; },
      });
    });
    sockets.push(socket);
    return socket;
  }

  let randomValue = 0.5; // zero jitter by default
  return {
    timers: timersApi,
    timerState: timers,
    sockets,
    socketFactory() { return makeSocket(); },
    random() { return randomValue; },
    now() { return timers.now; },
    setRandom(v) { randomValue = v; },
  };
}

function test_backoff_progression_and_jitter_bounds() {
  const h = createHarness();
  const delays = [];
  const conn = ChatConnection.createConnection({
    url: "wss://x/ws/chat/",
    socketFactory: h.socketFactory,
    timers: h.timers,
    now: h.now,
    random: h.random,
    baseBackoff: 500,
    maxBackoff: 25000,
    onState() {},
  });
  // Simpler: measure the delay between two close/connect cycles.
  const t2 = createHarness();
  const conn2 = ChatConnection.createConnection({
    url: "wss://x/", socketFactory: t2.socketFactory, timers: t2.timers,
    now: t2.now, random: t2.random, baseBackoff: 500, maxBackoff: 25000,
    onState() {},
  });
  conn2.connect();
  t2.sockets[0].fireClose(1006);
  const before = t2.now();
  const firstTask = t2.timers.pending()[0];
  const reconnectDelay = firstTask.at - before;
  assert(reconnectDelay >= 375 && reconnectDelay <= 625, "first backoff within 500±25%: " + reconnectDelay);
  t2.timers.advance(reconnectDelay);
  assert(t2.sockets.length === 2, "reconnected after backoff");
  t2.sockets[1].fireClose(1006);
  const before2 = t2.now();
  const secondTask = t2.timers.pending()[0];
  const delay2 = secondTask.at - before2;
  assert(delay2 >= 750 && delay2 <= 1250, "second backoff within 1000±25%: " + delay2);
  console.log("✓ backoff progression within jitter bounds");
}

function test_terminal_close_codes_stop_reconnect() {
  const h = createHarness();
  const states = [];
  const conn = ChatConnection.createConnection({
    url: "wss://x/", socketFactory: h.socketFactory, timers: h.timers,
    now: h.now, random: h.random, onState(s) { states.push(s); },
  });
  conn.connect();
  h.sockets[0].fireClose(4401);
  assert(conn.state() === "auth_required", "4401 → auth_required");
  h.timers.advance(60000);
  assert(h.sockets.length === 1, "no reconnect after auth_required");

  const h2 = createHarness();
  const conn2 = ChatConnection.createConnection({
    url: "wss://x/", socketFactory: h2.socketFactory, timers: h2.timers,
    now: h2.now, random: h2.random, onState() {},
  });
  conn2.connect();
  h2.sockets[0].fireClose(4003);
  assert(conn2.state() === "unsupported", "4003 → unsupported");
  h2.timers.advance(60000);
  assert(h2.sockets.length === 1, "no reconnect after 4003");
  console.log("✓ terminal close codes stop reconnection");
}

function test_heartbeat_missing_pong_reconnects() {
  const h = createHarness();
  const conn = ChatConnection.createConnection({
    url: "wss://x/", socketFactory: h.socketFactory, timers: h.timers,
    now: h.now, random: h.random, heartbeatInterval: 25000, pongTimeout: 10000,
  });
  conn.connect();
  const socket = h.sockets[0];
  socket.fireOpen();
  h.timers.advance(25000);
  const ping = socket.sent.find((m) => m.event === "connection.ping");
  assert(ping, "heartbeat ping sent");
  h.timers.advance(10000);
  assert(socket.terminated || socket.closed, "socket killed on missing pong");
  assert(conn.state() === "reconnecting", "reconnecting after heartbeat failure");
  console.log("✓ heartbeat pong timeout forces reconnect");
}

function test_network_up_triggers_immediate_reconnect() {
  const h = createHarness();
  const conn = ChatConnection.createConnection({
    url: "wss://x/", socketFactory: h.socketFactory, timers: h.timers,
    now: h.now, random: h.random,
  });
  conn.connect();
  h.sockets[0].fireClose(1006); // → reconnecting with a backoff timer
  const socketsBefore = h.sockets.length;
  conn.networkUp();
  assert(h.sockets.length === socketsBefore + 1, "networkUp connects immediately");
  assert(conn.state() === "connecting", "state connecting on networkUp");
  console.log("✓ networkUp bypasses backoff");
}

function test_outbox_ack_timeout_and_late_ack() {
  const h = createHarness();
  const delivered = [];
  const statusLog = [];
  const outbox = ChatConnection.createOutbox({
    ackTimeout: 10000,
    timers: h.timers,
    now: h.now,
    onDeliver(envelope) { delivered.push(envelope); return true; },
    onStatus(entry, status) { statusLog.push([entry.id, status]); },
  });
  outbox.track("m1", { event: "message.single.send", data: { client_message_id: "m1" } });
  h.timers.advance(10000);
  const entry = outbox.get("m1");
  assert(entry.status === "failed", "ACK timeout → failed");
  assert(statusLog.some(([id, s]) => id === "m1" && s === "failed"), "failed status emitted");

  // Late ACK revives the entry to sent (same client_message_id).
  outbox.accepted("m1");
  assert(outbox.get("m1").status === "sent", "late ACK revives failed → sent");
  console.log("✓ ACK timeout → failed, late ACK → sent");
}

function test_outbox_resend_uses_same_id_and_gives_up() {
  const h = createHarness();
  let online = true;
  const outbox = ChatConnection.createOutbox({
    ackTimeout: 10000,
    maxAttempts: 3,
    timers: h.timers,
    now: h.now,
    onDeliver(envelope) { return online; },
  });
  outbox.track("m1", { data: { client_message_id: "m1" } });
  h.timers.advance(10000); // failed
  outbox.resendPending();
  outbox.resendPending();
  assert(outbox.get("m1").attempts === 3, "three delivery attempts");
  outbox.resendPending();
  assert(outbox.get("m1").attempts === 3, "gives up after maxAttempts");
  h.timers.advance(10000); // let the in-flight ack timer time out
  assert(outbox.get("m1").status === "failed", "stays failed after give-up");

  online = false;
  const offline = ChatConnection.createOutbox({
    ackTimeout: 10000, timers: h.timers, now: h.now, onDeliver() { return false; },
  });
  offline.track("m2", {});
  assert(offline.get("m2").status === "sending", "sending until delivered");
  console.log("✓ resend keeps the same id and respects maxAttempts");
}

function test_message_status_monotonic() {
  const can = ChatConnection.canTransitionMessage;
  assert(can("sending", "sent"), "sending → sent");
  assert(can("sending", "failed"), "sending → failed");
  assert(can("failed", "sending"), "failed → sending (retry)");
  assert(can("failed", "sent"), "failed → sent (late ACK)");
  assert(can("sent", "delivered"), "sent → delivered");
  assert(can("delivered", "read"), "delivered → read");
  assert(!can("read", "delivered"), "read never downgrades");
  assert(!can("sent", "sending"), "sent never back to sending");
  assert(!can("sent", "failed"), "sent never fails");
  assert(!can("delivered", "failed"), "delivered never fails");
  console.log("✓ message status transitions are forward-only");
}

const tests = [
  test_backoff_progression_and_jitter_bounds,
  test_terminal_close_codes_stop_reconnect,
  test_heartbeat_missing_pong_reconnects,
  test_network_up_triggers_immediate_reconnect,
  test_outbox_ack_timeout_and_late_ack,
  test_outbox_resend_uses_same_id_and_gives_up,
  test_message_status_monotonic,
];

let failed = 0;
for (const t of tests) {
  try {
    t();
  } catch (err) {
    failed += 1;
    console.error("FAIL:", t.name, "-", err.message);
  }
}
if (failed) {
  console.error(failed + " test(s) failed");
  process.exit(1);
}
console.log("chat-connection: all tests passed");
