import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import type { Components } from "react-markdown";

/** Extract language from rehype-highlight className e.g. "hljs language-python" → "python" */
function parseLang(className?: string): string | null {
  const m = /language-(\w+)/.exec(className ?? "");
  return m ? m[1] : null;
}

const components: Components = {
  // Wrap <pre> to add a language badge + left accent border.
  pre({ children, ...props }) {
    // Pull language out of nested <code> className if possible.
    let lang: string | null = null;
    if (children && typeof children === "object" && "props" in (children as object)) {
      const codeEl = children as React.ReactElement<{ className?: string }>;
      lang = parseLang(codeEl.props?.className);
    }
    return (
      <div className="brut-md-pre-wrap">
        {lang && (
          <div className="brut-md-lang-badge">{lang}</div>
        )}
        <pre className="brut-md-pre" {...props}>
          {children}
        </pre>
      </div>
    );
  },
  code({ children, className, ...props }) {
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
 * Styling lives in index.css under .brut-md* classes.
 * rehype-highlight adds .hljs classes for syntax coloring.
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
