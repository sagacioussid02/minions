/**
 * Lightweight markdown-to-React renderer for transcript bodies.
 *
 * Agent output uses a small, stable subset of markdown — headings, lists,
 * bold, italic, code spans, paragraphs. Pulling in `react-markdown` for
 * that is overkill, so we hand-roll a renderer that:
 *
 *   - turns `## Heading` into styled headings
 *   - groups consecutive `- item` / `* item` / `1. item` lines into lists
 *   - splits remaining text into paragraphs on blank lines
 *   - renders **bold** / *italic* / `code` inline
 *
 * The output is intentionally subdued — small spacing, muted heading
 * colour, no code highlight — so it sits well inside the transcript
 * sidebar without fighting the rest of the UI.
 */

import { Fragment, type ReactElement, type ReactNode } from "react";

type Block =
  | { kind: "heading"; level: 1 | 2 | 3; text: string }
  | { kind: "list"; ordered: boolean; items: string[] }
  | { kind: "paragraph"; text: string };

export function Prose({ text }: { text: string }): ReactElement {
  const blocks = parseBlocks(text);
  return (
    <div className="prose-soft space-y-2 text-[12px] leading-relaxed text-[var(--text-primary)]">
      {blocks.map((b, i) => renderBlock(b, i))}
    </div>
  );
}

function renderBlock(block: Block, key: number): ReactElement {
  if (block.kind === "heading") {
    const sizeClass =
      block.level === 1
        ? "text-[12px]"
        : block.level === 2
          ? "text-[11.5px]"
          : "text-[11px]";
    return (
      <h4
        key={key}
        className={`${sizeClass} mt-2 font-semibold uppercase tracking-wider text-[var(--text-muted)]`}
      >
        {renderInline(block.text)}
      </h4>
    );
  }
  if (block.kind === "list") {
    const ListTag = block.ordered ? "ol" : "ul";
    const markerClass = block.ordered ? "list-decimal" : "list-disc";
    return (
      <ListTag
        key={key}
        className={`${markerClass} ml-4 space-y-0.5 marker:text-[var(--text-muted)]`}
      >
        {block.items.map((item, i) => (
          <li key={i}>{renderInline(item)}</li>
        ))}
      </ListTag>
    );
  }
  return (
    <p key={key} className="whitespace-pre-wrap">
      {renderInline(block.text)}
    </p>
  );
}

function parseBlocks(text: string): Block[] {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];

  let pendingPara: string[] = [];
  let pendingList: { ordered: boolean; items: string[] } | null = null;

  const flushPara = () => {
    if (pendingPara.length > 0) {
      blocks.push({ kind: "paragraph", text: pendingPara.join(" ").trim() });
      pendingPara = [];
    }
  };
  const flushList = () => {
    if (pendingList && pendingList.items.length > 0) {
      blocks.push({ kind: "list", ...pendingList });
    }
    pendingList = null;
  };

  for (const rawLine of lines) {
    const line = rawLine.replace(/\s+$/, "");

    if (line.trim() === "") {
      flushPara();
      flushList();
      continue;
    }

    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushPara();
      flushList();
      const level = Math.min(heading[1].length, 3) as 1 | 2 | 3;
      blocks.push({ kind: "heading", level, text: heading[2].trim() });
      continue;
    }

    const ul = /^\s*[-*+]\s+(.*)$/.exec(line);
    if (ul) {
      flushPara();
      if (!pendingList || pendingList.ordered) {
        flushList();
        pendingList = { ordered: false, items: [] };
      }
      pendingList.items.push(ul[1].trim());
      continue;
    }

    const ol = /^\s*\d+\.\s+(.*)$/.exec(line);
    if (ol) {
      flushPara();
      if (!pendingList || !pendingList.ordered) {
        flushList();
        pendingList = { ordered: true, items: [] };
      }
      pendingList.items.push(ol[1].trim());
      continue;
    }

    flushList();
    pendingPara.push(line.trim());
  }
  flushPara();
  flushList();

  return blocks;
}

// ---------- Inline formatting ----------

type InlineToken =
  | { kind: "text"; value: string }
  | { kind: "code"; value: string }
  | { kind: "strong"; value: string }
  | { kind: "em"; value: string }
  | { kind: "link"; value: string; href: string };

function renderInline(text: string): ReactNode {
  return tokenizeInline(text).map((tok, i) => {
    switch (tok.kind) {
      case "text":
        return <Fragment key={i}>{tok.value}</Fragment>;
      case "code":
        return (
          <code
            key={i}
            className="rounded bg-[var(--bg-elevated)] px-1 py-px font-mono text-[11px] text-[var(--text-primary)]"
          >
            {tok.value}
          </code>
        );
      case "strong":
        return (
          <strong key={i} className="font-semibold text-[var(--text-primary)]">
            {tok.value}
          </strong>
        );
      case "em":
        return (
          <em key={i} className="italic text-[var(--text-primary)]">
            {tok.value}
          </em>
        );
      case "link":
        return (
          <a
            key={i}
            href={tok.href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[var(--accent)] underline-offset-2 hover:underline"
          >
            {tok.value}
          </a>
        );
      default:
        return null;
    }
  });
}

// Single-pass tokenizer that walks the string looking for the leftmost
// marker at each step. Order matters: code spans win over emphasis so
// `**inside `code`**` keeps the code intact.
function tokenizeInline(text: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  let i = 0;
  while (i < text.length) {
    const rest = text.slice(i);

    // `code`
    const code = /^`([^`\n]+)`/.exec(rest);
    if (code) {
      tokens.push({ kind: "code", value: code[1] });
      i += code[0].length;
      continue;
    }
    // [text](href)
    const link = /^\[([^\]]+)\]\(([^)\s]+)\)/.exec(rest);
    if (link) {
      tokens.push({ kind: "link", value: link[1], href: link[2] });
      i += link[0].length;
      continue;
    }
    // **strong** or __strong__
    const strong = /^(\*\*|__)([^*_\n]+)\1/.exec(rest);
    if (strong) {
      tokens.push({ kind: "strong", value: strong[2] });
      i += strong[0].length;
      continue;
    }
    // *em* or _em_   (require non-word char before to avoid matching `foo_bar`)
    const em = /^(\*|_)([^*_\n]+)\1/.exec(rest);
    if (em) {
      tokens.push({ kind: "em", value: em[2] });
      i += em[0].length;
      continue;
    }

    // Plain text up to the next potential marker.
    const next = rest.search(/[`*_[]/);
    if (next === -1) {
      tokens.push({ kind: "text", value: rest });
      break;
    }
    if (next === 0) {
      // The marker didn't match a complete pattern — eat one char as text
      // so we don't loop forever.
      tokens.push({ kind: "text", value: rest[0] });
      i += 1;
      continue;
    }
    tokens.push({ kind: "text", value: rest.slice(0, next) });
    i += next;
  }
  return tokens;
}
