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

  it("writes money as the agent's text does: grouped, kopecks only when there are any", () => {
    const nbsp = " ";
    expect(formatQuantity({ value: 7500, unit: "UAH/ha" }, "uk")).toBe(`7${nbsp}500 грн/га`);
    expect(formatQuantity({ value: 22500.5, unit: "USD" }, "uk")).toBe(`22${nbsp}500,50 дол. США`);
    expect(formatQuantity({ value: 1250000, unit: "UAH" }, "en")).toBe("1,250,000 UAH");
    expect(formatQuantity({ value: 120.4, unit: "USD/ha" }, "en")).toBe("120.40 USD/ha");
  });
});
