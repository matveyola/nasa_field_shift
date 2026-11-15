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

  it.each([false, true])("opens from the host's own button and reports close (reduced motion: %s)", async (reducedMotion) => {
    mockMedia(reducedMotion ? ["(prefers-reduced-motion: reduce)"] : []);
    const onOpenChange = vi.fn();
    const { user, rerender } = setup({ launcher: false, open: false, onOpenChange });
    expect(screen.queryByRole("button", { name: "Open Field Assistant" })).toBeNull(); // the host's button only
    rerender({ launcher: false, open: true, onOpenChange });
    await screen.findByRole("dialog", { name: "Field Assistant" });
    await waitFor(() => expect(onOpenChange).toHaveBeenLastCalledWith(true));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // DOM removal and the host's passive-effect notification are separate observations.
    await waitFor(() => expect(onOpenChange).toHaveBeenLastCalledWith(false));
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

describe("optional host saved conversations", () => {
  it("is absent by default and leaves the current draft intact when the host opens history", async () => {
    const onOpenHistory = vi.fn();
    const { user, rerender } = setup();
    await user.click(screen.getByRole("button", { name: "Open Field Assistant" }));
    expect(screen.queryByRole("button", { name: "Saved conversations" })).toBeNull();
    const draft = screen.getByRole("textbox", { name: "Message to the assistant" });
    await user.type(draft, "My unsent field question");
    rerender({ onOpenHistory });
    const history = screen.getByRole("button", { name: "Saved conversations" });
    history.focus();
    await user.keyboard("{Enter}");
    expect(onOpenHistory).toHaveBeenCalledTimes(1);
    expect(draft).toHaveValue("My unsent field question");
    expect(screen.getByRole("dialog", { name: "Field Assistant" })).toBeInTheDocument();
  });

  it("keeps a completed current answer when opening the host’s saved history", async () => {
    const onOpenHistory = vi.fn();
    const { user } = setup({ onOpenHistory });
    await openAndAsk(user);
    const answer = await screen.findByText(/адаптивна сівозміна має нижчий ризик/);
    await user.click(screen.getByRole("button", { name: "Saved conversations" }));
    expect(onOpenHistory).toHaveBeenCalledOnce();
    expect(answer).toBeInTheDocument();
  });

  it("does not open the host's history while a turn is running", async () => {
    const stream = manualStream();
    const onOpenHistory = vi.fn();
    const { user } = setup({ fetch: stream.fetch, onOpenHistory });
    await openAndAsk(user);
    const history = screen.getByRole("button", { name: "Saved conversations" });
    expect(history).toBeDisabled();
    await user.click(history);
    expect(onOpenHistory).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(history).toBeEnabled());
  });

  it("localizes the optional history button without changing the host's thread", async () => {
    const onOpenHistory = vi.fn();
    const { user } = setup({ locale: "uk", onOpenHistory });
    await user.click(screen.getByRole("button", { name: "Відкрити: Польовий помічник" }));
    await user.click(screen.getByRole("button", { name: "Збережені розмови" }));
    expect(onOpenHistory).toHaveBeenCalledOnce();
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

  it("hiding a proposal changes nothing in the host and says so", async () => {
    // "Dismiss" is now "Hide": it only hides the proposal here, unlike "Reject", which is saved (Task 05D-1).
    const { user, onAction } = setup();
    await openAndAsk(user);
    const hide = await screen.findByRole("button", { name: "Hide without saving: No irrigation" });
    expect(hide).toHaveAttribute("title", "Only hides it in this conversation. Nothing is saved.");
    await user.click(hide);
    expect(onAction).not.toHaveBeenCalled();
    expect(screen.getByText("Hidden here · not saved")).toBeInTheDocument();
  });

  describe("rejecting a proposal", () => {
    async function reject(over: Parameters<typeof setup>[0] = {}) {
      const ctx = setup(over);
      await openAndAsk(ctx.user, "No irrigation, and I want to cut fertiliser costs.");
      await ctx.user.click(await screen.findByRole("button", { name: "Reject and save: No irrigation" }));
      const item = () => screen.getByText("No irrigation").closest("li")!;
      return { ...ctx, item };
    }

    it("sends the farmer's rejection to the host and shows it as a saved decision", async () => {
      const { onAction, rerender, props, item } = await reject();
      expect(onAction).toHaveBeenCalledTimes(1);
      const req = onAction.mock.calls[0]![0]!;
      expect(req.origin).toBe("farmer_click");
      expect(req.envelope).toMatchObject({ id: "reject.k_irrigation", trigger: "chip", field_context_revision: 3 });
      expect(req.envelope.action).toMatchObject({
        type: "compare.set_constraints",
        constraints: [{ id: "k_irrigation", type: "irrigation_available", value: false, status: "rejected" }],
      });
      // Saved only once the host's context holds the rejection: until then it is still being saved.
      expect(within(item()).getByText("Saving…")).toBeInTheDocument();
      const rejected = req.envelope.action.type === "compare.set_constraints" ? req.envelope.action.constraints : [];
      rerender({ fieldContext: { ...props.fieldContext!, constraints: rejected } });
      expect(within(item()).getByText("Rejected by you · saved")).toBeInTheDocument();
      expect(within(item()).queryByRole("button")).toBeNull();
      expect(within(item()).queryByRole("status")).toBeNull(); // a decision, not an error note
      expect(within(item()).queryByText("Confirmed")).toBeNull();
      // The other proposal is untouched.
      expect(screen.getByRole("button", { name: "Confirm condition: Minimise nitrogen fertiliser" })).toBeInTheDocument();
    });

    it("reports a host failure as not saved, never as a rejection", async () => {
      const { item } = await reject({ onAction: () => ({ status: "rejected", reason: "failed" }) });
      expect(await within(item()).findByText("Not saved: The app could not apply this action.")).toBeInTheDocument();
      expect(within(item()).queryByText("Rejected by you · saved")).toBeNull();
    });

    it("reports a thrown host error as a failure", async () => {
      const { item } = await reject({ onAction: () => { throw new Error("offline"); } });
      expect(await within(item()).findByText("Not saved: The app could not apply this action.")).toBeInTheDocument();
    });

    it("reports a stale field view, without saving", async () => {
      const { item } = await reject({ onAction: () => ({ status: "rejected", reason: "stale_context" }) });
      expect(await within(item()).findByText(/the field view changed since this answer/)).toBeInTheDocument();
      expect(within(item()).queryByText("Rejected by you · saved")).toBeNull();
    });

    it("reopens the choice when the farmer keeps an earlier decision", async () => {
      const { item } = await reject({ onAction: () => ({ status: "rejected", reason: "cancelled" }) });
      await waitFor(() => expect(within(item()).getByRole("button", { name: "Reject and save: No irrigation" })).toBeInTheDocument());
      expect(within(item()).getByText("Proposed")).toBeInTheDocument();
      expect(within(item()).queryByRole("status")).toBeNull(); // no failure message for the farmer's own choice
    });

    it("waits for the host while the farmer reviews a replacement", async () => {
      let answer!: (result: { status: "applied" }) => void;
      const { item } = await reject({ onAction: () => new Promise((resolve) => { answer = resolve; }) });
      expect(within(item()).getByText("Saving…")).toBeInTheDocument();
      await act(async () => answer({ status: "applied" }));
      // Applied, but shown as rejected only once the host's context holds it.
      expect(within(item()).getByText("Saving…")).toBeInTheDocument();
    });

    it("shows a rejection the host already holds as decided earlier, with nothing to click", async () => {
      // Task 05D-2: a decision the host held before this answer reads "earlier", so it is never taken for one just saved.
      const held = { ...poltava().constraints![0]!, id: "pc_irrigation_available", status: "rejected" as const };
      const { user } = setup({ fieldContext: { ...clean(poltava()), constraints: [{ ...held, type: "irrigation_available", value: false }] } });
      await openAndAsk(user, "No irrigation, and I want to cut fertiliser costs.");
      const item = (await screen.findByText("No irrigation")).closest("li")!;
      expect(within(item).getByText("Already rejected earlier · nothing new saved")).toBeInTheDocument();
      expect(within(item).queryByRole("button")).toBeNull();
    });

    it("an agent's action never rejects a condition for the farmer", async () => {
      const auto: AgentMessage = {
        ...fixtureMessage,
        ui_actions: [{
          id: "a_reject", field_context_revision: 3, trigger: "auto", label: "Reject irrigation",
          action: { type: "compare.set_constraints", constraints: [{ ...fixtureMessage.constraints_proposed![0]!, status: "rejected" }] },
        }],
      };
      const { user, onAction } = setup({ fetch: fakeServer(sse(turn(auto))).fetch });
      await openAndAsk(user, "No irrigation, and I want to cut fertiliser costs.");
      await screen.findByText("No irrigation");
      expect(onAction).not.toHaveBeenCalled();
      expect(screen.queryByText("Rejected by you · saved")).toBeNull();
    });
  });

  describe("editing a proposal before deciding", () => {
    // The host's crops whose identity the agent shares (apps/web constraintCrops.ts).
    const planningCrops = [
      { id: "maize", label: "Maize" },
      { id: "soybean", label: "Soy" },
      { id: "sunflower", label: "Sunflower" },
    ];
    const proposing = (constraints: unknown[]): AgentMessage => ({
      ...fixtureMessage,
      ui_actions: [],
      constraints_proposed: constraints as AgentMessage["constraints_proposed"],
    });
    const rotation = { id: "k_len", type: "rotation_length_years", value: 4, status: "proposed", source: "agent_inferred", confidence: 0.6 };
    const noCrops = { id: "k_crops", type: "excluded_crops", value: ["sunflower"], status: "proposed", source: "farmer_text", evidence: "no sunflower" };
    const unshared = { id: "k_crops", type: "excluded_crops", value: ["winter_wheat", "sunflower"], status: "proposed", source: "farmer_text", evidence: "no winter wheat or sunflower" };

    async function ask(over: Parameters<typeof setup>[0] = {}) {
      const ctx = setup(over);
      await openAndAsk(ctx.user, "No irrigation, and I want to cut fertiliser costs.");
      return ctx;
    }
    const item = (text: string | RegExp) => screen.getByText(text).closest("li")!;

    it("edits a value, saves nothing until Confirm, and then sends the farmer's own choice", async () => {
      const { user, onAction, rerender, props } = await ask();
      const edit = await screen.findByRole("button", { name: "Edit before deciding: No irrigation" });
      await user.click(edit);
      const editor = screen.getByRole("group", { name: "Change the condition" });
      expect(within(editor).getByText("Nothing is saved until you confirm or reject it.")).toBeInTheDocument();
      expect(within(editor).getByRole("radio", { name: "Irrigation available" })).toHaveFocus();
      await user.click(within(editor).getByRole("radio", { name: "Irrigation available" }));
      await user.click(screen.getByRole("button", { name: "Use this value" }));
      expect(onAction).not.toHaveBeenCalled(); // editing never saves

      const chip = item("Irrigation available");
      expect(within(chip).getByText("Edited by you · not saved yet")).toBeInTheDocument();
      expect(within(chip).getByText(/Your choice in the app\. The assistant proposed: No irrigation/)).toBeInTheDocument();
      expect(within(chip).queryByText(/From your words/)).toBeNull(); // the farmer did not say this
      expect(screen.getByRole("button", { name: "Edit before deciding: Irrigation available" })).toHaveFocus();

      await user.click(within(chip).getByRole("button", { name: "Confirm condition: Irrigation available" }));
      expect(onAction).toHaveBeenCalledTimes(1);
      const req = onAction.mock.calls[0]![0]!;
      expect(req.origin).toBe("farmer_click");
      const sent = req.envelope.action.type === "compare.set_constraints" ? req.envelope.action.constraints : [];
      expect(sent).toEqual([{ id: "k_irrigation", type: "irrigation_available", value: true, status: "confirmed", source: "farmer_ui" }]);
      expect(within(chip).getByText("Saving…")).toBeInTheDocument();
      // The host holds it (and its revision moved on, which discards undecided edits): the sent value is matched.
      rerender({ fieldContext: { ...props.fieldContext!, revision: 4, constraints: sent } });
      expect(within(item("Irrigation available")).getByText("Confirmed")).toBeInTheDocument();
      expect(screen.queryByText("No irrigation")).toBeNull();
    });

    it("cancelling the editor (button or Escape) changes nothing", async () => {
      const { user, onAction } = await ask();
      await user.click(await screen.findByRole("button", { name: "Edit before deciding: No irrigation" }));
      await user.click(screen.getByRole("radio", { name: "Irrigation available" }));
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("group", { name: "Change the condition" })).toBeNull();
      expect(within(item("No irrigation")).getByText("Proposed")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Edit before deciding: No irrigation" })).toHaveFocus();

      await user.click(screen.getByRole("button", { name: "Edit before deciding: No irrigation" }));
      await user.click(screen.getByRole("radio", { name: "Irrigation available" }));
      await user.keyboard("{Escape}");
      expect(screen.queryByRole("group", { name: "Change the condition" })).toBeNull();
      expect(screen.getByRole("dialog", { name: "Field Assistant" })).toBeInTheDocument(); // the assistant stays open
      expect(within(item("No irrigation")).getByText("Proposed")).toBeInTheDocument();
      expect(onAction).not.toHaveBeenCalled();
    });

    it("refuses an invalid value with an accessible error, and sends nothing", async () => {
      const { user, onAction } = await ask({ fetch: fakeServer(sse(turn(proposing([rotation])))).fetch });
      await user.click(await screen.findByRole("button", { name: "Edit before deciding: Rotation of 4 years" }));
      const years = screen.getByRole("spinbutton", { name: "Rotation length in years (2–6)" });
      await user.clear(years);
      await user.type(years, "9");
      await user.click(screen.getByRole("button", { name: "Use this value" }));
      expect(screen.getByRole("alert")).toHaveTextContent("Choose a whole number of years from 2 to 6.");
      expect(years).toHaveAttribute("aria-invalid", "true");
      expect(years).toHaveAccessibleDescription("Choose a whole number of years from 2 to 6.");
      expect(onAction).not.toHaveBeenCalled();
      await user.clear(years);
      await user.type(years, "5");
      await user.click(screen.getByRole("button", { name: "Use this value" }));
      // An inference the farmer changed is the farmer's choice, no longer the assistant's guess.
      expect(within(item("Rotation of 5 years")).getByText(/Your choice in the app/)).toBeInTheDocument();
      expect(within(item("Rotation of 5 years")).queryByText("Inferred by the assistant")).toBeNull();
    });

    it("edits crops only among those the app and the assistant share, and rejects the edit", async () => {
      const { user, onAction } = await ask({ fetch: fakeServer(sse(turn(proposing([noCrops])))).fetch, planningCrops });
      await user.click(await screen.findByRole("button", { name: "Edit before deciding: Exclude: Sunflower" }));
      expect(screen.getByText("Only crops that this app's planning and the assistant name the same way can be chosen here.")).toBeInTheDocument();
      expect(screen.getAllByRole("checkbox").map((box) => box.closest("label")!.textContent)).toEqual(["Maize", "Soy", "Sunflower"]);
      expect(screen.getByRole("checkbox", { name: "Sunflower" })).toBeChecked();
      await user.click(screen.getByRole("checkbox", { name: "Maize" }));
      await user.click(screen.getByRole("button", { name: "Use this value" }));
      await user.click(screen.getByRole("button", { name: "Reject and save: Exclude: Sunflower, Maize" }));
      const sent = onAction.mock.calls[0]![0]!.envelope.action;
      expect(sent).toEqual({
        type: "compare.set_constraints",
        constraints: [{ id: "k_crops", type: "excluded_crops", value: ["sunflower", "maize"], status: "rejected", source: "farmer_ui" }],
      });
    });

    it("a proposal naming a crop the app and the assistant do not share cannot be edited, only decided as proposed", async () => {
      const { user, onAction } = await ask({ fetch: fakeServer(sse(turn(proposing([unshared])))).fetch, planningCrops });
      await screen.findByText("Exclude: winter wheat, Sunflower");
      const chip = item("Exclude: winter wheat, Sunflower");
      expect(within(chip).queryByRole("button", { name: /^Edit before deciding/ })).toBeNull();
      expect(within(chip).getByText(
        "Can't be edited here: winter wheat has no single name shared by this app's planning list and the assistant. You can still confirm, reject or hide it as proposed.",
      )).toBeInTheDocument();
      expect(within(chip).getByRole("button", { name: "Hide without saving: Exclude: winter wheat, Sunflower" })).toBeInTheDocument();
      expect(within(chip).getByRole("button", { name: "Reject and save: Exclude: winter wheat, Sunflower" })).toBeInTheDocument();
      // Confirmed as proposed: winter wheat is kept exactly, with the farmer's own words.
      await user.click(within(chip).getByRole("button", { name: "Confirm condition: Exclude: winter wheat, Sunflower" }));
      expect(onAction.mock.calls[0]![0]!.envelope.action).toEqual({
        type: "compare.set_constraints",
        constraints: [{ ...unshared, status: "confirmed" }],
      });
    });

    it("a previous crop the app and the assistant do not share cannot be edited either", async () => {
      const previous = { id: "k_prev", type: "previous_crop", value: "winter_wheat", status: "proposed", source: "agent_inferred", confidence: 0.7 };
      await ask({ fetch: fakeServer(sse(turn(proposing([previous])))).fetch, planningCrops });
      expect(await screen.findByRole("button", { name: "Confirm condition: Previous crop: winter wheat" })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Edit before deciding/ })).toBeNull();
      expect(screen.getByText(/^Can't be edited here: winter wheat/)).toBeInTheDocument();
    });

    it("needs at least one crop", async () => {
      const { user, onAction } = await ask({ fetch: fakeServer(sse(turn(proposing([noCrops])))).fetch, planningCrops });
      await user.click(await screen.findByRole("button", { name: "Edit before deciding: Exclude: Sunflower" }));
      await user.click(screen.getByRole("checkbox", { name: "Sunflower" }));
      await user.click(screen.getByRole("button", { name: "Use this value" }));
      expect(screen.getByRole("alert")).toHaveTextContent("Choose at least one crop.");
      expect(screen.getByRole("group", { name: "Crops" })).toHaveAttribute("aria-invalid", "true");
      expect(onAction).not.toHaveBeenCalled();
    });

    it("without the app's crop list, a crop proposal can be decided but not edited", async () => {
      await ask({ fetch: fakeServer(sse(turn(proposing([noCrops])))).fetch });
      expect(await screen.findByRole("button", { name: /^Confirm condition: Exclude/ })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^Edit before deciding: Exclude/ })).toBeNull();
    });

    it("going back to the proposal restores it, words and all", async () => {
      const { user } = await ask();
      await user.click(await screen.findByRole("button", { name: "Edit before deciding: No irrigation" }));
      await user.click(screen.getByRole("radio", { name: "Irrigation available" }));
      await user.click(screen.getByRole("button", { name: "Use this value" }));
      await user.click(screen.getByRole("button", { name: "Back to the assistant's proposal: No irrigation" }));
      expect(within(item("No irrigation")).getByText("Proposed")).toBeInTheDocument();
      expect(within(item("No irrigation")).getByText(/From your words/)).toBeInTheDocument();
    });

    it("another field view discards an open editor and an undecided edit", async () => {
      const { user, rerender, props, onAction } = await ask();
      await user.click(await screen.findByRole("button", { name: "Edit before deciding: No irrigation" }));
      await user.click(screen.getByRole("radio", { name: "Irrigation available" }));
      await user.click(screen.getByRole("button", { name: "Use this value" }));
      await user.click(screen.getByRole("button", { name: "Edit before deciding: Minimise nitrogen fertiliser" }));
      rerender({ fieldContext: { ...props.fieldContext!, revision: 9 } }); // e.g. the boundary changed
      expect(screen.queryByRole("group", { name: "Change the condition" })).toBeNull();
      expect(screen.getByText("No irrigation")).toBeInTheDocument();
      expect(screen.queryByText("Irrigation available")).toBeNull();
      expect(onAction).not.toHaveBeenCalled();
    });

    it("an edit to a decision the host already holds says it was saved earlier, not that it was just saved", async () => {
      const held = { id: "pc_irrigation_available", type: "irrigation_available", value: true, status: "confirmed", source: "farmer_ui" } as const;
      const { user, onAction } = await ask({ fieldContext: { ...clean(poltava()), constraints: [held] } });
      await user.click(await screen.findByRole("button", { name: "Edit before deciding: No irrigation" }));
      await user.click(screen.getByRole("radio", { name: "Irrigation available" }));
      await user.click(screen.getByRole("button", { name: "Use this value" }));
      const chip = item("Irrigation available");
      expect(within(chip).getByText("Already confirmed earlier · nothing new saved")).toBeInTheDocument();
      expect(within(chip).queryByText("Confirmed")).toBeNull();
      expect(within(chip).queryByText("Saving…")).toBeNull();
      expect(onAction).not.toHaveBeenCalled(); // nothing was submitted
      // The farmer can go back to the assistant's proposal and decide that instead.
      await user.click(within(chip).getByRole("button", { name: "Back to the assistant's proposal: No irrigation" }));
      expect(within(item("No irrigation")).getByText("Proposed")).toBeInTheDocument();
    });

    it("a decision made here is shown as made here, even when the host held it already", async () => {
      const { user, onAction, rerender, props } = await ask();
      await user.click(await screen.findByRole("button", { name: "Confirm condition: No irrigation" }));
      const sent = onAction.mock.calls[0]![0]!.envelope.action;
      rerender({ fieldContext: { ...props.fieldContext!, constraints: sent.type === "compare.set_constraints" ? sent.constraints : [] } });
      expect(within(item("No irrigation")).getByText("Confirmed")).toBeInTheDocument();
      expect(within(item("No irrigation")).queryByText(/earlier/)).toBeNull();
    });

    it("keeps the edit when the farmer keeps an earlier decision instead, or the host fails", async () => {
      for (const reason of ["cancelled", "failed"] as const) {
        const { user, unmount } = await ask({ onAction: () => ({ status: "rejected", reason }) });
        await user.click(await screen.findByRole("button", { name: "Edit before deciding: No irrigation" }));
        await user.click(screen.getByRole("radio", { name: "Irrigation available" }));
        await user.click(screen.getByRole("button", { name: "Use this value" }));
        await user.click(screen.getByRole("button", { name: "Confirm condition: Irrigation available" }));
        const chip = item("Irrigation available");
        if (reason === "cancelled") {
          // Nothing changed: the edited choice is offered again, without a failure message.
          await waitFor(() => expect(within(chip).getByText("Edited by you · not saved yet")).toBeInTheDocument());
          expect(within(chip).queryByRole("status")).toBeNull();
        } else {
          expect(await within(chip).findByText("Not saved: The app could not apply this action.")).toBeInTheDocument();
        }
        unmount();
      }
    });
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
