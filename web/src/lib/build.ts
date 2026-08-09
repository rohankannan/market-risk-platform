// Build-stamp comparison, mirroring risk/db.py::same_commit.
//
// /api/v1/meta carries two commits: code_version is the batch's, read off the
// run row, and build_version is the API process's own. They agree when the
// deploy is current and separate the moment one stops landing - which is the
// only in-product signal that the service is serving stale code, since the
// batch stamp advances nightly regardless.

// Stamps arrive at different lengths: a host-injected SHA truncated to twelve
// characters against `git describe --always`'s own seven. So this is a
// common-prefix test, not string equality.
export function sameCommit(a: string | null | undefined, b: string | null | undefined): boolean {
  // either side missing means "cannot tell" - the offline snapshot has no
  // running build, and a false drift warning there would be worse than none
  if (!a || !b) return true;
  // an uncommitted tree is not the commit it names
  if (a.includes("-dirty") !== b.includes("-dirty")) return false;
  const x = a.replace(/-dirty$/, "");
  const y = b.replace(/-dirty$/, "");
  const n = Math.min(x.length, y.length);
  return n > 0 && x.slice(0, n) === y.slice(0, n);
}
