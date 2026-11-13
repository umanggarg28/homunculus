import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import remarkMath from "remark-math";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import type { Components } from "react-markdown";
import "katex/dist/katex.min.css";

/** Extract language from rehype-highlight className e.g. "hljs language-python" → "python" */
function parseLang(className?: string): string | null {
  const m = /language-(\w+)/.exec(className ?? "");
  return m ? m[1] : null;
}

const components: Components = {
  // Wrap <pre> to name its language in a corner.
  pre({ children, ...props }) {
    // Pull language out of nested <code> className if possible.
    let lang: string | null = null;
    if (children && typeof children === "object" && "props" in (children as object)) {
      const codeEl = children as React.ReactElement<{ className?: string }>;
      lang = parseLang(codeEl.props?.className);
    }
    return (
      <div className="md-pre-wrap">
        {lang && (
          <div className="md-lang">{lang}</div>
        )}
        <pre className="md-pre" {...props}>
          {children}
        </pre>
      </div>
    );
  },
  code({ children, className, ...props }) {
    // Fenced blocks without a language tag have no className: detect by newlines.
    const isBlock = !!className || String(children).includes("\n");
    if (isBlock) {
      return (
        <code className={`md-code-block ${className ?? ""}`} {...props}>
          {children}
        </code>
      );
    }
    return (
      <code className="md-inline-code" {...props}>
        {children}
      </code>
    );
  },
  // Tables scroll sideways on narrow screens rather than squeezing their columns.
  table({ children }) {
    return (
      <div className="md-table-wrap">
        <table className="md-table">{children}</table>
      </div>
    );
  },
};

/** Markdown renderer for agent replies.
 *
 * Styling lives in design/markdown.css.
 * rehype-highlight adds .hljs classes for syntax coloring.
 */
export function MarkdownMessage({ text }: { text: string }) {
  return (
    <div className="md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkBreaks, remarkMath]}
        rehypePlugins={[rehypeHighlight, rehypeKatex]}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
