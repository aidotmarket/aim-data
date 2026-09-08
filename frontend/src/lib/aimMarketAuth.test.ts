import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { startAuth, completeAuth } from "./aimMarketAuth";

const pair = { access_token: "new-access", refresh_token: "new-refresh", auth_mode: "oauth",
  user: { id: "account-a", role: "user" }, registration_status: "not_ready" };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
beforeEach(() => {
  localStorage.clear();
  let tail = Promise.resolve();
  vi.stubGlobal("navigator", { locks: { request: vi.fn((_name, operation) => {
    const next = tail.then(operation);
    tail = next.catch(() => {});
    return next;
  }) } });
});
afterEach(() => vi.unstubAllGlobals());

it("refresh_single_flight_across_tabs", async () => {
  const first = await import("./api");
  first.storeAuthTokens(pair as never);
  vi.resetModules(); // Independent module realm shares only origin storage and lock manager.
  const second = await import("./api");
  const fetcher = vi.fn(async (_url: string, _options: RequestInit) => response({ ...pair, access_token: "rotated", refresh_token: "successor" }));
  vi.stubGlobal("fetch", fetcher);
  await Promise.all([first.refreshAuth("new-access"), first.refreshAuth("new-access"), second.refreshAuth("new-access")]);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(navigator.locks.request).toHaveBeenCalledTimes(2);
  expect(JSON.parse(fetcher.mock.calls[0][1].body as string)).toEqual({ refresh_token: "new-refresh", auth_mode: "oauth" });
  expect(localStorage.getItem("aim_data_refresh_token")).toBe("successor");
});

it("mode_legacy_account_switch_and_logout", async () => {
  const api = await import("./api");
  expect(api.getAuthMode()).toBe("password");
  api.storeAuthTokens(pair as never);
  api.storeAuthTokens({ ...pair, auth_mode: undefined, user: { id: "account-b" } } as never);
  expect(api.getAuthMode()).toBe("password");
  api.clearAuthTokens();
  expect(localStorage.length).toBe(0);
  localStorage.setItem("aim_data_auth_mode", "forged");
  expect(() => api.getAuthMode()).toThrow("Unknown sign-in mode");
  expect(localStorage.length).toBe(0);
  expect(() => api.storeAuthTokens({ ...pair, refresh_token: "null" } as never)).toThrow();
  expect(localStorage.length).toBe(0);
});

it.each([429, 502, 503, 504, "network"])("transient_refresh_does_not_clear_auth (%s)", async status => {
  const api = await import("./api");
  api.storeAuthTokens(pair as never);
  const fetcher = vi.fn(async () => {
    if (status === "network") throw new TypeError("offline");
    return response({ error_code: "upstream_unavailable" }, status as number);
  });
  vi.stubGlobal("fetch", fetcher);
  await expect(api.refreshAuth()).rejects.toThrow();
  expect(localStorage.getItem("aim_data_access_token")).toBe("new-access");
  expect(api.getAuthMode()).toBe("oauth");
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it.each([401, 403])("terminal refresh clears auth (%s)", async status => {
  const api = await import("./api");
  api.storeAuthTokens(pair as never);
  vi.stubGlobal("fetch", vi.fn(async () => response({ error_code: "client_disabled" }, status)));
  await expect(api.refreshAuth()).rejects.toThrow("Provider-only");
  expect(localStorage.length).toBe(0);
});

it("logout or account switch during refresh cannot restore the old session", async () => {
  const api = await import("./api");
  api.storeAuthTokens(pair as never);
  let finish!: (value: Response) => void;
  vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(resolve => { finish = resolve; })));
  const refresh = api.refreshAuth();
  await vi.waitFor(() => expect(finish).toBeDefined());
  api.clearAuthTokens();
  finish(response(pair));
  await expect(refresh).rejects.toThrow("Account changed");
  expect(localStorage.length).toBe(0);
});

it("no_credentials_in_navigation", async () => {
  const bootstrap = { enabled: true, reason: null, csrf_nonce: "memory-only", loopback_origin: location.origin };
  const url = "https://api.ai.market/api/v1/oauth/authorize?state=opaque&code_challenge=hashed";
  const fetcher = vi.fn().mockResolvedValueOnce(response({ authorization_url: url }))
    .mockResolvedValueOnce(response(bootstrap)).mockResolvedValueOnce(response(pair));
  vi.stubGlobal("fetch", fetcher);
  expect(await startAuth(bootstrap)).toBe(url);
  expect(await completeAuth()).toEqual(pair);
  expect(fetcher.mock.calls.map(call => call[0])).toEqual([
    "/api/auth/aim-market/start", "/api/auth/aim-market/bootstrap", "/api/auth/aim-market/complete",
  ]);
  expect(localStorage.length).toBe(0);
  expect(JSON.stringify(fetcher.mock.calls)).not.toMatch(/new-access|new-refresh|code_verifier/);
});

it("legacy refresh explicitly uses password mode", async () => {
  const api = await import("./api");
  localStorage.setItem("aim_data_access_token", "old");
  localStorage.setItem("aim_data_refresh_token", "legacy-cookie");
  const fetcher = vi.fn(async (_url, _options) => response({ ...pair, auth_mode: "password" }));
  vi.stubGlobal("fetch", fetcher);
  await api.refreshAuth();
  expect(JSON.parse(fetcher.mock.calls[0][1].body).auth_mode).toBe("password");
  expect(api.getAuthMode()).toBe("password");
});

it("unavailable coordination fails closed without a refresh transport", async () => {
  const api = await import("./api");
  api.storeAuthTokens(pair as never);
  vi.stubGlobal("navigator", {});
  vi.stubGlobal("indexedDB", undefined);
  const fetcher = vi.fn();
  vi.stubGlobal("fetch", fetcher);
  await expect(api.refreshAuth()).rejects.toThrow();
  expect(fetcher).not.toHaveBeenCalled();
  expect(localStorage.getItem("aim_data_refresh_token")).toBe("new-refresh");
});

it("serial fallback queues independent tabs and stores no lock values", async () => {
  vi.stubGlobal("navigator", {});
  // Model IndexedDB's cross-connection readwrite transaction scheduling.
  let tail = Promise.resolve();
  const transactions: string[] = [];
  const database = { close: vi.fn(), transaction: (_store, mode) => {
    transactions.push(mode);
    const previous = tail;
    let release!: () => void;
    tail = new Promise<void>(resolve => { release = resolve; });
    let pending = 0;
    const tx = { oncomplete: () => {}, objectStore: () => ({ get: () => {
      const request = { onsuccess: () => {} };
      pending++;
      void previous.then(() => setTimeout(() => {
        pending--;
        request.onsuccess();
        if (!pending) { tx.oncomplete(); release(); }
      }, 0));
      return request;
    } }) };
    return tx;
  } };
  vi.stubGlobal("indexedDB", { open: () => {
    const request = { result: database, onsuccess: () => {} };
    queueMicrotask(() => request.onsuccess());
    return request;
  } });
  const first = await import("./api");
  first.storeAuthTokens(pair as never);
  vi.resetModules();
  const second = await import("./api");
  const fetcher = vi.fn(async () => response({ ...pair, access_token: "rotated" }));
  vi.stubGlobal("fetch", fetcher);
  await Promise.all([first.refreshAuth("new-access"), second.refreshAuth("new-access")]);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(transactions).toEqual(["readwrite", "readwrite"]);
  expect(database.close).toHaveBeenCalledTimes(2);
});

it.each(["google", "github", undefined] as const)("startAuth preserves nonce with provider %s", async provider => {
  const bootstrap = { enabled: true, reason: null, csrf_nonce: "nonce", loopback_origin: location.origin };
  const fetcher = vi.fn().mockResolvedValue(response({ authorization_url: "https://api.ai.market/authorize" }));
  vi.stubGlobal("fetch", fetcher);
  await startAuth(bootstrap, provider);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({
    csrf_nonce: "nonce", ...(provider === undefined ? {} : { provider }),
  });
});
