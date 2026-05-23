import ReactMarkdown from "react-markdown";

export function MarkdownMessage({ text }: { text: string }) {
  return (
    <div className="markdown-message">
      <ReactMarkdown
        components={{
          a: ({ children, ...props }) => (
            <a
              {...props}
              target="_blank"
              rel="noreferrer"
              className="text-[var(--color-signal)] underline decoration-[var(--color-signal)]/30 underline-offset-4"
            >
              {children}
            </a>
          ),
          code: ({ children, className, ...props }) => {
            const inline = !className;
            if (inline) {
              return (
                <code
                  {...props}
                  className="px-1 py-0.5 rounded bg-[var(--color-surface-3)] text-[var(--color-signal)]"
                >
                  {children}
                </code>
              );
            }
            return (
              <code {...props} className={className}>
                {children}
              </code>
            );
          },
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
