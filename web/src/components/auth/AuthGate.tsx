import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, getWebToken, setWebToken } from "@/lib/api";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Input";

interface Props { children: ReactNode; }

/** Brutalist auth gate — terminal-style login. Same hairline-bordered
 *  box, mono everywhere, accent prompt. */
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
      <div
        className="min-h-screen grid place-items-center"
        style={{ background: "var(--color-bg)", fontFamily: "var(--font-mono)" }}
      >
        <div className="text-[10px] uppercase tracking-[0.22em]" style={{ color: "var(--color-text-muted)" }}>
          ── loading
        </div>
      </div>
    );
  }

  if (authRequired && !getWebToken()) {
    return (
      <div
        className="min-h-screen grid place-items-center px-6"
        style={{ background: "var(--color-bg)", fontFamily: "var(--font-mono)" }}
      >
        <form
          onSubmit={submit}
          className="w-full max-w-[420px] p-7"
          style={{
            background: "var(--color-bg)",
            border: "1px solid var(--color-accent)",
            boxShadow: "0 0 32px var(--color-accent-glow)",
          }}
        >
          <div className="mb-2 text-[14px] font-bold uppercase tracking-[0.04em]" style={{ color: "var(--color-text)" }}>
            <span style={{ color: "var(--color-accent)" }}>$</span> homunculus
          </div>
          <div className="mb-7 text-[10px] uppercase tracking-[0.22em]" style={{ color: "var(--color-text-muted)" }}>
            ── auth required · paste your token to continue
          </div>

          <Field label="access token">
            <Input
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoFocus
              type="password"
              placeholder="hmcl_…"
            />
          </Field>

          <Button type="submit" variant="primary" disabled={!token.trim()} className="w-full">
            [sign in ↵]
          </Button>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
