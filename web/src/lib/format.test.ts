import { fmtBp, fmtMoney, fmtMoneyFull, fmtPct, fmtSignedPct, fmtVolPt } from "./format";

// same known answers as the Streamlit ui tests, plus the new formats
test("fmtMoney scales and sign", () => {
  expect(fmtMoney(1_420_000)).toBe("$1.42M");
  expect(fmtMoney(-318_000)).toBe("-$318k");
  expect(fmtMoney(950)).toBe("$950");
  expect(fmtMoney(null)).toBe("-");
  expect(fmtMoney(1_137_118.3)).toBe("$1.14M");
  expect(fmtMoney(-2_610_000)).toBe("-$2.61M");
});

test("fmtMoney half-ties round to even, matching Python's format()", () => {
  expect(fmtMoney(318_500)).toBe("$318k"); // f"{318.5:,.0f}" == "318"
  expect(fmtMoney(319_500)).toBe("$320k");
  expect(fmtMoney(1_125_000)).toBe("$1.12M"); // f"{1.125:,.2f}" == "1.12"
  expect(fmtMoney(-1_135_000)).toBe("-$1.14M");
});

test("fmtMoneyFull keeps cents", () => {
  expect(fmtMoneyFull(1_137_118.3)).toBe("$1,137,118.30");
  expect(fmtMoneyFull(-83_000)).toBe("-$83,000.00");
});

test("fmtPct", () => {
  expect(fmtPct(0.584)).toBe("58.4%");
  expect(fmtPct(null)).toBe("-");
  expect(fmtSignedPct(0.021)).toBe("+2.1%");
  expect(fmtSignedPct(-0.032)).toBe("-3.2%");
});

test("fmtBp signs whole basis points", () => {
  expect(fmtBp(18)).toBe("+18bp");
  expect(fmtBp(-118)).toBe("-118bp");
  expect(fmtBp(0)).toBe("0bp");
});

test("fmtVolPt", () => {
  expect(fmtVolPt(-2749)).toBe("-$2,749/pt");
  expect(fmtVolPt(1200)).toBe("$1,200/pt");
});
