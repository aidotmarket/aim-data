import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import LoginPage from "./LoginPage";
const login = vi.fn().mockResolvedValue("requires_2fa");
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ login, verify2fa: vi.fn(), pending2fa: false }) }));
vi.mock("@/components/VersionBadge", () => ({ default: () => null }));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.clearAllMocks(); });

it.each(["local_disabled", "client_disabled", "backend_unsupported", "backend_unavailable"])(
  "button_disabled_preserves_password (%s)", async reason => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ enabled: false, reason,
      csrf_nonce: "nonce", loopback_origin: location.origin })));
    vi.stubGlobal("fetch", fetcher);
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    await screen.findByRole("status");
    expect(screen.getByRole("button", { name: "Continue with Google" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Continue with GitHub" })).toBeDisabled();
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Provider-only accounts");
    const google = screen.getByRole("button", { name: "Continue with Google" });
    const github = screen.getByRole("button", { name: "Continue with GitHub" });
    const divider = screen.getByText("or continue with email").parentElement!.parentElement!;
    const form = screen.getByLabelText("Email").closest("form")!;
    const guidance = screen.getByRole("status");
    const disclosure = screen.getByText(/AIM Data keeps session tokens/);
    const registration = screen.getByRole("link", { name: "Create one at ai.market" });
    expect(google.nextElementSibling).toBe(github);
    expect(github.nextElementSibling).toBe(divider);
    expect(divider.nextElementSibling).toBeNull();
    expect(divider.parentElement!.nextElementSibling).toBe(form);
    expect(form.nextElementSibling!.firstElementChild).toBe(guidance);
    expect(guidance.nextElementSibling).toBe(disclosure);
    expect(disclosure.nextElementSibling).toContainElement(registration);
    expect(guidance).toHaveClass("text-xs", "text-muted-foreground");
    expect(disclosure).toHaveClass("text-xs", "text-muted-foreground");
    if (reason === "backend_unavailable") expect(screen.getByRole("status")).not.toHaveTextContent("is disabled");
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: "buyer@example.test" } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: "synthetic-password" } });
    fireEvent.click(screen.getByRole("button", { name: "Sign In" }));
    await waitFor(() => expect(login).toHaveBeenCalledWith("buyer@example.test", "synthetic-password"));
    expect(fetcher).toHaveBeenCalledTimes(1);
  },
);

it("start disabled race keeps fallback and never navigates", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true,
    csrf_nonce: "nonce", loopback_origin: location.origin })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ error_code: "client_disabled" }), { status: 409 })));
  render(<MemoryRouter><LoginPage /></MemoryRouter>);
  const button = screen.getByRole("button", { name: "Continue with Google" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(await screen.findByRole("status")).toHaveTextContent("ai.market sign-in is disabled");
  expect(button.querySelector(".animate-spin")).toBeNull();
  expect(screen.getByLabelText("Password")).toBeEnabled();
  expect(location.pathname).toBe("/");
});


it.each([["Google", "google"], ["GitHub", "github"]])(
  "clicking %s posts its provider hint and disables both buttons", async (label, provider) => {
    const assign = vi.fn();
    vi.stubGlobal("location", { origin: location.origin, assign });
    let finishStart!: (response: Response) => void;
    const pendingStart = new Promise<Response>(resolve => { finishStart = resolve; });
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true, csrf_nonce: "nonce", loopback_origin: location.origin })))
      .mockReturnValueOnce(pendingStart);
    vi.stubGlobal("fetch", fetcher);
    render(<MemoryRouter><LoginPage /></MemoryRouter>);
    const button = screen.getByRole("button", { name: `Continue with ${label}` });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    const spinner = button.querySelector("svg")!;
    expect(spinner).toHaveClass("animate-spin", "h-4", "w-4");
    expect(spinner).toHaveAttribute("viewBox", "0 0 24 24");
    expect(spinner).toHaveAttribute("fill", "none");
    expect(spinner.querySelector("circle")).toHaveClass("opacity-25");
    expect(spinner.querySelector("circle")).toHaveAttribute("stroke-width", "4");
    expect(spinner.querySelector("path")).toHaveClass("opacity-75");
    expect(spinner.querySelector("path")).toHaveAttribute("d", "M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z");
    const otherButton = screen.getByRole("button", { name: `Continue with ${label === "Google" ? "GitHub" : "Google"}` });
    expect(otherButton.querySelector("svg")).toHaveAttribute("width", "18");
    expect(otherButton.querySelector(".animate-spin")).toBeNull();
    expect(button).toBeDisabled();
    expect(otherButton).toBeDisabled();
    expect(assign).not.toHaveBeenCalled();
    finishStart(new Response(JSON.stringify({ authorization_url: "https://api.ai.market/api/v1/oauth/authorize" })));
    await waitFor(() => expect(assign).toHaveBeenCalledWith("https://api.ai.market/api/v1/oauth/authorize"));
    expect(fetcher).toHaveBeenLastCalledWith("/api/auth/aim-market/start", expect.objectContaining({
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ csrf_nonce: "nonce", provider }),
    }));
    for (const name of ["Continue with Google", "Continue with GitHub"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
  },
);
