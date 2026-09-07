import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "@/contexts/AuthContext";
import { completeAuth, authGuidance } from "@/lib/aimMarketAuth";

export default function LoginCompletePage() {
  const { completeLogin } = useAuth();
  const navigate = useNavigate();
  const completion = useRef<ReturnType<typeof completeAuth>>();
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    completion.current ??= completeAuth();
    completion.current.then(data => {
      if (!active) return;
      completeLogin(data);
      navigate("/datasets", { replace: true });
    }).catch(reason => {
      if (active) setError(reason instanceof Error ? reason.message : authGuidance("unknown"));
    });
    return () => { active = false; };
  }, [completeLogin, navigate]);
  return <main className="max-w-md mx-auto p-8">
    <h1>Completing sign-in</h1>
    {error ? <><p role="alert">{error}</p><Link to="/login">Return to sign-in</Link></> : <p>Please wait…</p>}
  </main>;
}
