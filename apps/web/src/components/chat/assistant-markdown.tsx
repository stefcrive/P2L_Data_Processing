import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { formatScientificText } from "@/lib/scientific-notation";

type MarkdownNode = {
  type?: string;
  value?: string;
  children?: MarkdownNode[];
};

function remarkScientificNotation() {
  return (tree: MarkdownNode) => {
    const visit = (node: MarkdownNode, inCode = false) => {
      const isCode = inCode || node.type === "code" || node.type === "inlineCode";
      if (!isCode && node.type === "text" && typeof node.value === "string") {
        node.value = formatScientificText(node.value);
      }
      node.children?.forEach((child) => visit(child, isCode));
    };
    visit(tree);
  };
}

export function AssistantMarkdown({ children }: { children: string }) {
  return (
    <div className="space-y-3 text-sm leading-6 text-slate-700">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkScientificNotation]}
        components={{
          h1: ({ children }) => <h2 className="font-display text-lg font-semibold text-slate-950">{children}</h2>,
          h2: ({ children }) => <h3 className="font-display text-base font-semibold text-slate-950">{children}</h3>,
          h3: ({ children }) => <h4 className="font-display text-sm font-semibold text-slate-900">{children}</h4>,
          p: ({ children }) => <p>{children}</p>,
          ul: ({ children }) => <ul className="ml-5 list-disc space-y-1">{children}</ul>,
          ol: ({ children }) => <ol className="ml-5 list-decimal space-y-1">{children}</ol>,
          a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer" className="font-medium text-blue-700 underline underline-offset-2">{children}</a>,
          code: ({ children }) => <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[12px] text-slate-800">{children}</code>,
          pre: ({ children }) => <pre className="overflow-x-auto rounded-md border border-slate-200 bg-slate-950 p-3 text-xs text-slate-100">{children}</pre>,
          table: ({ children }) => <div className="overflow-x-auto"><table className="w-full border-collapse text-left text-xs">{children}</table></div>,
          th: ({ children }) => <th className="border-b border-slate-300 bg-slate-50 px-2 py-1.5 font-semibold text-slate-800">{children}</th>,
          td: ({ children }) => <td className="border-b border-slate-200 px-2 py-1.5 align-top">{children}</td>,
          blockquote: ({ children }) => <blockquote className="border-l-2 border-blue-300 pl-3 text-slate-600">{children}</blockquote>,
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
