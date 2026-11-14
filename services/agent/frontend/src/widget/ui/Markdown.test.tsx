import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Markdown } from "./Markdown";
import { safeSourceUrl } from "./safeUrl";

const cite = (id: string, key: string) =>
  id === "1" ? (
    <button key={key} data-cite={id}>
      {id}
    </button>
  ) : null;

describe("Markdown", () => {
  it("renders paragraphs, lists, bold, italic and code", () => {
    const { container } = render(<Markdown text={"Intro **bold** and *it*.\n\n- one `x`\n- two\n\n1. first"} cite={cite} />);
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("em")?.textContent).toBe("it");
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelector("ol li")?.textContent).toBe("first");
  });

  it("never creates HTML from model or source text", () => {
    const evil = '<img src=x onerror="alert(1)"> <script>alert(2)</script> <a href="javascript:alert(3)">x</a>';
    const { container } = render(<Markdown text={evil} cite={cite} />);
    expect(container.querySelector("img, script, a")).toBeNull();
    expect(container.textContent).toContain("<img src=x");
  });

  it("drops link targets and keeps only the visible text", () => {
    const { container } = render(<Markdown text={"See [FAO](https://evil.example/phish) now"} cite={cite} />);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toBe("See FAO now");
  });

  it("keeps a citation's trailing punctuation with it, so a line never starts with a lone '.'", () => {
    const { container } = render(<Markdown text={"Water stress [1]. Unknown [7]."} cite={cite} />);
    const kept = container.querySelector(".fsa-nowrap");
    expect(kept?.textContent).toBe("1.");
    expect(kept?.querySelector("[data-cite='1']")).not.toBeNull();
    expect(container.textContent).toBe("Water stress 1. Unknown [7].");
  });

  it("turns [n] into a citation only when the message has that citation", () => {
    const { container } = render(<Markdown text={"Known [1], unknown [7]."} cite={cite} />);
    expect(container.querySelectorAll("[data-cite]")).toHaveLength(1);
    expect(container.textContent).toContain("unknown [7]");
  });
});

describe("safeSourceUrl", () => {
  const hosts = ["www.fao.org"];
  it("allows https on approved hosts only", () => {
    expect(safeSourceUrl("https://www.fao.org/4/x0490e/x0490e0b.htm#tabulated%20kc", hosts)).toBe("https://www.fao.org/4/x0490e/x0490e0b.htm#tabulated%20kc");
    for (const bad of [
      "http://www.fao.org/x",
      "javascript:alert(1)",
      "https://www.fao.org.evil.example/x",
      "https://user:pw@www.fao.org/x",
      "data:text/html,hi",
      "//www.fao.org/x",
      "not a url",
    ]) {
      expect(safeSourceUrl(bad, hosts)).toBeNull();
    }
  });
});
