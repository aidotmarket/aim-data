import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { InputOTP, InputOTPGroup, InputOTPSlot, InputOTPSeparator } from "@/components/ui/input-otp";
import { Loader2, AlertCircle } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import { bootstrapAuth, startAuth, authGuidance, type Bootstrap } from "@/lib/aimMarketAuth";
import VersionBadge from "@/components/VersionBadge";

const LoginPage = () => {
  const navigate = useNavigate();
  const { login, verify2fa, pending2fa } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null);
  const [oauthMessage, setOauthMessage] = useState("");
  const [starting, setStarting] = useState<"google" | "github" | null>(null);
  useEffect(() => {
    let active = true;
    bootstrapAuth().then(data => {
      if (!active) return;
      const local = location.origin === data.loopback_origin;
      setBootstrap({ ...data, enabled: data.enabled && local });
      setOauthMessage(!local ? authGuidance("loopback_origin_required") :
        data.enabled ? "" : authGuidance(data.reason ?? "backend_unavailable"));
    }).catch(() => { if (active) setOauthMessage(authGuidance("backend_unavailable")); });
    return () => { active = false; };
  }, []);
  const signInWithMarket = async (provider: "google" | "github") => {
    if (!bootstrap) return;
    setStarting(provider);
    try {
      window.location.assign(await startAuth(bootstrap, provider));
    } catch (reason) {
      setOauthMessage(reason instanceof Error ? reason.message : authGuidance("backend_unavailable"));
      setBootstrap(null); // A fresh page/start is required after a failed attempt.
      setStarting(null);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (pending2fa) {
      if (otpCode.length !== 6) {
        setError("Enter the 6-digit code");
        return;
      }

      setIsSubmitting(true);
      try {
        await verify2fa(otpCode);
        navigate("/datasets", { replace: true });
      } catch (err) {
        const message = err instanceof Error ? err.message : "Invalid code";
        setError(message === "Invalid verification code" ? "Invalid code, try again" : message);
        setOtpCode("");
      } finally {
        setIsSubmitting(false);
      }
      return;
    }

    if (!email || !password) {
      setError("Email and password are required");
      return;
    }

    setIsSubmitting(true);
    try {
      const result = await login(email, password);
      if (result === "success") {
        navigate("/datasets", { replace: true });
      } else {
        setOtpCode("");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid credentials");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      {/* ai.market logo, upper-right */}
      <img
        src="https://ai.market/logo.svg"
        alt="ai.market"
        className="fixed top-4 right-4 w-12 h-12"
      />
      <div className="w-full max-w-md space-y-8">
        {/* AIM DATA header */}
        <div className="text-center space-y-2">
          <h1 className="text-5xl font-extrabold tracking-tight text-primary">AIM DATA</h1>
          <p className="text-muted-foreground">Sign in with your ai.market account to access AIM Data</p>
        </div>

        <Card className="bg-card border-border">
          <CardHeader>
            <CardTitle className="text-foreground">{pending2fa ? "Two-factor code" : "Sign in"}</CardTitle>
            <CardDescription>
              {pending2fa ? "Enter the code from your authenticator app" : "Use your ai.market account"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {!pending2fa && <div className="space-y-3">
              <button
                type="button"
                disabled={!bootstrap?.enabled || starting !== null || isSubmitting}
                onClick={() => signInWithMarket('google')}
                className="w-full flex items-center justify-center gap-3 rounded-lg border border-gray-300 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {starting === 'google' ? (
                  <Spinner />
                ) : (
                  <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
                    <path d="M17.64 9.2c0-.637-.057-1.251-.164-1.84H9v3.481h4.844a4.14 4.14 0 01-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615z" fill="#4285F4"/>
                    <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 009 18z" fill="#34A853"/>
                    <path d="M3.964 10.71A5.41 5.41 0 013.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 000 9c0 1.452.348 2.827.957 4.042l3.007-2.332z" fill="#FBBC05"/>
                    <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 00.957 4.958L3.964 6.29C4.672 4.163 6.656 2.58 9 3.58z" fill="#EA4335"/>
                  </svg>
                )}
                Continue with Google
              </button>

              <button
                type="button"
                disabled={!bootstrap?.enabled || starting !== null || isSubmitting}
                onClick={() => signInWithMarket('github')}
                className="w-full flex items-center justify-center gap-3 rounded-lg border border-transparent px-4 py-2.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed"
                style={{ backgroundColor: '#24292e' }}
              >
                {starting === 'github' ? (
                  <Spinner />
                ) : (
                  <svg width="18" height="18" viewBox="0 0 16 16" fill="currentColor" xmlns="http://www.w3.org/2000/svg">
                    <path fillRule="evenodd" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>
                  </svg>
                )}
                Continue with GitHub
              </button>

              <div className="relative my-4">
                <div className="absolute inset-0 flex items-center">
                  <div className="w-full border-t border-gray-300" />
                </div>
                <div className="relative flex justify-center text-sm">
                  <span className="bg-white px-4 text-gray-500">
                    or continue with email
                  </span>
                </div>
              </div>
            </div>}
            <form onSubmit={handleSubmit} className="space-y-4">
              {pending2fa ? (
                <div className="space-y-2">
                  <Label htmlFor="otp-code">Verification code</Label>
                  <InputOTP
                    id="otp-code"
                    maxLength={6}
                    value={otpCode}
                    onChange={(value) => setOtpCode(value.replace(/\D/g, "").slice(0, 6))}
                    disabled={isSubmitting}
                    autoFocus
                    containerClassName="justify-center"
                  >
                    <InputOTPGroup>
                      <InputOTPSlot index={0} />
                      <InputOTPSlot index={1} />
                      <InputOTPSlot index={2} />
                    </InputOTPGroup>
                    <InputOTPSeparator />
                    <InputOTPGroup>
                      <InputOTPSlot index={3} />
                      <InputOTPSlot index={4} />
                      <InputOTPSlot index={5} />
                    </InputOTPGroup>
                  </InputOTP>
                </div>
              ) : (
                <>
                  <div>
                    <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">Email</label>
                    <input
                      id="email"
                      type="email"
                      placeholder="Email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#3F51B5] focus:border-transparent"
                      autoFocus
                      autoComplete="email"
                    />
                  </div>

                  <div>
                    <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">Password</label>
                    <input
                      id="password"
                      type="password"
                      placeholder="Password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-[#3F51B5] focus:border-transparent"
                      autoComplete="current-password"
                    />
                  </div>
                </>
              )}

              {error && (
                <div className="flex items-center gap-2 text-sm text-destructive">
                  <AlertCircle className="w-4 h-4 flex-shrink-0" />
                  {error}
                </div>
              )}

              <button type="submit" className="w-full rounded-lg bg-[#3F51B5] px-4 py-2.5 text-sm font-medium text-white hover:bg-[#3545a0] disabled:opacity-50 disabled:cursor-not-allowed" disabled={isSubmitting}>
                {isSubmitting ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    {pending2fa ? "Verifying..." : "Signing in..."}
                  </>
                ) : (
                  pending2fa ? "Verify Code" : "Sign In"
                )}
              </button>

            </form>
            {!pending2fa && (
              <div className="mt-4 space-y-3">
                {oauthMessage && <p role="status" className="text-xs text-muted-foreground">{oauthMessage}</p>}
                <p className="text-xs text-muted-foreground">AIM Data keeps session tokens in this browser’s local storage and a local file restricted to its owner (0600). These stores are not encrypted secure storage.</p>
                <p className="text-sm text-center text-muted-foreground">
                  Don't have an account?{" "}
                  <a
                    href="https://ai.market/register"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[#3F51B5] hover:underline"
                  >
                    Create one at ai.market
                  </a>
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
      <VersionBadge />
    </div>
  );
};

function Spinner() {
  return (
    <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
    </svg>
  );
}

export default LoginPage;
