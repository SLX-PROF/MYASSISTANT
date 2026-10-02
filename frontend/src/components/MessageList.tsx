import { Fragment, memo } from "react";
import { CircleCheck, TriangleAlert } from "lucide-react";
import { formatDay, formatTime, localDateKey } from "../lib/format";
import type { CardData, ChatMessage } from "../lib/types";
import { ToolCard } from "./Cards";
import { Markdown } from "./Markdown";
import { Orb, type OrbState } from "./Orb";

export interface UIMessage extends ChatMessage {
  pending?: boolean;
  error?: boolean;
}

export interface Draft {
  text: string;
  tools: { label: string; state: "run" | "done" | "failed"; error?: string | null }[];
  cards: CardData[];
}

type Group = { key: string; me: boolean; day: string; items: UIMessage[] };

function visible(m: UIMessage) {
  return m.text.trim() !== "" || (m.cards && m.cards.length > 0);
}

function groupMessages(messages: UIMessage[]): Group[] {
  const groups: Group[] = [];
  for (const m of messages) {
    if (!visible(m)) continue;
    const me = m.role === "user";
    const day = localDateKey(new Date(m.created_at));
    const last = groups[groups.length - 1];
    if (last && last.me === me && last.day === day) last.items.push(m);
    else groups.push({ key: `g${m.id}`, me, day, items: [m] });
  }
  return groups;
}

const MessageItem = memo(function MessageItem({ m }: { m: UIMessage }) {
  if (m.role === "user") {
    return <div className={`bubble me ${m.pending ? "pending" : ""}`}>{m.text}</div>;
  }
  return (
    <>
      {m.role === "assistant" && m.text.trim() && (
        <div className={`bubble bot ${m.error ? "error" : ""}`}>
          {m.error ? (
            <span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}>
              <TriangleAlert size={15} aria-hidden="true" /> {m.text}
            </span>
          ) : (
            <Markdown text={m.text} />
          )}
        </div>
      )}
      {m.cards?.map((c, i) => <ToolCard key={`${m.id}-${i}`} card={c} />)}
    </>
  );
});

interface Props {
  messages: UIMessage[];
  draft: Draft | null;
  orbState: OrbState;
}

export function MessageList({ messages, draft, orbState }: Props) {
  const groups = groupMessages(messages);
  const lastIsBot = groups.length > 0 && !groups[groups.length - 1].me;
  const draftVisible = draft !== null;

  const renderDraft = () =>
    draft && (
      <>
        {draft.tools.map((t, i) => (
          <div key={`t${i}`} className={`tool-status ${t.state === "run" ? "" : t.state}`} role="status">
            {t.state === "run" ? <span className="spinner" aria-hidden="true" /> : t.state === "done" ? <CircleCheck size={13} aria-hidden="true" /> : <TriangleAlert size={13} aria-hidden="true" />}
            {t.state === "failed" ? `Не получилось: ${t.error ?? t.label}` : t.label}
          </div>
        ))}
        {draft.cards.map((c, i) => (
          <ToolCard key={`dc${i}`} card={c} />
        ))}
        {draft.text ? (
          <div className="bubble bot" aria-live="polite">
            <Markdown text={draft.text} />
            <span className="caret" aria-hidden="true" />
          </div>
        ) : (
          draft.tools.every((t) => t.state !== "run") && (
            <div className="bubble bot typing" role="status" aria-label="Атлас печатает">
              <span />
              <span />
              <span />
            </div>
          )
        )}
      </>
    );

  return (
    <>
      {groups.map((g, gi) => {
        const prev = groups[gi - 1];
        const showDay = !prev || prev.day !== g.day;
        const isLast = gi === groups.length - 1;
        const last = g.items[g.items.length - 1];
        return (
          <Fragment key={g.key}>
            {showDay && <div className="day-sep">{formatDay(g.items[0].created_at)}</div>}
            <div className={`group ${g.me ? "me" : "bot"}`}>
              {!g.me && <Orb small size={26} className="avatar" />}
              <div className="group-body">
                {g.items.map((m) => (
                  <MessageItem key={m.id} m={m} />
                ))}
                {isLast && lastIsBot && draftVisible && renderDraft()}
                {!(isLast && lastIsBot && draftVisible) && <div className="msg-time">{formatTime(last.created_at)}</div>}
              </div>
            </div>
          </Fragment>
        );
      })}
      {draftVisible && !lastIsBot && (
        <div className="group bot">
          <Orb small size={26} className="avatar" state={orbState} />
          <div className="group-body">{renderDraft()}</div>
        </div>
      )}
    </>
  );
}
