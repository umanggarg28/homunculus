import { PageHeader } from "@/components/ui/PageHeader";
import { FeedStream } from "@/components/feed/FeedStream";

export function FeedPage() {
  return (
    <div className="max-w-[1100px] mx-auto px-8 pt-10 pb-16">
      <PageHeader
        title="Activity"
        subtitle="Every tool call, reply, and LLM call · live"
      />
      <FeedStream />
    </div>
  );
}
