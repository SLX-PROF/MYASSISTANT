import { useState } from "react";
import { AlarmClock, BellRing, Brain, Check, ListChecks, Repeat } from "lucide-react";
import { api } from "../lib/api";
import { formatDateTime, formatDue, isOverdue, tzShort } from "../lib/format";
import { useStore } from "../lib/store";
import type { CardData, FactCardData, ReminderCardData, TaskCardData } from "../lib/types";

export function Checkbox({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="checkbox" aria-checked={checked} aria-label={label} className="check" onClick={() => onChange(!checked)}>
      <Check size={14} strokeWidth={3} />
    </button>
  );
}

function useAction() {
  const { toast, bumpItems } = useStore();
  const [busy, setBusy] = useState(false);
  const run = async <T,>(fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
    setBusy(true);
    try {
      const r = await fn();
      if (ok) toast(ok);
      bumpItems();
      return r;
    } catch (e) {
      toast((e as Error).message, "error");
      return undefined;
    } finally {
      setBusy(false);
    }
  };
  return { busy, run };
}

export function ReminderCard({ card: initial }: { card: ReminderCardData }) {
  const [card, setCard] = useState(initial);
  const { busy, run } = useAction();
  const active = card.status === "active";
  const cancel = async () => {
    const r = await run(() => api.cancelReminder(card.id), "Напоминание отменено");
    if (r) setCard(r);
  };
  return (
    <div className={`card ${active ? "" : "is-off"}`}>
      <div className="card-kicker">
        <AlarmClock size={15} aria-hidden="true" />
        {active ? "Напоминание" : card.status === "cancelled" ? "Напоминание отменено" : "Напоминание выполнено"}
      </div>
      <div className="card-title">{card.text}</div>
      <div className="card-meta">
        {card.next_fire_at ? `${formatDateTime(card.next_fire_at)} · ${tzShort()}` : "—"}
        {card.recurrence.kind !== "none" && (
          <>
            {" · "}
            <Repeat size={11} aria-hidden="true" /> {card.recurrence_label}
          </>
        )}
      </div>
      {active && (
        <div className="card-actions">
          <button type="button" className="btn btn-sm" disabled={busy} onClick={cancel}>
            Отменить
          </button>
        </div>
      )}
    </div>
  );
}

export function TaskCard({ card: initial }: { card: TaskCardData }) {
  const [card, setCard] = useState(initial);
  const { run } = useAction();
  const done = card.status === "done";
  const due = formatDue(card.due_at, card.due_has_time);
  const toggle = async (v: boolean) => {
    setCard({ ...card, status: v ? "done" : "open" });
    const r = await run(() => api.updateTask(card.id, { done: v }));
    if (r) setCard(r);
    else setCard(card);
  };
  return (
    <div className={`card ${done ? "is-off" : ""}`}>
      <div className="card-kicker">
        <ListChecks size={15} aria-hidden="true" />
        {done ? "Задача выполнена" : "Задача"}
      </div>
      <div style={{ display: "flex", gap: 10, alignItems: "flex-start", marginTop: 8 }}>
        <Checkbox checked={done} onChange={toggle} label={`Выполнено: ${card.title}`} />
        <div style={{ minWidth: 0 }}>
          <div className="card-title" style={{ marginTop: 0 }}>
            {card.title}
          </div>
          {(due || card.notes) && (
            <div className="card-meta">
              {due && <span className={isOverdue(card.due_at, !card.due_has_time) && !done ? "overdue" : ""}>{due}</span>}
              {due && card.notes ? " · " : ""}
              {card.notes}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function FactCard({ card }: { card: FactCardData }) {
  const [gone, setGone] = useState(false);
  const { busy, run } = useAction();
  const forget = async () => {
    const r = await run(() => api.deleteFact(card.id), "Факт удалён из памяти");
    if (r) setGone(true);
  };
  return (
    <div className={`card ${gone ? "is-off" : ""}`}>
      <div className="card-kicker">
        <Brain size={15} aria-hidden="true" />
        {gone ? "Забыто" : "Запомнил"}
      </div>
      <div className="card-title">{card.text}</div>
      {!gone && (
        <div className="card-actions">
          <button type="button" className="btn btn-sm" disabled={busy} onClick={forget}>
            Забыть
          </button>
        </div>
      )}
    </div>
  );
}

function ReminderRow({ r: initial }: { r: ReminderCardData }) {
  const [r, setR] = useState(initial);
  const { busy, run } = useAction();
  return (
    <div className="row">
      <AlarmClock size={16} aria-hidden="true" style={{ color: "var(--accent)", flexShrink: 0 }} />
      <div className="row-main">
        <div className="row-title">{r.text}</div>
        <div className="row-meta">
          {r.next_fire_at ? formatDateTime(r.next_fire_at) : r.status === "cancelled" ? "отменено" : "выполнено"}
          {r.recurrence.kind !== "none" && ` · ${r.recurrence_label}`}
        </div>
      </div>
      {r.status === "active" && (
        <button
          type="button"
          className="btn btn-sm btn-ghost"
          disabled={busy}
          onClick={async () => {
            const x = await run(() => api.cancelReminder(r.id), "Напоминание отменено");
            if (x) setR(x);
          }}
        >
          Отменить
        </button>
      )}
    </div>
  );
}

function TaskRow({ t: initial }: { t: TaskCardData }) {
  const [t, setT] = useState(initial);
  const { run } = useAction();
  const due = formatDue(t.due_at, t.due_has_time);
  return (
    <div className="row">
      <Checkbox
        checked={t.status === "done"}
        label={`Выполнено: ${t.title}`}
        onChange={async (v) => {
          setT({ ...t, status: v ? "done" : "open" });
          const x = await run(() => api.updateTask(t.id, { done: v }));
          setT(x ?? t);
        }}
      />
      <div className="row-main">
        <div className="row-title" style={t.status === "done" ? { textDecoration: "line-through", color: "var(--muted)" } : undefined}>
          {t.title}
        </div>
        {due && <div className="row-meta">{due}</div>}
      </div>
    </div>
  );
}

function ListCard({ kicker, icon, empty, children, count }: { kicker: string; icon: React.ReactNode; empty: string; children: React.ReactNode; count: number }) {
  return (
    <div className="card wide">
      <div className="card-kicker">
        {icon}
        {kicker}
      </div>
      {count === 0 ? <div className="card-empty">{empty}</div> : <div className="card-list">{children}</div>}
    </div>
  );
}

export function ToolCard({ card }: { card: CardData }) {
  switch (card.type) {
    case "reminder":
      return <ReminderCard card={card} />;
    case "task":
      return <TaskCard card={card} />;
    case "fact":
      return <FactCard card={card} />;
    case "reminder_list":
      return (
        <ListCard kicker="Напоминания" icon={<AlarmClock size={15} aria-hidden="true" />} empty="Активных напоминаний нет." count={card.items.length}>
          {card.items.map((r) => (
            <ReminderRow key={r.id} r={r} />
          ))}
        </ListCard>
      );
    case "task_list":
      return (
        <ListCard kicker="Задачи" icon={<ListChecks size={15} aria-hidden="true" />} empty="Задач нет. Отличный день." count={card.items.length}>
          {card.items.map((t) => (
            <TaskRow key={t.id} t={t} />
          ))}
        </ListCard>
      );
    case "fact_list":
      return (
        <ListCard kicker="Память" icon={<Brain size={15} aria-hidden="true" />} empty="Пока ничего не запомнил." count={card.items.length}>
          {card.items.map((f) => (
            <div className="row" key={f.id}>
              <div className="row-main">
                <div className="row-title">{f.text}</div>
              </div>
            </div>
          ))}
        </ListCard>
      );
    case "fact_forgotten":
      return (
        <div className="card is-off">
          <div className="card-kicker muted">
            <Brain size={15} aria-hidden="true" />
            Забыто
          </div>
          <div className="card-title">{card.text}</div>
        </div>
      );
    case "reminder_fired":
      return (
        <div className="card">
          <div className={`card-kicker ${card.overdue ? "warn" : ""}`}>
            <BellRing size={15} aria-hidden="true" />
            {card.overdue ? "Просроченное напоминание" : "Напоминание"}
          </div>
          <div className="card-title">{card.text}</div>
          <div className="card-meta">
            {card.overdue && card.scheduled_for ? `Было на ${formatDateTime(card.scheduled_for)}` : formatDateTime(card.fired_at)}
            {card.next_fire_at && ` · следующее: ${formatDateTime(card.next_fire_at)}`}
          </div>
        </div>
      );
    default:
      return null;
  }
}
