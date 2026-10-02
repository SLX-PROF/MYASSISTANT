import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ArrowDown } from "lucide-react";
import { api, streamChat } from "../lib/api";
import { useStore } from "../lib/store";
import type { ChatEvent, ChatMessage } from "../lib/types";
import { Composer, type ComposerHandle } from "../components/Composer";
import { MessageList, type Draft, type UIMessage } from "../components/MessageList";
import { Orb, type OrbState } from "../components/Orb";

const NEAR_BOTTOM = 140;
let tempId = -1;

export function ChatPage({ conversationId, onOrbState }: { conversationId: number | null; onOrbState: (s: OrbState) => void }) {
  const { navigate, refreshConversations, toast, bumpItems, onLiveMessage, me } = useStore();
  const [messages, setMessages] = useState<UIMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [orbState, setOrbState] = useState<OrbState>("idle");
  const [atBottom, setAtBottom] = useState(true);
  const [unseen, setUnseen] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);
  const composer = useRef<ComposerHandle>(null);
  const skipLoad = useRef<number | null>(null);
  const busy = draft !== null;
  const busyRef = useRef(false);
  busyRef.current = busy;

  useEffect(() => onOrbState(orbState), [orbState, onOrbState]);

  const scrollToBottom = useCallback((smooth = true) => {
    const el = scroller.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
    setUnseen(0);
  }, []);

  /* --------------------------------------------------------- loading */
  const load = useCallback(async (cid: number) => {
    setLoading(true);
    try {
      const page = await api.messages(cid);
      setMessages(page.messages);
      setHasMore(page.has_more);
      setDraft(page.running ? { text: "", tools: [], cards: [] } : null);
      requestAnimationFrame(() => scrollToBottom(false));
      api.markRead({ conversation_id: cid }).then(refreshConversations).catch(() => undefined);
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setLoading(false);
    }
  }, [refreshConversations, scrollToBottom, toast]);

  useEffect(() => {
    if (conversationId === null) {
      setMessages([]);
      setHasMore(false);
      setDraft(null);
      return;
    }
    if (skipLoad.current === conversationId) {
      skipLoad.current = null;
      return;
    }
    load(conversationId);
  }, [conversationId, load]);

  // Another tab / the server finished something in this conversation.
  useEffect(() => {
    const h = (e: Event) => {
      const cid = (e as CustomEvent<number>).detail;
      if (cid === conversationId && !busyRef.current) load(cid);
    };
    window.addEventListener("atlas:conversation-updated", h);
    return () => window.removeEventListener("atlas:conversation-updated", h);
  }, [conversationId, load]);
  // Fired reminders arriving live.
  useEffect(
    () =>
      onLiveMessage((m: ChatMessage) => {
        if (m.conversation_id !== conversationId) return;
        setMessages((prev) => (prev.some((x) => x.id === m.id) ? prev : [...prev, m]));
        if (!document.hidden) api.markRead({ conversation_id: m.conversation_id }).then(refreshConversations).catch(() => undefined);
      }),
    [conversationId, onLiveMessage, refreshConversations],
  );

  const loadEarlier = async () => {
    const el = scroller.current;
    const first = messages.find((m) => m.id > 0);
    if (!el || !first || conversationId === null) return;
    const prevHeight = el.scrollHeight;
    try {
      const page = await api.messages(conversationId, first.id);
      setMessages((prev) => [...page.messages, ...prev]);
      setHasMore(page.has_more);
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight - prevHeight;
      });
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  /* -------------------------------------------------------- scrolling */
  const onScroll = () => {
    const el = scroller.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM;
    setAtBottom(near);
    if (near) setUnseen(0);
  };

  const prevCount = useRef(0);
  useLayoutEffect(() => {
    const grew = messages.length > prevCount.current;
    prevCount.current = messages.length;
    if (atBottom) scrollToBottom(true);
    else if (grew) setUnseen((n) => n + 1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length, draft?.text, draft?.tools.length, draft?.cards.length]);

  /* ---------------------------------------------------------- sending */
  const handleEvent = (ev: ChatEvent, optimisticId: number, cid: number) => {
    switch (ev.type) {
      case "user_message":
        setMessages((prev) => prev.map((m) => (m.id === optimisticId ? ev.message : m)));
        break;
      case "delta":
        setOrbState("speaking");
        setDraft((d) => (d ? { ...d, text: d.text + ev.text } : d));
        break;
      case "assistant_message":
        setMessages((prev) => [...prev, ev.message]);
        setDraft((d) => (d ? { ...d, text: "" } : d));
        break;
      case "tool_start":
        setOrbState("thinking");
        setDraft((d) => (d ? { ...d, tools: [...d.tools, { label: ev.label, state: "run" }] } : d));
        break;
      case "tool_result":
        setDraft((d) => {
          if (!d) return d;
          const tools = [...d.tools];
          const i = tools.findIndex((t) => t.state === "run");
          if (i >= 0) tools[i] = { ...tools[i], state: ev.is_error ? "failed" : "done", error: ev.error };
          return { ...d, tools, cards: ev.card ? [...d.cards, ev.card] : d.cards };
        });
        break;
      case "tool_message":
        setMessages((prev) => [...prev, ev.message]);
        setDraft((d) => (d ? { ...d, cards: [], tools: d.tools.filter((t) => t.state === "failed") } : d));
        bumpItems();
        break;
      case "error":
        setMessages((prev) => [
          ...prev,
          { id: tempId--, conversation_id: cid, role: "assistant", text: ev.message, cards: [], created_at: new Date().toISOString(), error: true },
        ]);
        break;
      case "done":
        break;
    }
  };

  const send = async (text: string) => {
    if (busy) return;
    let cid = conversationId;
    try {
      if (cid === null) {
        const conv = await api.createConversation();
        cid = conv.id;
        skipLoad.current = cid;
        navigate({ name: "chat", id: cid }, true);
      }
    } catch (e) {
      toast((e as Error).message, "error");
      return;
    }
    const optimistic: UIMessage = { id: tempId--, conversation_id: cid, role: "user", text, cards: [], created_at: new Date().toISOString(), pending: true };
    setMessages((prev) => [...prev, optimistic]);
    setDraft({ text: "", tools: [], cards: [] });
    setOrbState("thinking");
    setAtBottom(true);
    requestAnimationFrame(() => scrollToBottom(true));
    try {
      await streamChat(cid, text, (ev) => handleEvent(ev, optimistic.id, cid!));
    } catch (e) {
      setMessages((prev) => [
        ...prev.map((m) => (m.id === optimistic.id ? { ...m, pending: false } : m)),
        { id: tempId--, conversation_id: cid!, role: "assistant", text: (e as Error).message, cards: [], created_at: new Date().toISOString(), error: true },
      ]);
    } finally {
      setDraft(null);
      setOrbState("idle");
      refreshConversations();
    }
  };

  /* ------------------------------------------------------------ view */
  const empty = !loading && messages.length === 0 && !busy;
  return (
    <div className="chat">
      {empty ? (
        <div className="empty-chat">
          <Orb size={96} state={orbState} />
          <h2>Чем могу помочь?</h2>
          <p>
            {me?.assistant_name ?? "Атлас"} поставит напоминание, заведёт задачу или запомнит важное. Например: «Напомни через 2 минуты выпить воды».
          </p>
        </div>
      ) : (
        <div className="messages scroll" ref={scroller} onScroll={onScroll} aria-live="off">
          <div className="messages-inner">
            {hasMore && (
              <button type="button" className="btn btn-sm btn-ghost load-more" onClick={loadEarlier}>
                Показать более ранние
              </button>
            )}
            <MessageList messages={messages} draft={draft} orbState={orbState} />
          </div>
        </div>
      )}
      <div style={{ position: "relative" }}>
        {!atBottom && !empty && (
          <button type="button" className="icon-btn scroll-down" onClick={() => scrollToBottom(true)} aria-label="К последнему сообщению">
            <ArrowDown size={18} aria-hidden="true" />
            {unseen > 0 && <span className="badge">{unseen}</span>}
          </button>
        )}
        <Composer ref={composer} busy={busy} onSend={send} />
      </div>
    </div>
  );
}
