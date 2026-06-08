import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { InputOTP, InputOTPGroup, InputOTPSlot, InputOTPSeparator } from "@/components/ui/input-otp";
import { Loader2, AlertCircle } from "lucide-react";
import { useAuth } from "@/contexts/AuthContext";
import VersionBadge from "@/components/VersionBadge";

const LoginPage = () => {
  const navigate = useNavigate();
  const { login, verify2fa, pending2fa } = useAuth();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [otpCode, setOtpCode] = useState("");
  const [error, setError] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);

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
                  <div className="space-y-2">
                    <Label htmlFor="email">Email</Label>
                    <Input
                      id="email"
                      type="email"
                      placeholder="Email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      className="bg-background border-border"
                      autoFocus
                      autoComplete="email"
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="password">Password</Label>
                    <Input
                      id="password"
                      type="password"
                      placeholder="Password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="bg-background border-border"
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

              <Button type="submit" className="w-full" disabled={isSubmitting}>
                {isSubmitting ? (
                  <>
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                    {pending2fa ? "Verifying..." : "Signing in..."}
                  </>
                ) : (
                  pending2fa ? "Verify Code" : "Sign In"
                )}
              </Button>

              {!pending2fa && (
                <p className="text-sm text-center text-muted-foreground">
                  Don't have an account?{" "}
                  <a
                    href="https://ai.market/register"
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-primary hover:underline"
                  >
                    Create one at ai.market
                  </a>
                </p>
              )}
            </form>
          </CardContent>
        </Card>
      </div>
      <VersionBadge />
    </div>
  );
};

export default LoginPage;
