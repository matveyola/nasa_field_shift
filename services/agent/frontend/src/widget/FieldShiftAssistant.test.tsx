import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axe from "axe-core";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fakeServer, fixtureMessage, frame, kansas, manualStream, mockMedia, poltava, sse, turn } from "../test/helpers";
import type { AgentMessage, FieldContext } from "./contracts";
import { requestProblem } from "./contracts";
import { FieldShiftAssistant, type FieldShiftAssistantProps, type HostCapabilities } from "./index";

const caps: HostCapabilities = { actions: ["map.set_layers", "compare.set_constraints", "scenario.set"], simulation: false };

function clean(ctx: FieldContext): FieldContext {
  return { ...ctx, constraints: [] };
}

function setup(over: Partial<FieldShiftAssistantProps> & { fetch?: typeof fetch } = {}) {
  const onAction = vi.fn(over.onAction ?? (() => ({ status: "applied" as const })));
  const props: FieldShiftAssistantProps = {
    transport: { baseUrl: "/agent", fetch: over.fetch ?? fakeServer(sse(turn(fixtureMessage))).fetch },
    fieldContext: clean(poltava()),
    locale: "en",
    capabilities: caps,
    ...over,
    onAction,
  };
  const user = userEvent.setup();
  const view = render(<FieldShiftAssistant {...props} />);
  const rerender = (p: Partial<FieldShiftAssistantProps>) => view.rerender(<FieldShiftAssistant {...props} {...p} onAction={onAction} />);
  return { user, onAction, rerender, props, unmount: view.unmount };
}

async function openAndAsk(user: ReturnType<typeof userEvent.setup>, text = "What is known about my field?") {
  await user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
  const box = await screen.findByRole("textbox", { name: "Message to the assistant" });
  await user.type(box, `${text}{Enter}`);
}

beforeEach(() => mockMedia([]));
afterEach(() => vi.restoreAllMocks());

describe("launcher and panel", () => {
  it("starts closed, opens with the keyboard, closes with Escape and returns focus", async () => {
    const { user } = setup();
    const launcher = screen.getByRole("button", { name: "Open Field Assistant" });
    expect(launcher).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("dialog")).toBeNull();

    launcher.focus();
    await user.keyboard("{Enter}");
    const dialog = await screen.findByRole("dialog", { name: "Field Assistant" });
    expect(dialog).not.toHaveAttribute("aria-modal"); // desktop: the map stays usable
    expect(screen.getByRole("button", { name: "Close Field Assistant" })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("textbox", { name: "Message to the assistant" })).toHaveFocus();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(screen.getByRole("button", { name: "Open Field Assistant" })).toHaveFocus();
  });

  it("opens from the host's own button and tells the host when the farmer closes it", async () => {
    const onOpenChange = vi.fn();
    const { user, rerender } = setup({ launcher: false, open: false, onOpenChange });
    expect(screen.queryByRole("button", { name: "Open Field Assistant" })).toBeNull(); // the host's button only
    rerender({ launcher: false, open: true, onOpenChange });
    await screen.findByRole("dialog", { name: "Field Assistant" });
    expect(onOpenChange).toHaveBeenLastCalledWith(true);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(onOpenChange).toHaveBeenLastCalledWith(false);
  });

  it("shows two pulse rings, and none under prefers-reduced-motion", () => {
    const first = setup();
    expect(document.querySelectorAll(".fsa-pulse > span")).toHaveLength(2);
    first.unmount();
    mockMedia(["(prefers-reduced-motion: reduce)"]);
    setup();
    expect(document.querySelector(".fsa-pulse")).toBeNull();
    expect(screen.getByRole("button", { name: "Open Field Assistant" })).toHaveAttribute("data-motion", "reduced");
  });

  it("is a modal full-screen dialog on phones, with Tab kept inside", async () => {
    mockMedia(["(max-width: 640px)"]);
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.queryByRole("button", { name: /^(Open|Close) Field Assistant$/ })).toBeNull(); // launcher hidden
    for (let i = 0; i < 12; i++) await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("names the real model only when the service reports one (Task 14)", async () => {
    const health = (body: object) =>
      (async (input: RequestInfo | URL) =>
        String(input).endsWith("/health/ready")
          ? new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } })
          : new Response("", { status: 500 })) as typeof fetch;
    const gemini = setup({ fetch: health({ status: "ready", agent: "llm/gemini", model: "gemini-3.8-flash" }) });
    await gemini.user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    expect(await screen.findByTestId("agent-kind")).toHaveTextContent(/^Answered by AI \(gemini-3\.8-flash\)\. It can make mistakes\.$/);
    gemini.unmount();
    const scripted = setup({ fetch: health({ status: "ready", agent: "llm/scripted-llm", model: "gemini-3.8-flash" }) });
    await scripted.user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    expect(await screen.findByTestId("agent-kind")).toHaveTextContent(/^Answers are prepared in advance, not AI\.$/);
  });

  it("field strip: one honest line, and the map line only where the page has a map", async () => {
    const rules = (async () => new Response(JSON.stringify({ status: "ready", agent: "deterministic-dev" }), { status: 200 })) as typeof fetch;
    const withMap = setup({ fetch: rules });
    await withMap.user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    const kind = await screen.findByTestId("agent-kind");
    expect(kind.parentElement).toHaveTextContent(/^The numbers are made up\. Answers come from ready-made rules, not AI\.$/);
    expect(screen.getByText("Example")).toBeInTheDocument();
    expect(screen.getByText(/^On the map:/)).toBeInTheDocument();
    withMap.unmount();
    const noMap = setup({ fetch: rules, capabilities: { actions: ["compare.set_constraints"], simulation: false } });
    await noMap.user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    await screen.findByTestId("agent-kind");
    expect(screen.queryByText(/On the map/)).toBeNull();
  });

  it("offers at most four suggestions, and the input hint mentions speaking only where the browser can", async () => {
    const llm = (async () =>
      new Response(JSON.stringify({ status: "ready", agent: "llm/gemini", knowledge: { mode: "lexical" }, integrations: { simulation: { connected: true, compare_rotations: true, service_mode: "stub" } } }), {
        status: 200,
      })) as typeof fetch;
    const { user } = setup({ fetch: llm, capabilities: { ...caps, simulation: true } });
    await user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    const group = await screen.findByRole("group", { name: "Suggested questions" });
    await waitFor(() => expect(within(group).getByRole("button", { name: "What should I plant next? Compare rotations." })).toBeInTheDocument());
    expect(within(group).getAllByRole("button")).toHaveLength(4); // the FAO question is the fifth: left out
    expect(screen.getByRole("textbox", { name: "Message to the assistant" })).toHaveAttribute("placeholder", "Type a question…"); // jsdom: no speech
  });

  it("without a field, explains why and sends nothing", async () => {
    const server = fakeServer(sse(turn(fixtureMessage)));
    const { user } = setup({ fieldContext: null, fetch: server.fetch });
    await user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    expect(await screen.findByText(/Select a field on the map to start/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message to the assistant" })).toBeDisabled();
    expect(server.requests).toHaveLength(0);
  });

  it("is localised: Ukrainian copy and labels", async () => {
    const { user } = setup({ locale: "uk" });
    await user.click(screen.getByRole("button", { name: "Відкрити: Польовий помічник" }));
    expect(await screen.findByText(/Привіт! Я допоможу розібратися з вашим полем/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Повідомлення помічнику" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Що відомо про моє поле?" })).toBeInTheDocument();
  });
});

describe("conversation with the agent service", () => {
  it("sends a contract-valid AgentRequest and renders the final message once", async () => {
    const server = fakeServer(sse(turn(fixtureMessage, [{ event: "text.delta", text: "Draft that must not stay. " }])));
    const { user } = setup({ fetch: server.fetch });
    await openAndAsk(user);
    expect(await screen.findByText(/адаптивна сівозміна має нижчий ризик/)).toBeInTheDocument();
    const body = server.requests[0]!.body as { field_context: FieldContext; message: { text: string }; locale: string };
    expect(requestProblem(body)).toBeNull();
    expect(body.message.text).toBe("What is known about my field?");
    expect(body.field_context.field_id).toBe("demo-poltava-01");
    expect(body.locale).toBe("en");
    expect(screen.queryByText(/Draft that must not stay/)).toBeNull();
    expect(screen.getAllByText(/адаптивна сівозміна має нижчий ризик/)).toHaveLength(1);
    expect(screen.getByText(/These are demo numbers for the example, not real measurements/)).toBeInTheDocument();
    // The clarifying question offers its options as buttons that send a reply.
    expect(screen.getByRole("group", { name: "Choose an answer" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Так, є схил" })).toBeEnabled();
  });

  it("shows tool activity only for tools that actually started", async () => {
    const s = manualStream();
    const { user } = setup({ fetch: s.fetch });
    await openAndAsk(user);
    await act(async () => s.push(frame({ event: "turn.started", turn_id: fixtureMessage.turn_id })));
    expect(screen.queryByText("Looking it up in scientific guidance…")).toBeNull();
    await act(async () => s.push(frame({ event: "tool.started", call_id: "c1", tool: "search_knowledge" })));
    expect(await screen.findAllByText("Looking it up in scientific guidance…")).not.toHaveLength(0); // step + live region
    await act(async () => s.push(frame({ event: "turn.completed", message: fixtureMessage })));
    await waitFor(() => expect(screen.queryByText("Looking it up in scientific guidance…")).toBeNull());
  });

  it("a duplicated turn.completed frame never duplicates the message (M6C)", async () => {
    const s = manualStream();
    const { user } = setup({ fetch: s.fetch });
    await openAndAsk(user);
    await act(async () => s.push(frame({ event: "turn.started", turn_id: fixtureMessage.turn_id })));
    const completed = frame({ event: "turn.completed", message: fixtureMessage });
    await act(async () => s.push(completed + completed)); // both in one chunk: the parser sees the duplicate
    await waitFor(() => expect(screen.getAllByText(/адаптивна сівозміна має нижчий ризик/)).toHaveLength(1));
  });

  it("opens a verified citation with its exact quote and a safe source link, then returns focus", async () => {
    const { user } = setup();
    await openAndAsk(user);
    const marker = await screen.findByRole("button", { name: /^Source 1: Crop evapotranspiration/ });
    await user.click(marker);
    const detail = screen.getByRole("region", { name: "Source 1" });
    expect(within(detail).getByText(fixtureMessage.citations![0]!.quote)).toBeInTheDocument();
    expect(within(detail).getByText("Chapter 8")).toBeInTheDocument();
    const link = within(detail).getByRole("link", { name: /View original source/ });
    expect(link).toHaveAttribute("href", "https://www.fao.org/4/x0490e/x0490e00.htm");
    expect(link).toHaveAttribute("rel", expect.stringContaining("noopener"));
    // the covered conversation can't take focus or be read while the source is open
    expect(document.querySelector(".fsa-scroll")).toHaveAttribute("inert");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(marker).toHaveFocus());
    expect(document.querySelector(".fsa-scroll")).not.toHaveAttribute("inert");
    expect(screen.getByRole("dialog")).toBeInTheDocument(); // Escape closed the source, not the assistant
  });

  it("does not link a citation URL outside the approved hosts", async () => {
    const evil: AgentMessage = { ...fixtureMessage, citations: [{ ...fixtureMessage.citations![0]!, url: "https://evil.example/fao56" }] };
    const { user } = setup({ fetch: fakeServer(sse(turn(evil))).fetch });
    await openAndAsk(user);
    await user.click(await screen.findByRole("button", { name: /^Source 1:/ }));
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(/not an approved source address/)).toBeInTheDocument();
  });

  it("keeps proposed constraints out of the host until the farmer confirms", async () => {
    const { user, onAction, rerender, props } = setup();
    await openAndAsk(user, "No irrigation, and I want to cut fertiliser costs.");
    expect(await screen.findByText("No irrigation")).toBeInTheDocument();
    // a1 in the fixture is an `auto` compare.set_constraints: it must still wait for a click.
    expect(onAction).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Confirm condition: No irrigation" }));
    expect(onAction).toHaveBeenCalledTimes(1);
    const req = onAction.mock.calls[0]![0]!;
    expect(req.origin).toBe("farmer_click");
    expect(req.envelope.action).toMatchObject({ type: "compare.set_constraints", constraints: [{ id: "k_irrigation", status: "confirmed", value: false }] });

    // The chip says "Confirmed" only once the host's context holds the confirmed constraint.
    const confirmed = req.envelope.action.type === "compare.set_constraints" ? req.envelope.action.constraints : [];
    rerender({ fieldContext: { ...props.fieldContext!, revision: 4, constraints: confirmed } });
    const item = screen.getByText("No irrigation").closest("li")!;
    expect(within(item).getByText("Confirmed")).toBeInTheDocument();
    expect(within(item).queryByRole("button", { name: /Confirm/ })).toBeNull();
    // The other proposal is untouched.
    expect(screen.getByRole("button", { name: "Confirm condition: Minimise nitrogen fertiliser" })).toBeInTheDocument();
  });

  it("dismissing a proposal changes nothing in the host", async () => {
    const { user, onAction } = setup();
    await openAndAsk(user);
    await user.click(await screen.findByRole("button", { name: "Dismiss condition: No irrigation" }));
    expect(onAction).not.toHaveBeenCalled();
    expect(screen.getByText("Dismissed")).toBeInTheDocument();
  });

  it("applies a map chip through the host, and refuses it once the field view has changed", async () => {
    const { user, onAction, rerender, props } = setup({ onAction: () => ({ status: "applied", revision: 4 }) });
    await openAndAsk(user);
    const chip = await screen.findByRole("button", { name: /Шар вологості SMAP/ });
    // The chart module is not available in this host: shown, but not clickable.
    expect(screen.getByRole("button", { name: /Дефіцит вологи/ })).toBeDisabled();

    rerender({ fieldContext: { ...props.fieldContext!, revision: 7 } }); // the farmer changed the map meanwhile
    await user.click(chip);
    expect(onAction).not.toHaveBeenCalled();
    expect(screen.getByText(/the field view changed since this answer/)).toBeInTheDocument();
  });

  it("re-stamps a still-current chip and reports the host's result", async () => {
    const { user, onAction } = setup({ onAction: () => ({ status: "applied", revision: 4 }) });
    await openAndAsk(user);
    await user.click(await screen.findByRole("button", { name: /Шар вологості SMAP/ }));
    expect(onAction).toHaveBeenCalledTimes(1);
    const req = onAction.mock.calls[0]![0]!;
    expect([req.envelope.action.type, req.envelope.field_context_revision, req.fieldId]).toEqual(["map.set_layers", 3, "demo-poltava-01"]);
    expect(await screen.findByText("Applied")).toBeInTheDocument();
  });

  it("switching fields mid-answer cancels it: nothing reaches the new field", async () => {
    const s = manualStream();
    const { user, onAction, rerender } = setup({ fetch: s.fetch });
    await openAndAsk(user);
    await act(async () => s.push(frame({ event: "turn.started", turn_id: fixtureMessage.turn_id })));
    rerender({ fieldContext: kansas() });
    await waitFor(() => expect(s.aborted).toBe(true));
    expect(onAction).not.toHaveBeenCalled();
    // Field B starts with its own, empty conversation and can be used at once.
    expect(screen.getByText(/I can help you understand your field/)).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Message to the assistant" })).toBeEnabled();
    // Field A's conversation records why its answer stopped.
    rerender({ fieldContext: clean(poltava()) });
    expect(await screen.findByText(/Stopped because you switched fields/)).toBeInTheDocument();
  });

  it("closing the panel stops the running answer", async () => {
    const s = manualStream();
    const { user } = setup({ fetch: s.fetch });
    await openAndAsk(user);
    await user.click(screen.getByRole("button", { name: "Close assistant" }));
    await waitFor(() => expect(s.aborted).toBe(true));
  });

  it("shows a recoverable error when the service is offline, and retries only on request", async () => {
    let online = false;
    const good = fakeServer(sse(turn(fixtureMessage)));
    const f = (async (input: RequestInfo | URL, init?: RequestInit) => {
      if (!online) throw new TypeError("Failed to fetch");
      return good.fetch(input, init);
    }) as typeof fetch;
    const { user } = setup({ fetch: f });
    await openAndAsk(user);
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((a) => /Can't reach the assistant right now/.test(a.textContent ?? ""))).toBe(true);
    expect(good.requests).toHaveLength(0);
    online = true;
    const retry = screen.getAllByRole("button", { name: "Try again" }).at(-1)!;
    await user.click(retry);
    expect(await screen.findByText(/адаптивна сівозміна/)).toBeInTheDocument();
    expect(good.requests).toHaveLength(1);
  });

  it("has no axe violations with an answer, sources and chips on screen", async () => {
    const { user } = setup();
    await openAndAsk(user);
    await screen.findByRole("button", { name: /^Source 1:/ });
    const results = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } }); // jsdom cannot compute colours
    expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(", ")}`)).toEqual([]);
  });
});

describe("name and composer", () => {
  type Handlers = { onresult?: (e: object) => void; onerror?: (e: object) => void; onend?: () => void };
  let started: (Handlers & { lang: string })[] = [];
  class FakeRecognition {
    lang = "";
    interimResults = false;
    continuous = false;
    onresult: Handlers["onresult"];
    onerror: Handlers["onerror"];
    onend: Handlers["onend"];
    start() {
      started.push(this);
    }
    stop() {
      this.onend?.();
    }
    abort() {
      this.onend?.();
    }
  }
  const speech = window as unknown as { SpeechRecognition?: unknown };
  beforeEach(() => {
    started = [];
  });
  afterEach(() => {
    delete speech.SpeechRecognition;
  });

  it("uses the host's agent name for the panel title and the launcher", async () => {
    const { user } = setup({ agentName: "Жучок" });
    await user.click(screen.getByRole("button", { name: "Open Жучок" }));
    expect(await screen.findByRole("dialog", { name: "Жучок" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Close Жучок" })).toBeVisible();
  });

  it("shows the attachment button as unavailable: it never opens a file picker", async () => {
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    const clip = await screen.findByRole("button", { name: "Attach a file: not supported yet" });
    expect(clip).toHaveAttribute("aria-disabled", "true");
    await user.click(clip);
    expect(document.querySelector("input[type=file]")).toBeNull();
  });

  it("dictates into the message box with the browser's speech recognition, in the widget's language", async () => {
    speech.SpeechRecognition = FakeRecognition;
    const { user } = setup();
    await user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    const box = await screen.findByRole("textbox", { name: "Message to the assistant" });
    await user.type(box, "Soil:");
    const mic = screen.getByRole("button", { name: /^Dictate a message/ });
    expect(mic).toHaveAttribute("aria-pressed", "false");
    await user.click(mic);
    expect(started).toHaveLength(1);
    expect(started[0]!.lang).toBe("en-US");
    expect(mic).toHaveAttribute("aria-pressed", "true"); // a toggle: same name, pressed state
    act(() => started[0]!.onresult?.({ results: [[{ transcript: "how wet is it" }]] }));
    expect(box).toHaveValue("Soil: how wet is it"); // appended, never sent by itself
    await user.click(mic);
    expect(mic).toHaveAttribute("aria-pressed", "false");
    expect(box).toHaveFocus(); // stopped from the button: back to the text

    await user.click(mic);
    await user.type(box, "!"); // typing takes over: dictation stops and speech can no longer overwrite it
    expect(mic).toHaveAttribute("aria-pressed", "false");
    act(() => started[1]!.onresult?.({ results: [[{ transcript: "late words" }]] }));
    expect(box).toHaveValue("Soil: how wet is it!");
  });

  it("explains a refused microphone, and labels the button when the browser has no speech recognition", async () => {
    speech.SpeechRecognition = FakeRecognition;
    const first = setup();
    await first.user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    await first.user.click(await screen.findByRole("button", { name: /^Dictate a message/ }));
    act(() => {
      started[0]!.onerror?.({ error: "not-allowed" });
      started[0]!.onend?.();
    });
    expect(screen.getByText(/^No access to the microphone/)).toHaveAttribute("role", "status");
    first.unmount();

    delete speech.SpeechRecognition;
    const second = setup();
    await second.user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    expect(await screen.findByRole("button", { name: "Voice input is not supported in this browser" })).toHaveAttribute("aria-disabled", "true");
  });
});

describe("presentation blocks", () => {
  it("draws cards, a table, bars and a notice from the message's facts", async () => {
    const message = {
      ...fixtureMessage,
      blocks: [
        ...fixtureMessage.blocks,
        { type: "metrics", title: "Water", fact_ids: ["f_sm_rootzone_2025_07"] },
        { type: "table", title: "Rotations", columns: ["Water risk"], scenario: "drought_2024",
          rows: [{ label: "Adaptive", fact_ids: ["rot_adaptive.water_risk"] }, { label: "Usual", fact_ids: [null] }] },
        { type: "bar_chart", title: "Water risk", bars: [{ label: "Adaptive", fact_id: "rot_adaptive.water_risk" },
                                                         { label: "Usual", fact_id: "rot_conventional.water_risk" }] },
        { type: "callout", tone: "warning", text: "Check the label before spraying.", fact_ids: [] },
      ],
    } as unknown as AgentMessage;
    const { user } = setup({ fetch: fakeServer(sse(turn(message))).fetch });
    await openAndAsk(user);
    const cards = await screen.findByRole("region", { name: "Water" });
    expect(within(cards).getByText("Soil moisture around the roots")).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Rotations" });
    expect(within(table).getByRole("rowheader", { name: "Adaptive" })).toBeInTheDocument();
    expect(within(table).getByLabelText("no value")).toBeInTheDocument(); // an empty cell says so
    expect(screen.getByText("Calculated for the “drought (like 2024)” case. A calculation, not a real harvest.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Water risk" })).toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("Check the label before spraying.");
    expect(document.querySelectorAll(".fsa-bar")).toHaveLength(2);
    const results = await axe.run(document.body, { rules: { "color-contrast": { enabled: false } } });
    expect(results.violations).toEqual([]);
  });
});

describe("app screens and going back", () => {
  it("tells the agent what the app can show, opens a screen at once and puts it back on request", async () => {
    const screens = [{ screen_id: "field_map", label: "Field map" }, { screen_id: "soil", label: "Soil", how_to_open: "Field → Soil tab" }];
    const ctx = { ...clean(poltava()), ui_state: { ...poltava().ui_state, screen_id: "field_map" } };
    const message: AgentMessage = {
      ...fixtureMessage,
      ui_actions: [{ id: "a1", field_context_revision: ctx.revision, trigger: "auto", label: "Open the soil screen",
                     action: { type: "app.open_screen", screen_id: "soil" } }],
    };
    let revision = ctx.revision;
    const server = fakeServer(sse(turn(message)));
    const { user, onAction } = setup({
      fetch: server.fetch,
      fieldContext: ctx,
      capabilities: { actions: [...caps.actions, "app.open_screen"], simulation: false, screens },
      onAction: () => ({ status: "applied" as const, revision: ++revision }),
    });
    await openAndAsk(user, "Show me the soil");
    await waitFor(() => expect(onAction).toHaveBeenCalledTimes(1));
    const body = server.requests[0]!.body as { client: { capabilities: { actions: string[]; screens: unknown[] } } };
    expect(body.client.capabilities.actions).toContain("app.open_screen");
    expect(body.client.capabilities.screens).toEqual(screens);
    expect(onAction.mock.calls[0]![0]).toMatchObject({ origin: "agent_auto", envelope: { action: { type: "app.open_screen", screen_id: "soil" } } });
    await user.click(await screen.findByRole("button", { name: "Put it back as it was" }));
    await waitFor(() => expect(onAction).toHaveBeenCalledTimes(2));
    expect(onAction.mock.calls[1]![0]).toMatchObject({ origin: "farmer_click", envelope: { action: { type: "app.open_screen", screen_id: "field_map" } } });
    expect(await screen.findByText("Put back as it was.")).toBeInTheDocument();
  });
});

describe("wide tables in the narrow panel", () => {
  it("shows three or more measures as one card per option", async () => {
    const message = {
      ...fixtureMessage,
      blocks: [...fixtureMessage.blocks, { type: "table", title: "Rotations", columns: ["Water", "Cover", "Nitrogen"],
        rows: [{ label: "Adaptive", fact_ids: ["rot_adaptive.water_risk", null, null] }] }],
    } as unknown as AgentMessage;
    const { user } = setup({ fetch: fakeServer(sse(turn(message))).fetch });
    await openAndAsk(user);
    const cards = await screen.findByRole("region", { name: "Rotations" });
    expect(within(cards).queryByRole("table")).toBeNull();
    expect(within(cards).getByRole("heading", { name: "Adaptive" })).toBeInTheDocument();
    expect(within(cards).getByText("Water")).toBeInTheDocument();
    expect(within(cards).getAllByLabelText("no value")).toHaveLength(2);
  });
});
