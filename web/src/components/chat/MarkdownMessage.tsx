import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { Components } from "react-markdown";

/** Custom renderers that respect the phosphor CRT design language. */
const components: Components = {
  // Code blocks: rendered as phosphor terminal panels.
  // rehype-highlight adds className="hljs language-*" to <code>.
  pre({ children, ...props }) {
    return (
      <pre className="brut-md-pre" {...props}>
        {children}
      </pre>
    );
  },
  code({ children, className, ...props }) {
    // Block code (inside <pre>) vs inline code
    const isBlock = !!className;
    if (isBlock) {
      return (
        <code className={`brut-md-code-block ${className ?? ""}`} {...props}>
          {children}
        </code>
      );
    }
    return (
      <code className="brut-md-inline-code" {...props}>
        {children}
      </code>
    );
  },
  // Tables with phosphor borders
  table({ children }) {
    return (
      <div className="brut-md-table-wrap">
        <table className="brut-md-table">{children}</table>
      </div>
    );
  },
};

/** Markdown renderer for agent replies.
 *
 * Styling lives entirely in index.css under .brut-md* classes.
 * rehype-highlight adds .hljs classes for syntax coloring (also in index.css).
 */
export function MarkdownMessage({ text }: { text: string }) {
  return (
    <div className="brut-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={components}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
