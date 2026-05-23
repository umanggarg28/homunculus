import { useEffect, useRef } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { FeedRow } from "./FeedRow";
import { Card } from "@/components/ui/Card";
import { Empty } from "@/components/ui/Empty";

export function FeedStream() {
  const { events } = useEventStream(300);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const nearBottom =
      window.innerHeight + window.scrollY > document.body.offsetHeight - 200;
    if (nearBottom) endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  if (events.length === 0) return <Empty>Waiting for first event…</Empty>;

  return (
    <Card className="overflow-hidden p-0">
      {events.map((e, i) => (
        <FeedRow key={`${e.ts}-${i}`} event={e} />
      ))}
      <div ref={endRef} />
    </Card>
  );
}
