import { isValidElement, useEffect } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { useModelDoc } from "../api/queries";
import { Skeleton } from "../components/Skeleton";
import styles from "./ModelDocPage.module.css";

// the doc's relative links (rniv.md, challenger_garch.md) are repo files the
// SPA never serves: send them to the repo's blob view instead of the router
const DOC_LINK_BASE = "https://github.com/rohankannan/riskdesk/blob/main/docs/";

// stable anchor ids from heading text, shared by the TOC and the headings
const slug = (text: string): string =>
  text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");

interface TocEntry {
  level: 2 | 3;
  text: string;
}

// TOC from the raw markdown: ## and ### lines outside fenced code blocks
function tocEntries(markdown: string): TocEntry[] {
  const entries: TocEntry[] = [];
  let inFence = false;
  for (const line of markdown.split("\n")) {
    if (line.startsWith("```")) inFence = !inFence;
    if (inFence) continue;
    const m = /^(#{2,3})\s+(.*)$/.exec(line);
    if (m) entries.push({ level: m[1].length as 2 | 3, text: m[2].trim() });
  }
  return entries;
}

const textOf = (children: React.ReactNode): string =>
  Array.isArray(children)
    ? children.map(textOf).join("")
    : isValidElement<{ children?: React.ReactNode }>(children)
      ? textOf(children.props.children)
      : String(children ?? "");

export default function ModelDocPage() {
  const doc = useModelDoc();

  // the browser's own fragment scroll runs while the skeleton is still up,
  // so a deep link re-attempts it once the headings exist
  useEffect(() => {
    if (doc.isSuccess && window.location.hash) {
      document.getElementById(window.location.hash.slice(1))?.scrollIntoView();
    }
  }, [doc.isSuccess]);

  if (doc.isPending) {
    return (
      <div className={styles.layout}>
        <Skeleton height={300} />
        <Skeleton height={600} />
      </div>
    );
  }
  if (doc.isError) throw doc.error;

  const entries = tocEntries(doc.data.markdown);

  return (
    <div className={styles.layout}>
      <nav className={styles.toc} aria-label="Model document contents">
        <div className={styles.tocTitle}>MODEL DOC</div>
        {entries.map((e) => (
          <a
            key={slug(e.text)}
            href={`#${slug(e.text)}`}
            className={e.level === 3 ? styles.h3 : undefined}
          >
            {e.text}
          </a>
        ))}
      </nav>
      <article className={styles.doc}>
        <Markdown
          // two-tilde-only strikethrough: the doc uses single ~ as an
          // approximation marker, not markup
          remarkPlugins={[[remarkGfm, { singleTilde: false }]]}
          components={{
            h2: ({ children }) => <h2 id={slug(textOf(children))}>{children}</h2>,
            h3: ({ children }) => <h3 id={slug(textOf(children))}>{children}</h3>,
            a: ({ href = "", children }) =>
              href.endsWith(".md") ? (
                <a href={DOC_LINK_BASE + href} target="_blank" rel="noreferrer">
                  {children}
                </a>
              ) : (
                <a href={href}>{children}</a>
              ),
          }}
        >
          {doc.data.markdown}
        </Markdown>
      </article>
    </div>
  );
}
