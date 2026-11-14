import { scenarioLabel } from "./ComparisonCard";

// Task 12: the comparison panel showed the raw scenario id ("typical"). Display only; the id itself is unchanged.
describe("scenarioLabel", () => {
  it("names the contract scenarios in both languages", () => {
    expect(scenarioLabel("typical", "uk")).toBe("Типові умови");
    expect(scenarioLabel("typical", "en")).toBe("Typical conditions");
    expect(scenarioLabel("drought_2024", "uk")).toBe("Посуха (як у 2024)");
    expect(scenarioLabel("heat_plus_2_5c", "en")).toBe("Heat +2.5 °C");
  });

  it("shows an unknown id as it is, including names inherited from Object", () => {
    expect(scenarioLabel("frost_2027", "uk")).toBe("frost_2027");
    expect(scenarioLabel("constructor", "en")).toBe("constructor");
  });
});
