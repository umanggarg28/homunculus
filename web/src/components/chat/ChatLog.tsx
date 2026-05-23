import { AnimatePresence } from "framer-motion";
import { Fragment, useMemo } from "react";
import { ChatMessage } from "./ChatMessage";
import { ChatLandingHero } from "./ChatLandingHero";
import { SectionDivider } from "./SectionDivider";
import { useChatToolCalls } from "@/hooks/useChatToolCalls";
import type { ChatMessage as ChatMessageType } from "@/hooks/useChatStream";

interface Props {
  messages: ChatMessageType[];
  onPickPrompt?: (text: string) => void;
}

export function ChatLog({ messages, onPickPrompt }: Props) {
  const toolTimeline = useChatToolCalls();

  const assistantToolCalls = useMemo(() => {
    const map = new Map<string, typeof toolTimeline>();
    const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");
    if (lastAssistant) map.set(lastAssistant.id, toolTimeline);
    return map;
  }, [messages, toolTimeline]);

  if (messages.length === 0) {
    return <ChatLandingHero onPick={(t) => onPickPrompt?.(t)} />;
  }

  let userIndex = -1;

  return (
    <div className="flex flex-col gap-7">
      <AnimatePresence initial={false}>
        {messages.map((m) => {
          let beforeDivider: React.ReactNode = null;
          if (m.role === "user") {
            userIndex += 1;
            if (userIndex > 0) {
              beforeDivider = <SectionDivider key={`d-${m.id}`} />;
            }
          }
          const toolCalls = (m.role === "assistant" && assistantToolCalls.get(m.id)) || [];

          return (
            <Fragment key={m.id}>
              {beforeDivider}
              <ChatMessage message={m} toolCalls={toolCalls} />
            </Fragment>
          );
        })}
      </AnimatePresence>
    </div>
  );
}
