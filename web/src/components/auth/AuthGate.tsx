import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, getWebToken, setWebToken } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";

interface Props { children: ReactNode; }

export function AuthGate({ children }: Props) {
  const [loading, setLoading] = useState(true);
  const [authRequired, setAuthRequired] = useState(false);
  const [token, setToken] = useState(getWebToken());

  useEffect(() => {
    api.config()
      .then((config) => setAuthRequired(config.auth_required))
      .catch(() => setAuthRequired(true))
      .finally(() => setLoading(false));
  }, []);

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setWebToken(token);
    window.location.reload();
  };

  if (loading) {
    return (
      <div className="min-h-screen grid place-items-center" style={{ background: "var(--color-bg)" }}>
        <div className="text-[12px] text-[var(--color-text-muted)]">Loading…</div>
      </div>
    );
  }

  if (authRequired && !getWebToken()) {
    return (
      <div className="min-h-screen grid place-items-center px-6" style={{ background: "var(--color-bg)" }}>
        <form
          onSubmit={submit}
          className="w-full max-w-[380px] p-8 rounded-[10px]"
          style={{
            background: "var(--color-surface-2)",
            border: "1px solid var(--color-border-strong)",
          }}
        >
          <div className="flex items-center gap-2.5 mb-6">
            <div
              className="w-6 h-6 rounded-[5px] grid place-items-center"
              style={{ background: "var(--color-accent)", color: "white", fontWeight: 700, fontSize: 13 }}
            >
              H
            </div>
            <span className="text-[16px] font-semibold tracking-tight text-[var(--color-text)]">
              Homunculus
            </span>
          </div>

          <p className="text-[13px] mb-5 text-[var(--color-text-dim)]">
            Enter your access token to continue.
          </p>

          <Field label="Access token">
            <Input
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoFocus
              type="password"
              placeholder="hmcl_…"
            />
          </Field>

          <Button type="submit" variant="primary" disabled={!token.trim()} className="w-full">
            Sign in
          </Button>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
