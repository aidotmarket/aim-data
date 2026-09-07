import React, { createContext, useContext, useState, useEffect, useCallback, useRef } from "react";
import { authApi, getApiUrl, clearAuthTokens, storeAuthTokens, getAuthMode, AUTH_CHANGED, type AuthLoginResponse } from "@/lib/api";

const ACCESS_TOKEN_KEY = "aim_data_access_token";
const REFRESH_TOKEN_KEY = "aim_data_refresh_token";

interface UserInfo {
  user_id: string;
  username?: string;
  email?: string;
  first_name?: string;
  last_name?: string;
  company_name?: string;
  role: string;
  status?: string;
  is_active?: boolean;
  onboarding_required?: boolean;
  onboarding_step?: string | null;
}

interface AuthContextType {
  apiKey: string | null;
  user: UserInfo | null;
  onboarding_required: boolean;
  onboarding_step: string | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  pending2fa: boolean;
  login: (email: string, password: string) => Promise<"success" | "requires_2fa">;
  verify2fa: (code: string) => Promise<void>;
  refreshUser: () => Promise<void>;
  logout: () => void;
  completeLogin: (data: AuthLoginResponse) => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

type NormalizableUserInfo = Partial<UserInfo> & {
  id?: string;
  email?: string;
};

type TwoFactorLoginResponse = {
  requires_2fa: true;
  pre_auth_token: string;
};

function isTwoFactorLoginResponse(data: unknown): data is TwoFactorLoginResponse {
  const maybeResponse = data as { requires_2fa?: unknown; pre_auth_token?: unknown };
  return (
    typeof data === "object" &&
    data !== null &&
    maybeResponse.requires_2fa === true &&
    typeof maybeResponse.pre_auth_token === "string"
  );
}

function normalizeUser(data: NormalizableUserInfo): UserInfo {
  return {
    ...data,
    user_id: data.user_id ?? data.id ?? "",
    username: data.username ?? data.email,
    role: data.role ?? "user",
  };
}

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [apiKey, setApiKey] = useState<string | null>(() => localStorage.getItem(ACCESS_TOKEN_KEY));
  const [user, setUser] = useState<UserInfo | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [pending2fa, setPending2fa] = useState<{ email: string; preAuthToken: string } | null>(null);
  const refreshUserInFlight = useRef(false);
  const authGeneration = useRef(0);

  const clearAuth = useCallback(() => {
    clearAuthTokens();
    setApiKey(null);
    setUser(null);
    setPending2fa(null);
  }, []);

  const completeLogin = useCallback((data: AuthLoginResponse) => {
    storeAuthTokens(data);
  }, []);

  const refreshUser = useCallback(async () => {
    if (!apiKey || refreshUserInFlight.current) {
      return;
    }

    refreshUserInFlight.current = true;
    const generation = authGeneration.current;
    try {
      const data = await authApi.me();
      if (generation !== authGeneration.current) return;
      setApiKey(localStorage.getItem(ACCESS_TOKEN_KEY));
      setUser(normalizeUser(data));
    } catch {
      // Keep the current session state on transient refresh failures.
    } finally {
      refreshUserInFlight.current = false;
    }
  }, [apiKey]);

  // Validate stored access token on mount.
  useEffect(() => {
    const validate = async () => {
      const accessToken = localStorage.getItem(ACCESS_TOKEN_KEY);
      if (!accessToken) {
        setIsLoading(false);
        return;
      }

      const generation = authGeneration.current;
      try {
        getAuthMode();
        const data = await authApi.me();
        if (generation !== authGeneration.current) return;
        setApiKey(localStorage.getItem(ACCESS_TOKEN_KEY));
        setUser(normalizeUser(data));
      } catch {
        // Network error: keep tokens so a transient outage does not sign the user out.
      } finally {
        setIsLoading(false);
      }
    };
    validate();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const sync = (event: Event) => {
      const generation = ++authGeneration.current;
      try { getAuthMode(); } catch { return; }
      const data = (event as CustomEvent<AuthLoginResponse>).detail;
      setApiKey(localStorage.getItem(ACCESS_TOKEN_KEY));
      setPending2fa(null);
      setUser(data?.user ? normalizeUser({ ...data.user,
        onboarding_required: data.onboarding_required, onboarding_step: data.onboarding_step }) : null);
      if (!data && localStorage.getItem(ACCESS_TOKEN_KEY)) {
        void authApi.me().then(me => {
          if (generation === authGeneration.current) setUser(normalizeUser(me));
        }).catch(() => {});
      }
    };
    const storage = (event: StorageEvent) => {
      if (event.key === null || [ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY, "aim_data_auth_mode"].includes(event.key)) sync(event);
    };
    window.addEventListener(AUTH_CHANGED, sync);
    window.addEventListener("storage", storage);
    return () => {
      window.removeEventListener(AUTH_CHANGED, sync);
      window.removeEventListener("storage", storage);
    };
  }, []);

  useEffect(() => {
    if (!apiKey || !user) {
      return;
    }

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void refreshUser();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [apiKey, refreshUser, user]);

  const login = useCallback(async (email: string, password: string) => {
    const res = await fetch(`${getApiUrl()}/api/auth/aim-market-login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Login failed" }));
      throw new Error(err.detail || `Login failed: ${res.status}`);
    }

    const data: unknown = await res.json();
    if (isTwoFactorLoginResponse(data)) {
      setPending2fa({ email, preAuthToken: data.pre_auth_token });
      return "requires_2fa";
    }

    completeLogin(data as AuthLoginResponse);
    return "success";
  }, [completeLogin]);

  const verify2fa = useCallback(async (code: string) => {
    if (!pending2fa) {
      throw new Error("No two-factor login is pending");
    }

    const res = await fetch(`${getApiUrl()}/api/auth/aim-market-login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: pending2fa.email,
        pre_auth_token: pending2fa.preAuthToken,
        code,
      }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Verification failed" }));
      throw new Error(err.detail || `Verification failed: ${res.status}`);
    }

    const data = await res.json() as AuthLoginResponse;
    completeLogin(data);
  }, [completeLogin, pending2fa]);

  const logout = useCallback(() => {
    clearAuth();
  }, [clearAuth]);

  return (
    <AuthContext.Provider
      value={{
        apiKey,
        user,
        onboarding_required: !!user?.onboarding_required,
        onboarding_step: user?.onboarding_step ?? null,
        isAuthenticated: !!apiKey && !!user,
        isLoading,
        pending2fa: !!pending2fa,
        login,
        verify2fa,
        refreshUser,
        logout,
        completeLogin,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
};
