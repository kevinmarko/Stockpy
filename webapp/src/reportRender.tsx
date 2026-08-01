/**
 * reportRender.tsx — shared rendering helpers for GET /reports/{name}
 * content (`ReportContent`), used by both ReportLibrary.tsx (the full
 * library screen) and ReportPreviewModal.tsx (the omni-search quick
 * preview). Split out so the two call sites share one implementation
 * instead of drifting copies of the same markdown renderer.
 */
import type { ReactNode } from "react";
import type { ReportContent } from "./api/types";

const DASH = "—";

export function fmtBytes(n: number | null): string {
  if (n == null) return DASH;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export function mimeFor(content: ReportContent): string {
  if (content.content_type === "html") return "text/html;charset=utf-8;";
  if (content.content_type === "markdown") return "text/markdown;charset=utf-8;";
  return "application/json;charset=utf-8;";
}

export function textFor(content: ReportContent): string {
  if (content.content_type === "json") return JSON.stringify(content.json, null, 2);
  return content.text ?? "";
}

/**
 * A tiny, dependency-free renderer for the small markdown subset
 * scripts/daily_briefing.py actually emits (#/##/### headers, `- ` bullet
 * items, **bold**, _italic_, `code`, blank-line paragraph breaks). Not a
 * general CommonMark implementation — deliberately so, to avoid adding a
 * markdown-parser dependency for one screen's worth of machine-generated
 * text. Builds plain React elements only (no `dangerouslySetInnerHTML`), so
 * it carries no injection risk regardless of the briefing's content.
 */
function renderInlineMarkdown(text: string, keyPrefix: string) {
  const parts = text.split(/(\*\*[^*]+\*\*|_[^_]+_|`[^`]+`)/g).filter((p) => p !== "");
  return parts.map((part, i) => {
    const key = `${keyPrefix}-${i}`;
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={key}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith("_") && part.endsWith("_") && part.length > 2) {
      return <em key={key}>{part.slice(1, -1)}</em>;
    }
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return (
        <code key={key} style={{ fontFamily: "var(--font-mono, ui-monospace, monospace)" }}>
          {part.slice(1, -1)}
        </code>
      );
    }
    return part;
  });
}

export function MiniMarkdown({ text }: { text: string }) {
  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let listItems: string[] = [];

  const flushList = (key: string) => {
    if (listItems.length === 0) return;
    blocks.push(
      <ul key={key} style={{ margin: "0 0 var(--s-3)", paddingLeft: "var(--s-5)" }}>
        {listItems.map((item, i) => (
          <li key={i} style={{ marginBottom: "var(--s-1)" }}>
            {renderInlineMarkdown(item, `${key}-li-${i}`)}
          </li>
        ))}
      </ul>
    );
    listItems = [];
  };

  lines.forEach((line, i) => {
    const trimmed = line.trim();
    if (trimmed.startsWith("- ")) {
      listItems.push(trimmed.slice(2));
      return;
    }
    flushList(`list-${i}`);
    if (trimmed === "") return;
    const heading = /^(#{1,3})\s+(.*)$/.exec(trimmed);
    if (heading) {
      const level = heading[1].length;
      const content = renderInlineMarkdown(heading[2], `h-${i}`);
      const style = {
        margin: level === 1 ? "0 0 var(--s-3)" : "var(--s-4) 0 var(--s-2)",
        fontSize: level === 1 ? "var(--t-title)" : level === 2 ? "var(--t-subhead)" : "var(--t-callout)",
      };
      if (level === 1) blocks.push(<h2 key={i} style={style}>{content}</h2>);
      else if (level === 2) blocks.push(<h3 key={i} style={style}>{content}</h3>);
      else blocks.push(<h4 key={i} style={style}>{content}</h4>);
      return;
    }
    blocks.push(
      <p key={i} style={{ margin: "0 0 var(--s-2)", lineHeight: 1.6 }}>
        {renderInlineMarkdown(trimmed, `p-${i}`)}
      </p>
    );
  });
  flushList("list-end");

  return <div data-testid="mini-markdown">{blocks}</div>;
}
