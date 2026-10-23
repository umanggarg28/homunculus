import { useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { useApprovals } from "@/hooks/useApprovals";
import { api, type Approval } from "@/lib/api";
import { argumentPairs } from "@/lib/toolSteps";

/** Actions the agent proposed that wait for the operator.
 *
 *  An "ask" request is approved or declined. A "hand off" request is something only
 *  the operator should do, such as a payment: the operator does it and marks it done.
 *  Either way the agent is told the outcome and carries on in Chat. */
export function ApprovalsPage() {
  const { approvals, pendingCount, refresh } = useApprovals();
  const pending = approvals.filter((a) => a.status === "pending");
  const decided = approvals.filter((a) => a.status !== "pending");

  return (
    <PageShell>
      <PageHeader
        title="Approvals"
        subtitle={pendingCount === 0 ? "nothing waiting" : `${pendingCount} waiting for you`}
      />
      <div className="max-w-[720px] mx-auto flex flex-col gap-4" style={{ paddingBottom: 48 }}>
        {pending.length === 0 && (
          <div
            className="text-[11px] uppercase tracking-[0.18em] py-10 text-center"
            style={{ color: "var(--color-text-faint)" }}
          >
            nothing is waiting for your approval
          </div>
        )}
        {pending.map((approval) => (
          <PendingRequest key={approval.id} approval={approval} onDecided={refresh} />
        ))}

        {decided.length > 0 && (
          <>
            <div
              className="text-[9px] uppercase tracking-[0.22em] mt-6"
              style={{ color: "var(--color-text-faint)" }}
            >
              recently decided
            </div>
            {decided.map((approval) => (
              <DecidedRequest key={approval.id} approval={approval} />
            ))}
          </>
        )}
      </div>
    </PageShell>
  );
}

function PendingRequest({ approval, onDecided }: { approval: Approval; onDecided: () => Promise<void> }) {
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const isHandOff = approval.kind === "hand off";

  const act = async (decision: () => Promise<Approval>) => {
    setBusy(true);
    setError(null);
    try {
      await decision();
      await onDecided();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      data-testid={`approval-${approval.id}`}
      className="p-4"
      style={{ border: "1px solid var(--color-warning)", background: "var(--color-amber-dim)" }}
    >
      <RequestHeading approval={approval} />
      <Arguments raw={approval.arguments} />
      <div className="text-[11px] mt-3" style={{ color: "var(--color-text-muted)" }}>
        {isHandOff
          ? "Only you should do this. Do it yourself, then mark it done."
          : "Approve to run exactly this action. The agent is told either way."}
      </div>
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder={isHandOff ? "note for the agent (optional)" : "reason, if you decline (optional)"}
        className="w-full mt-3 px-2 py-1 text-[12px]"
        style={{
          background: "var(--color-bg)",
          border: "1px solid var(--color-border)",
          color: "var(--color-text)",
          fontFamily: "var(--font-mono)",
        }}
      />
      <div className="flex gap-3 mt-3">
        {isHandOff ? (
          <Action label="mark done" primary disabled={busy} onClick={() => act(() => api.done(approval.id, note))} />
        ) : (
          <Action label="approve" primary disabled={busy} onClick={() => act(() => api.approve(approval.id))} />
        )}
        <Action label="decline" disabled={busy} onClick={() => act(() => api.deny(approval.id, note))} />
      </div>
      {error && (
        <div className="mt-2 text-[11px]" style={{ color: "var(--color-danger)" }}>
          {error}
        </div>
      )}
    </div>
  );
}

function DecidedRequest({ approval }: { approval: Approval }) {
  const color = approval.status === "denied" ? "var(--color-text-muted)" : "var(--color-accent)";
  return (
    <div className="p-3" style={{ border: "1px solid var(--color-border)" }}>
      <RequestHeading approval={approval} status={approval.status} statusColor={color} />
      {approval.note && (
        <div className="text-[11px] mt-1" style={{ color: "var(--color-text-muted)" }}>
          note: {approval.note}
        </div>
      )}
      {approval.result && (
        <div className="text-[11px] mt-1" style={{ color: "var(--color-text-dim)" }}>
          result: {approval.result}
        </div>
      )}
      <Link to="/chat" className="text-[10px] uppercase tracking-[0.16em] mt-2 inline-block" style={{ color: "var(--color-text-faint)" }}>
        the agent's follow-up is in chat ›
      </Link>
    </div>
  );
}

function RequestHeading({
  approval,
  status,
  statusColor,
}: {
  approval: Approval;
  status?: string;
  statusColor?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <div className="text-[13px] uppercase tracking-[0.08em]" style={{ color: "var(--color-text)" }}>
        {approval.tool}
        <span className="ml-3 text-[10px] tracking-[0.16em]" style={{ color: "var(--color-warning)" }}>
          {approval.kind}
        </span>
      </div>
      <div className="text-[10px] uppercase tracking-[0.14em]" style={{ color: statusColor ?? "var(--color-text-faint)" }}>
        {status ?? `request ${approval.id}`} · {formatTime(approval.decided ?? approval.created)}
      </div>
    </div>
  );
}

function Arguments({ raw }: { raw: string }) {
  const pairs = argumentPairs(raw);
  if (pairs.length === 0) return null;
  return (
    <div className="mt-3 grid gap-x-4 gap-y-1" style={{ gridTemplateColumns: "max-content 1fr" }}>
      {pairs.map(([key, value]) => (
        <div key={key} className="contents text-[12px]">
          <span className="uppercase tracking-[0.12em] text-[10px]" style={{ color: "var(--color-accent)", paddingTop: 2 }}>
            {key}
          </span>
          <span style={{ color: "var(--color-text)", whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{value}</span>
        </div>
      ))}
    </div>
  );
}

function Action({
  label,
  onClick,
  disabled,
  primary,
}: {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  primary?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className="text-[11px] uppercase tracking-[0.16em] px-3 py-1 disabled:opacity-50"
      style={{
        fontFamily: "var(--font-mono)",
        cursor: disabled ? "default" : "pointer",
        background: primary ? "var(--color-accent)" : "transparent",
        color: primary ? "var(--color-bg)" : "var(--color-text-muted)",
        border: `1px solid ${primary ? "var(--color-accent)" : "var(--color-border)"}`,
      }}
    >
      [{label}]
    </button>
  );
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "--:--";
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}
