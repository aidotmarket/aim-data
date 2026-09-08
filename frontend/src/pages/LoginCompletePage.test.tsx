import { StrictMode } from "react";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter, Routes, Route, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "@/contexts/AuthContext";
import { authApi, storeAuthTokens, clearAuthTokens } from "@/lib/api";
import LoginCompletePage from "./LoginCompletePage";
afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); });
const data = { access_token: "synthetic-access", refresh_token: "synthetic-refresh", auth_mode: "oauth",
  user: { id: "account", role: "user" }, registration_status: "not_ready" };
function Datasets() {
  const auth = useAuth();
  return <p>{useLocation().pathname}:{auth.user?.user_id}:{String(auth.isAuthenticated)}</p>;
}
it("complete_once_then_datasets", async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ csrf_nonce: "memory-only" })))
    .mockResolvedValueOnce(new Response(JSON.stringify(data)));
  vi.stubGlobal("fetch", fetcher);
  render(<StrictMode><MemoryRouter initialEntries={["/login/complete"]}><AuthProvider><Routes>
    <Route path="/login/complete" element={<LoginCompletePage />} />
    <Route path="/datasets" element={<Datasets />} />
  </Routes></AuthProvider></MemoryRouter></StrictMode>);
  await screen.findByText("/datasets:account:true");
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(localStorage.getItem("aim_data_auth_mode")).toBe("oauth");
  expect(localStorage.getItem("aim_data_refresh_token")).toBe("synthetic-refresh");
  expect(Object.keys(localStorage).sort()).toEqual(["aim_data_access_token", "aim_data_auth_mode", "aim_data_refresh_token"]);
});
it("failed completion never publishes authentication", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ csrf_nonce: "nonce" })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ error_code: "access_denied" }), { status: 400 })));
  render(<MemoryRouter><AuthProvider><LoginCompletePage /></AuthProvider></MemoryRouter>);
  expect(await screen.findByRole("alert")).toHaveTextContent("Sign-in was not approved");
  expect(localStorage.length).toBe(0);
});

it.each([200, 429, 503])("reload and API share refresh without clearing transient auth (%s)", async status => {
  storeAuthTokens(data as never);
  let tail = Promise.resolve();
  vi.stubGlobal("navigator", { locks: { request: (_name, action) => {
    const next = tail.then(action);
    tail = next.catch(() => {});
    return next;
  } } });
  const fetcher = vi.fn(async (url, options) => {
    if (url.endsWith("aim-market-refresh")) {
      return new Response(JSON.stringify({ ...data, access_token: "rotated" }), { status });
    }
    return new Response(JSON.stringify({ user_id: "account", role: "user" }), {
      status: options.headers.Authorization === "Bearer rotated" ? 200 : 401,
    });
  });
  vi.stubGlobal("fetch", fetcher);
  render(<MemoryRouter><AuthProvider><Datasets /></AuthProvider></MemoryRouter>);
  await act(async () => { await authApi.me().catch(() => {}); });
  await waitFor(() => expect(fetcher.mock.calls.filter(call => call[0].endsWith("aim-market-refresh"))).toHaveLength(1));
  expect(localStorage.getItem("aim_data_access_token")).toBe(status === 200 ? "rotated" : "synthetic-access");
  if (status === 200) await screen.findByText("/:account:true");
});

it("account switch and logout reset the published user", async () => {
  render(<MemoryRouter><AuthProvider><Datasets /></AuthProvider></MemoryRouter>);
  act(() => storeAuthTokens(data as never));
  await screen.findByText("/:account:true");
  act(() => storeAuthTokens({ ...data, auth_mode: "password", user: { id: "other-account" } } as never));
  await screen.findByText("/:other-account:true");
  act(() => clearAuthTokens());
  await screen.findByText("/::false");
  expect(localStorage.length).toBe(0);
});
