/**
 * P4 T14 review: desktop app-config resolution (mode gating, origin
 * normalization). Pure Node - no DOM, no network.
 */
const { normalizeOrigin, resolveAppConfig } = require("../../../desktop/app-config.js");

function assert(cond, message) {
  if (!cond) throw new Error("assert failed: " + message);
}

const tests = [
  function test_cloud_origin_normalization() {
    const cfg = resolveAppConfig({ ICHAT_SERVER_URL: "HTTPS://SUB.20060810.XYZ:8443 " }, []);
    assert(cfg.mode === "cloud", "uppercase + spaces resolve to cloud");
    assert(cfg.origin === "https://sub.20060810.xyz:8443", "origin normalized: " + cfg.origin);

    const bare = resolveAppConfig({ ICHAT_SERVER_URL: "https://sub.20060810.xyz" }, []);
    assert(bare.origin === "https://sub.20060810.xyz", "bare https origin kept");

    // :443 (default https port) and uppercase hostname are the SAME origin.
    const a = resolveAppConfig({ ICHAT_SERVER_URL: "https://sub.example.com:443" }, []);
    const b = resolveAppConfig({ ICHAT_SERVER_URL: "https://SUB.example.com" }, []);
    assert(a.origin === b.origin, ":443 normalized equal: " + a.origin + " vs " + b.origin);

    // :80 default port normalized away (at the normalizeOrigin level; the
    // resolveAppConfig level additionally enforces the HTTPS contract).
    assert(normalizeOrigin("http://sub.example.com:80") === "http://sub.example.com", ":80 normalized away");
    // HTTPS contract: remote http rejected, loopback http allowed.
    const remoteHttp = resolveAppConfig({ ICHAT_SERVER_URL: "http://sub.example.com:8000" }, []);
    assert(remoteHttp.mode === "unconfigured", "remote http rejected");
    assert(remoteHttp.message.includes("HTTPS"), "rejection explains the HTTPS requirement");
    const loopbackHttp = resolveAppConfig({ ICHAT_SERVER_URL: "http://localhost:8000" }, []);
    assert(loopbackHttp.mode === "cloud", "loopback http allowed for local tests");
    console.log("✓ cloud origin normalization");
  },
  function test_mode_gating() {
    assert(resolveAppConfig({}, ["--dev"]).mode === "dev", "--dev → dev");
    assert(resolveAppConfig({ ICHAT_DEV: "1" }, []).mode === "dev", "ICHAT_DEV=1 → dev");
    assert(resolveAppConfig({}, []).mode === "unconfigured", "nothing → unconfigured");
    assert(resolveAppConfig({ ICHAT_SERVER_URL: "ftp://x" }, []).mode === "unconfigured", "bad scheme → unconfigured");
    assert(
      resolveAppConfig({ ICHAT_SERVER_URL: "https://sub.example.com:8443" }, ["--dev"]).mode === "cloud",
      "cloud wins over --dev",
    );
    console.log("✓ mode gating");
  },
];

(async () => {
  let failed = 0;
  for (const t of tests) {
    try {
      await Promise.race([
        Promise.resolve(t()),
        new Promise((_, reject) => setTimeout(() => reject(new Error("timed out")), 5000)),
      ]);
    } catch (err) {
      failed += 1;
      console.error("FAIL:", t.name, "-", err.message);
    }
  }
  if (failed) {
    console.error(failed + " test(s) failed");
    process.exit(1);
  }
  console.log("desktop-config: all tests passed");
})();
