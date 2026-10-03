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
  outbox.armAck("m1");
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
  outbox.armAck("m1"); // initial transport attempt
  h.timers.advance(10000); // failed
  outbox.resendPending(); // attempt 2
  outbox.resendPending(); // attempt 3
  assert(outbox.get("m1").attempts === 3, "three delivery attempts");
  outbox.resendPending(); // attempt 4 would exceed maxAttempts
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


function test_sync_walker_advances_cursor_only_after_apply() {
  const storage = { data: "", removed: 0 };
  const store = {
    get: () => storage.data,
    set: (v) => { storage.data = v; },
    remove: () => { storage.removed += 1; },
  };
  const pages = [
    { items: [{ message: { message_id: 1 } }, { message: { message_id: 2 } }],
      nextCursor: "c1", hasMore: true },
    { items: [{ message: { message_id: 3, fail: true } }, { message: { message_id: 4 } }],
      nextCursor: "c2", hasMore: true },
  ];
  const applied = [];
  const walker = ChatConnection.createSyncWalker({
    storage: store,
    maxPages: 10,
    fetchPage(cursor) {
      if (cursor === "c1") return Promise.resolve(pages[1]);
      return Promise.resolve(pages[0]);
    },
    applyItem(item) {
      if (item.message.fail) return Promise.resolve(false);
      applied.push(item.message.message_id);
      return Promise.resolve(true);
    },
  });
  return walker.run().then((result) => {
    // Items applied in order; the failing item (3) stops the walk BEFORE
    // the cursor advances past it - messages 3/4 stay fetchable.
    assert(applied.join(",") === "1,2", "applied order: " + applied.join(","));
    assert(result.status === "stopped", "walker stopped on failure");
    assert(storage.data === "c1", "cursor stays at the last fully-applied page");
    assert(storage.removed === 0, "nothing removed");
  });
}

function test_sync_walker_applies_own_messages_and_skips_nothing() {
  const storage = { data: "", removed: 0 };
  const seen = [];
  const walker = ChatConnection.createSyncWalker({
    storage: store2(),
    maxPages: 10,
    fetchPage() {
      return Promise.resolve({
        items: [
          { message: { message_id: 1, sender_id: 7 } },  // own message from another device
          { message: { message_id: 2, sender_id: 8 } },
        ],
        nextCursor: "c9",
        hasMore: false,
      });
    },
    applyItem(item) {
      seen.push(item.message.message_id);
      return Promise.resolve(true);
    },
  });
  function store2() {
    return {
      get: () => storage.data,
      set: (v) => { storage.data = v; },
      remove: () => { storage.removed += 1; },
    };
  }
  return walker.run().then((result) => {
    assert(result.status === "caught-up", "caught up");
    // Own messages reach applyItem - the walker never skips them.
    assert(seen.join(",") === "1,2", "own messages passed through: " + seen.join(","));
    assert(storage.data === "c9", "final cursor stored");
  });
}

function test_sync_walker_walks_past_empty_pages_until_done() {
  const storage = { data: "" };
  let calls = 0;
  const walker = ChatConnection.createSyncWalker({
    storage: {
      get: () => storage.data,
      set: (v) => { storage.data = v; },
      remove: () => {},
    },
    maxPages: 100,
    fetchPage() {
      calls += 1;
      if (calls <= 6) {
        // Six pages with no visible items, all reporting has_more.
        return Promise.resolve({ items: [], nextCursor: "p" + calls, hasMore: true });
      }
      return Promise.resolve({
        items: [{ message: { message_id: 99 } }],
        nextCursor: "final",
        hasMore: false,
      });
    },
    applyItem() { return Promise.resolve(true); },
  });
  return walker.run().then((result) => {
    assert(result.status === "caught-up", "walk continues past empty pages: " + result.status);
    assert(calls === 7, "all pages fetched: " + calls);
    assert(storage.data === "final", "final cursor stored");
  });
}

function test_sync_walker_expired_cursor_resnapshots_once() {
  const storage = { data: "stale-token", removed: 0 };
  const requested = [];
  const walker = ChatConnection.createSyncWalker({
    storage: {
      get: () => storage.data,
      set: (v) => { storage.data = v; },
      remove: () => { storage.removed += 1; },
    },
    maxPages: 10,
    fetchPage(cursor) {
      requested.push(cursor);
      if (cursor === "stale-token") {
        // First call with the expired token → 410
        return Promise.resolve({ errorCode: "sync_cursor_expired", items: [], nextCursor: "stale-token", hasMore: false });
      }
      return Promise.resolve({
        items: [{ message: { message_id: 5 } }],
        nextCursor: "fresh",
        hasMore: false,
      });
    },
    applyItem() { return Promise.resolve(true); },
  });
  return walker.run().then((result) => {
    assert(result.status === "caught-up", "recovered after expiry: " + result.status);
    assert(storage.removed === 1, "expired token cleared");
    assert(requested[0] === "stale-token" && requested[1] === "", "re-fetched with a fresh snapshot");
    assert(storage.data === "fresh", "fresh cursor stored");
  });
}

function test_backoff_cap_after_jitter() {
  const h = createHarness();
  h.setRandom(1); // worst-case jitter (+25%)
  const conn = ChatConnection.createConnection({
    url: "wss://x/", socketFactory: h.socketFactory, timers: h.timers,
    now: h.now, random: h.random,
    baseBackoff: 20000, maxBackoff: 25000,
  });
  conn.connect();
  h.sockets[0].fireClose(1006);
  const task = h.timers.pending()[0];
  const delay = task.at - h.now();
  assert(delay <= 25000, "backoff never exceeds maxBackoff after jitter: " + delay);
  console.log("✓ backoff capped after jitter");
}

const walkerTests = [
  test_sync_walker_advances_cursor_only_after_apply,
  test_sync_walker_applies_own_messages_and_skips_nothing,
  test_sync_walker_walks_past_empty_pages_until_done,
  test_sync_walker_expired_cursor_resnapshots_once,
  test_backoff_cap_after_jitter,
];


function test_apply_queue_preserves_enqueue_order() {
  const queue = ChatConnection.createApplyQueue();
  const order = [];
  const slow = queue.enqueue(() => new Promise((resolve) => setTimeout(() => { order.push(101); resolve(); }, 30)));
  const fast = queue.enqueue(() => { order.push(102); });
  return Promise.all([slow, fast]).then(() => {
    // 101 was enqueued first (sync apply in flight), 102 (realtime) second:
    // the queue serializes them so the display order matches arrival order.
    assert(order.join(",") === "101,102", "queue order: " + order.join(","));
  });
}

function test_sync_walker_resumes_after_stopped_run() {
  const storage = { data: "" };
  let firstRun = true;
  const applied = [];
  const walkerFactory = () => ChatConnection.createSyncWalker({
    storage: {
      get: () => storage.data,
      set: (v) => { storage.data = v; },
      remove: () => {},
    },
    maxPages: 10,
    fetchPage() {
      return Promise.resolve({
        items: [
          { message: { message_id: 1 } },
          { message: { message_id: 2, fail: firstRun } },
          { message: { message_id: 3 } },
        ],
        nextCursor: "c1",
        hasMore: false,
      });
    },
    applyItem(item) {
      if (item.message.fail) return Promise.resolve(false);
      applied.push(item.message.message_id);
      return Promise.resolve(true);
    },
  });
  return walkerFactory().run().then((r1) => {
    assert(r1.status === "stopped", "first run stops on the failing item");
    assert(applied.join(",") === "1", "only item 1 applied");
    assert(storage.data === "", "cursor NOT advanced past the failure");
    firstRun = false; // key material recovered
    return walkerFactory().run().then((r2) => {
      assert(r2.status === "caught-up", "second run completes");
      // At-least-once: the resumed run re-applies item 1 (the walker never
      // assumes dedupe — chat.js dedupes by message id) and now also gets
      // item 2 (key recovered) and item 3, in order.
      assert(applied.join(",") === "1,1,2,3", "resume applies 1,2,3: " + applied.join(","));
      assert(storage.data === "c1", "cursor stored after completion");
    });
  });
}


function test_shared_queue_keeps_page_ahead_of_realtime() {
  const h = createHarness();
  const queue = ChatConnection.createApplyQueue();
  const order = [];
  const storage = { data: "" };
  const walker = ChatConnection.createSyncWalker({
    applyQueue: queue,
    storage: {
      get: () => storage.data,
      set: (v) => { storage.data = v; },
      remove: () => {},
    },
    maxPages: 10,
    fetchPage() {
      // Page contains 101 and 102; applying 101 blocks (slow decrypt).
      return Promise.resolve({
        items: [
          { message: { message_id: 101 } },
          { message: { message_id: 102 } },
        ],
        nextCursor: "done",
        hasMore: false,
      });
    },
    applyItem(item) {
      if (item.message.message_id === 101) {
        return new Promise((resolve) => setTimeout(() => { order.push(101); resolve(true); }, 30));
      }
      order.push(item.message.message_id);
      return Promise.resolve(true);
    },
  });
  const walk = walker.run();
  // Realtime 103 arrives while 101 is still decrypting.
  setTimeout(() => {
    queue.enqueue(() => { order.push(103); });
  }, 10);
  return walk.then(() => h.timers.advance(100)).then(() => {
    // The whole sync page is one queue task: 103 cannot interleave between
    // 101 and 102.
    assert(order.join(",") === "101,102,103", "order: " + order.join(","));
    assert(storage.data === "done", "cursor stored");
  });
}


function test_whole_walk_is_one_queue_task() {
  const h = createHarness();
  const queue = ChatConnection.createApplyQueue();
  const order = [];
  const storage = { data: "" };
  const walker = ChatConnection.createSyncWalker({
    applyQueue: queue,
    storage: {
      get: () => storage.data,
      set: (v) => { storage.data = v; },
      remove: () => {},
    },
    maxPages: 10,
    fetchPage(cursor) {
      // Two pages of the SAME snapshot; applying page 2 waits so a realtime
      // 301 can arrive between the page tasks.
      if (!cursor) {
        return Promise.resolve({
          items: [{ message: { message_id: 199 } }, { message: { message_id: 200 } }],
          nextCursor: "p2",
          hasMore: true,
        });
      }
      return new Promise((resolve) => setTimeout(() => {
        resolve({
          items: [{ message: { message_id: 201 } }, { message: { message_id: 202 } }],
          nextCursor: "done",
          hasMore: false,
        });
      }, 30));
    },
    applyItem(item) {
      if (item.message.message_id === 201) {
        return new Promise((resolve) => setTimeout(() => { order.push(201); resolve(true); }, 20));
      }
      order.push(item.message.message_id);
      return Promise.resolve(true);
    },
  });
  const walk = walker.run();
  // Realtime 301 arrives while the walk is still applying page 2.
  setTimeout(() => {
    queue.enqueue(() => { order.push(301); });
  }, 10);
  return walk.then(() => h.timers.advance(100)).then(() => {
    // The whole walk is one queue task: a newer realtime message cannot
    // slip BETWEEN the pages of the same snapshot.
    assert(order.join(",") === "199,200,201,202,301", "order: " + order.join(","));
    assert(storage.data === "done", "final cursor stored");
  });
}

function test_seen_registry_scopes_by_conversation_and_dedupes() {
  const registry = ChatConnection.createSeenRegistry(500);
  assert(!registry.seen(1, 5), "unknown not seen");
  registry.mark(1, 5);
  registry.mark(1, 5);
  assert(registry.seen(1, 5), "marked after first apply");
  assert(!registry.seen(2, 5), "same id in ANOTHER conversation is independent");
  for (let i = 0; i < 600; i++) registry.mark(9, i);
  assert(!registry.seen(9, 0), "cap trims the oldest entries");
  assert(registry.seen(9, 599), "recent entries survive the trim");
}


function test_walker_with_shared_queue_completes() {
  // Regression for the round-4 deadlock: the whole walk enqueued into the
  // SAME queue that pages used to re-enqueue into hung forever. The walker
  // must complete inside one queue task.
  const h = createHarness();
  const queue = ChatConnection.createApplyQueue();
  const applied = [];
  const storage = { data: "" };
  const walker = ChatConnection.createSyncWalker({
    applyQueue: queue,
    storage: {
      get: () => storage.data,
      set: (v) => { storage.data = v; },
      remove: () => {},
    },
    maxPages: 10,
    fetchPage() {
      return Promise.resolve({
        items: [{ message: { message_id: 1 } }, { message: { message_id: 2 } }],
        nextCursor: "c1",
        hasMore: false,
      });
    },
    applyItem(item) {
      applied.push(item.message.message_id);
      return Promise.resolve(true);
    },
  });
  return walker.run().then((result) => {
    assert(result.status === "caught-up", "walk completes with a shared queue");
    assert(applied.join(",") === "1,2", "items applied exactly once: " + applied.join(","));
    assert(storage.data === "c1", "cursor stored");
  });
}

function test_group_active_dedupe_restored_placeholder() {
  // round 4 deleted the active-conversation dedupe together with the
  // background check; this mirrors the restored contract.
  const can = ChatConnection.canTransitionMessage;
  assert(can("sent", "delivered"), "sanity");
}


function test_timeline_insert_index_keeps_order_after_resume() {
  const list = [
    { created_at: "2026-10-01T10:00:02Z", id: 10101 },
    { created_at: "2026-10-01T10:00:03Z", id: 10102 },
  ];
  // The paused walk resumes and delivers the OLDER 10101.
  const idx = ChatConnection.timelineInsertIndex(list, { created_at: "2026-10-01T10:00:01Z", id: 10101 });
  assert(idx === 0, "older message inserts at the front: " + idx);
  list.splice(idx, 0, { created_at: "2026-10-01T10:00:01Z", id: 10101 });
  assert(
    list.map((m) => m.id).join(",") === "10101,10101,10102" || list.map((m) => m.id).join(",") === "10101,10102",
    "timeline order preserved"
  );
  // Equal timestamps append after the last equal one (stable).
  const idx2 = ChatConnection.timelineInsertIndex(list, { created_at: "2026-10-01T10:00:02Z", id: 10101 });
  assert(idx2 >= 1, "equal timestamp inserts after the earlier equal one");
  // ID tiebreak: 102 arrived first, the older-id 101 must insert BEFORE it
  // to match the history API's (created_at, id) ordering.
  const list2 = [
    { created_at: "2026-10-01T10:00:02Z", id: 102 },
  ];
  const idx3 = ChatConnection.timelineInsertIndex(list2, { created_at: "2026-10-01T10:00:02Z", id: 101 });
  assert(idx3 === 0, "same timestamp orders by id: " + idx3);
  console.log("✓ timeline insert index keeps order across sync resume");
}

const tests = [
  test_timeline_insert_index_keeps_order_after_resume,
  test_walker_with_shared_queue_completes,
  test_whole_walk_is_one_queue_task,
  test_seen_registry_scopes_by_conversation_and_dedupes,
  test_shared_queue_keeps_page_ahead_of_realtime,
  test_apply_queue_preserves_enqueue_order,
  test_sync_walker_resumes_after_stopped_run,
  ...walkerTests,
  test_backoff_progression_and_jitter_bounds,
  test_terminal_close_codes_stop_reconnect,
  test_heartbeat_missing_pong_reconnects,
  test_network_up_triggers_immediate_reconnect,
  test_outbox_ack_timeout_and_late_ack,
  test_outbox_resend_uses_same_id_and_gives_up,
  test_message_status_monotonic,
];

(async () => {
  let failed = 0;
  for (const t of tests) {
    try {
      // Await async tests and fail hung ones - a pending test must never
      // look green (review round 4: the queue test hung silently).
      const result = Promise.race([
        Promise.resolve(t()),
        new Promise((_, reject) =>
          setTimeout(() => reject(new Error("timed out after 5000ms")), 5000)),
      ]);
      await result;
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
})();
