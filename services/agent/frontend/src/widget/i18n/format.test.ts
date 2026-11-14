import { describe, expect, it } from "vitest";
import { formatQuantity } from "./format";

describe("formatQuantity", () => {
  it("writes Ukrainian units and days that agree with the number", () => {
    expect(formatQuantity({ value: 70, unit: "kg/ha" }, "uk")).toBe("70 кг/га");
    expect(formatQuantity({ value: 290, unit: "d" }, "uk")).toBe("290 днів");
    expect(formatQuantity({ value: 22, unit: "d" }, "uk")).toBe("22 дні");
    expect(formatQuantity({ value: 21, unit: "d" }, "uk")).toBe("21 день");
    expect(formatQuantity({ value: 0.21, unit: "m3/m3" }, "uk")).toBe("0,21 м³/м³");
    expect(formatQuantity({ value: 290, unit: "d" }, "en")).toBe("290 days");
    expect(formatQuantity({ value: 70, unit: "kg/ha" }, "en")).toBe("70 kg/ha");
  });
});
