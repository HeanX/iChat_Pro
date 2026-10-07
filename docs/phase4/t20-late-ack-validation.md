# T20 late ACK validation

> 更新日期：2026-10-07；核对代码基线：`f2f60ec`。历史记录或原阶段设计；下文保留当时口径，不能作为现行完成声明。
> 当前状态见 [项目现状](../current-status.md)；文档用途与归档规则见 [文档维护索引](../documentation-status.md)。

## 本次更新

保留 2026-10-03 的复现、时序与边界。之后 PR #304 修复稳定行身份、晚 ACK 时间与排序，并加强接线回归/异步 runner；#147 已由最终断网/排序实测关闭。该任务通过不等于崩溃后内存待发箱恢复。

Date: 2026-10-03. Baseline: 745db6c (PR #303). Related issue: #147.

## Regression and fix

When catch-up inserts received messages before a delayed send is accepted, applying new array positions to the old DOM order replaces an unrelated row. The baseline reproduced array IDs [43,44,45] but DOM IDs [45,44,45].

The ACK handler now locates and retags the original temporary row, updates the array using the server timestamp, moves that complete row, and then rebuilds grouping at both its old and new positions. A missing row or successor triggers a complete timeline render. Row lookup stays within the active history container. All four changed renderer scripts use version 20261003-t20e.

## Automated checks

- Django: 404 tests passed, 2 PostgreSQL-only cases skipped locally on SQLite. PostgreSQL/Redis remains a separate CI job.
- Existing private/group E2EE, connection and desktop configuration suites passed.
- 12 ACK/DOM tests execute the production handler, neighbour patcher, grouping function and row movement helper against a small DOM fixture. They cover tail/front/middle/tied timestamp placement, repeated ACK, selection identity, missing/legacy/system rows, AI-container isolation and grouping at the old gap.
- 2 public-key cache tests cover both modules. Existing crypto tests exposed version substitution after rotation: explicit versions now require an exact cache hit, while versionless offline sends retain newest-key fallback. Explicitly trusted rotations update the send cache.
- The JavaScript command is now run by CI, with each new asynchronous test managed by Node's test runner.

## Real desktop and browser check

The existing packaged Windows desktop was connected to the real server with only the four renderer script responses replaced by this branch's files. CDP script hashes matched the local source. Production assets and the installer were not changed by this test.

1. Sent baseline message 46 to warm the public-key cache.
2. Blocked only the desktop's TLS transport; an offline send encrypted and entered its outbox with a temporary ID.
3. The second browser sent messages 47 and 48 to the real server.
4. Restored desktop transport while delaying its resend frame by 3 seconds. Connection returned in 9.241 seconds. Catch-up rendered both received messages while the send still had its temporary ID.
5. The real server accepted the send as message 49. The array and DOM both became [46,47,48,49], each marker occurring once. Temporary selection migrated to ID 49. Selecting received rows returned their correct IDs and contents.
6. Refreshed both clients: both retained [46,47,48,49] with no duplicate send. The receiver observed the resend once.
7. Closed all test applications and the dedicated transport relay; their processes and listening ports were absent. User data was preserved.

## Acceptance boundary

This validates candidate renderer code against a real server and real ACKs. It does not certify deployment or installation of a new build. Keep #147 open until the merged assets are deployed and the production cache version and smoke result are verified. The installer lifecycle under #142 is outside this change.
