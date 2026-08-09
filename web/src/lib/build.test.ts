import { sameCommit } from "./build";

// mirrors tests/test_prod_parity.py - the same rule has to hold on both sides,
// or the footer and the CI check would disagree about what "stale" means
test("sameCommit compares on the common prefix", () => {
  expect(sameCommit("8736842abcdef0123456", "8736842")).toBe(true);
  expect(sameCommit("8736842", "8736842abcdef")).toBe(true);
  expect(sameCommit("8736842abcdef", "c3b8689abcdef")).toBe(false);
});

test("sameCommit treats a dirty tree as a different commit", () => {
  expect(sameCommit("8736842-dirty", "8736842")).toBe(false);
  expect(sameCommit("8736842", "8736842-dirty")).toBe(false);
  expect(sameCommit("8736842-dirty", "8736842-dirty")).toBe(true);
});

test("sameCommit stays quiet when either stamp is missing", () => {
  // the offline snapshot carries no build_version; the footer must not cry drift
  expect(sameCommit(null, "8736842")).toBe(true);
  expect(sameCommit(undefined, "8736842")).toBe(true);
  expect(sameCommit("8736842", null)).toBe(true);
  expect(sameCommit("", "8736842")).toBe(true);
});
