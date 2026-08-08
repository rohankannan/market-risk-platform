import { render, screen, within } from "@testing-library/react";

import App from "../App";
import modeldoc from "../mocks/fixtures/modeldoc.json";

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  return render(<App />);
}

test("renders the recorded model document with a sticky TOC of its sections", async () => {
  renderAt("/docs");
  const toc = await screen.findByRole("navigation", { name: "Model document contents" });
  // every ## section in the committed doc appears as a TOC anchor
  const sections = modeldoc.markdown
    .split("\n")
    .filter((l: string) => /^##\s/.test(l))
    .map((l: string) => l.replace(/^##\s+/, "").trim());
  expect(sections.length).toBeGreaterThan(3);
  for (const s of sections.slice(0, 4)) {
    const link = within(toc).getByRole("link", { name: s });
    expect(link.getAttribute("href")).toMatch(/^#[a-z0-9-]+$/);
  }
  // the document body renders headings with matching anchor ids
  const first = within(toc).getAllByRole("link")[0];
  const id = first.getAttribute("href")!.slice(1);
  expect(document.getElementById(id)).not.toBeNull();

  // single tildes are approximation markers in this doc, never strikethrough
  expect(document.querySelector("article del")).toBeNull();

  // doc-relative .md links leave the SPA for the repo's blob view
  const mdLink = [...document.querySelectorAll("article a")].find((a) =>
    a.getAttribute("href")?.endsWith(".md"),
  );
  if (mdLink) {
    expect(mdLink.getAttribute("href")).toMatch(
      /^https:\/\/github\.com\/.+\/docs\/.+\.md$/,
    );
  }
});
