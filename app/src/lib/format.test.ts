import { dateUTC, decimal, money, parseDec, pct, price, qty, relative, sign } from "./format";

describe("parseDec", () => {
  it("handles plain, signed and exponent forms without floats", () => {
    expect(parseDec("0E-12")).toEqual({ negative: false, int: "0", frac: "000000000000" });
    expect(parseDec("-12.08")).toEqual({ negative: true, int: "12", frac: "08" });
    expect(parseDec("1.5e3")).toEqual({ negative: false, int: "1500", frac: "" });
    expect(parseDec("abc")).toBeNull();
    expect(parseDec(null)).toBeNull();
  });
});

describe("decimal rounding", () => {
  it("rounds half away from zero on the digit string", () => {
    expect(decimal("0.125", { dp: 2 })).toBe("0.13");
    expect(decimal("-0.125", { dp: 2 })).toBe("−0.13");
    expect(decimal("9.999", { dp: 2 })).toBe("10.00");
    expect(decimal("999999.995", { dp: 2 })).toBe("1,000,000.00");
  });

  it("keeps precision that a float would lose", () => {
    // 0.1 + 0.2 style artefacts must never reach the screen.
    expect(decimal("0.30000000000000004441", { dp: 2 })).toBe("0.30");
    expect(decimal("0.2500000000000000000000000001", { dp: 4 })).toBe("0.2500");
    expect(decimal("12345678901234567890.55", { dp: 1 })).toBe("12,345,678,901,234,567,890.6");
  });

  it("never shows negative zero", () => {
    expect(decimal("-0.001", { dp: 2 })).toBe("0.00");
    expect(money("-0E-12", { signed: true })).toBe("$0.00");
  });
});

describe("money / qty / pct / price", () => {
  it("formats money with sign and grouping", () => {
    expect(money("10482.17")).toBe("$10,482.17");
    expect(money("82.17", { signed: true })).toBe("+$82.17");
    expect(money("-12.08", { signed: true })).toBe("−$12.08");
    expect(money(undefined)).toBe("—");
  });

  it("trims quantities", () => {
    expect(qty("7.326007326007326007326007326")).toBe("7.326007");
    expect(qty("0.310000")).toBe("0.31");
    expect(qty("2")).toBe("2");
  });

  it("formats percents and prices", () => {
    expect(pct("1.8", { dp: 1 })).toBe("1.8%");
    expect(pct("-0.52", { signed: true })).toBe("−0.52%");
    expect(price("79166.01000000")).toBe("79,166.01");
    expect(price("105.00")).toBe("105");
    expect(price("0.00012345")).toBe("0.000123");
  });

  it("derives sign from strings", () => {
    expect(sign("-0.0")).toBe(0);
    expect(sign("-3")).toBe(-1);
    expect(sign("0E-12")).toBe(0);
    expect(sign("0.01")).toBe(1);
  });
});

describe("dates", () => {
  it("treats naive backend timestamps as UTC", () => {
    expect(dateUTC("2026-09-15T10:55:59.743498")).toBe("15 sep 10:55:59 UTC");
    expect(dateUTC("2026-09-15T10:55:59Z", { seconds: false, zone: false })).toBe("15 sep 10:55");
  });

  it("describes relative time in Spanish", () => {
    const now = new Date("2026-09-25T12:00:00Z");
    expect(relative("2026-09-25T11:57:00Z", now)).toBe("hace 3 min");
    expect(relative("2026-09-25T09:00:00Z", now)).toBe("hace 3 h");
    expect(relative("2026-09-24T12:00:00Z", now)).toBe("hace 1 día");
  });
});

describe("exact decimal arithmetic", () => {
  it("adds and subtracts without float error", async () => {
    const { addDec, subDec } = await import("./format");
    expect(addDec("0.1", "0.2")).toBe("0.3");
    expect(addDec("82.17", "41.20")).toBe("123.37");
    expect(subDec("10520.00", "10523.37")).toBe("-3.37");
    expect(addDec("0E-12", null, "-1.5")).toBe("-1.5");
  });
});
