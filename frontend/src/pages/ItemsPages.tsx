import { useCallback, useEffect, useState } from "react";
import { AlarmClock, Brain, Plus, Repeat, Trash2, X } from "lucide-react";
import { api } from "../lib/api";
import { formatDateTime, formatDue, isOverdue, localInputToIso, nextHourInput, tzShort } from "../lib/format";
import { useStore } from "../lib/store";
import type { FactCardData, ReminderCardData, TaskCardData } from "../lib/types";
import { Checkbox } from "../components/Cards";

function useList<T>(loader: () => Promise<T[]>, deps: unknown[]) {
  const { itemsVersion, toast } = useStore();
  const [items, setItems] = useState<T[] | null>(null);
  const reload = useCallback(async () => {
    try {
      setItems(await loader());
    } catch (e) {
      toast((e as Error).message, "error");
      setItems((x) => x ?? []);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    reload();
  }, [reload, itemsVersion]);
  return { items, setItems, reload };
}

function Segmented<T extends string>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map(([v, l]) => (
        <button key={v} type="button" aria-pressed={v === value} onClick={() => onChange(v)}>
          {l}
        </button>
      ))}
    </div>
  );
}

/* ================================================================ tasks */

export function TaskList({ compact = false }: { compact?: boolean }) {
  const { toast, bumpItems } = useStore();
  const [status, setStatus] = useState<"open" | "done">("open");
  const { items, setItems } = useList<TaskCardData>(() => api.tasks(status), [status]);
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    try {
      await api.createTask({ title: title.trim(), due: due || null });
      setTitle("");
      setDue("");
      bumpItems();
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };

  const toggle = async (t: TaskCardData, done: boolean) => {
    setItems((xs) => xs?.map((x) => (x.id === t.id ? { ...x, status: done ? "done" : "open" } : x)) ?? null);
    try {
      await api.updateTask(t.id, { done });
      toast(done ? "Задача выполнена" : "Задача снова открыта", "info", {
        label: "Отменить",
        run: () => api.updateTask(t.id, { done: !done }).then(bumpItems),
      });
      setTimeout(bumpItems, 600);
    } catch (err) {
      toast((err as Error).message, "error");
      bumpItems();
    }
  };

  const remove = async (t: TaskCardData) => {
    try {
      await api.deleteTask(t.id);
      bumpItems();
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };

  return (
    <>
      <form className="surface add-form" onSubmit={add}>
        <label className="sr-only" htmlFor={`task-title-${compact}`}>
          Новая задача
        </label>
        <input id={`task-title-${compact}`} className="input grow" placeholder="Новая задача…" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={300} />
        {!compact && (
          <>
            <label className="sr-only" htmlFor="task-due">
              Срок
            </label>
            <input id="task-due" className="input" type="date" value={due} onChange={(e) => setDue(e.target.value)} />
          </>
        )}
        <button type="submit" className="btn btn-solid" disabled={!title.trim()} aria-label="Добавить задачу">
          <Plus size={16} aria-hidden="true" /> {!compact && "Добавить"}
        </button>
      </form>
      <Segmented label="Фильтр задач" value={status} onChange={setStatus} options={[["open", "Открытые"], ["done", "Выполненные"]]} />
      <ul className="surface list">
        {items === null && <li className="list-empty">Загрузка…</li>}
        {items?.length === 0 && <li className="list-empty">{status === "open" ? "Открытых задач нет." : "Пока ничего не выполнено."}</li>}
        {items?.map((t) => {
          const dueLabel = formatDue(t.due_at, t.due_has_time);
          const late = t.status === "open" && isOverdue(t.due_at, !t.due_has_time);
          return (
            <li key={t.id} className={`list-item ${t.status === "done" ? "is-done" : ""}`}>
              <Checkbox checked={t.status === "done"} onChange={(v) => toggle(t, v)} label={`Выполнено: ${t.title}`} />
              <div className="main-col">
                <div className="title">{t.title}</div>
                {(dueLabel || t.notes) && (
                  <div className="meta">
                    {dueLabel && <span className={late ? "overdue" : ""}>{late ? `Просрочено · ${dueLabel}` : dueLabel}</span>}
                    {t.notes && !compact && <span>{t.notes}</span>}
                  </div>
                )}
              </div>
              {!compact && (
                <div className="row-actions">
                  <button type="button" className="icon-btn plain" onClick={() => remove(t)} aria-label={`Удалить задачу ${t.title}`}>
                    <Trash2 size={16} aria-hidden="true" />
                  </button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}

export function TasksPage() {
  return (
    <div className="page scroll">
      <div className="page-inner">
        <div className="page-header sr-only">
          <h2>Задачи</h2>
        </div>
        <TaskList />
      </div>
    </div>
  );
}

/* ============================================================ reminders */

const WEEKDAYS: [string, string][] = [
  ["mon", "Пн"],
  ["tue", "Вт"],
  ["wed", "Ср"],
  ["thu", "Чт"],
  ["fri", "Пт"],
  ["sat", "Сб"],
  ["sun", "Вс"],
];

export function ReminderList({ compact = false }: { compact?: boolean }) {
  const { toast, bumpItems } = useStore();
  const [status, setStatus] = useState<"active" | "done">("active");
  const { items } = useList<ReminderCardData>(
    () => (status === "active" ? api.reminders("active") : api.reminders("all").then((xs) => xs.filter((x) => x.status !== "active"))),
    [status],
  );
  const [text, setText] = useState("");
  const [when, setWhen] = useState(nextHourInput);
  const [rec, setRec] = useState<"none" | "daily" | "weekly" | "monthly" | "quarterly" | "yearly">("none");
  const [days, setDays] = useState<string[]>([]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || !when) return;
    try {
      await api.createReminder({
        text: text.trim(),
        when: localInputToIso(when),
        recurrence: rec === "quarterly" ? "monthly" : rec,
        interval_months: rec === "quarterly" ? 3 : 1,
        weekdays: rec === "weekly" && days.length ? days : undefined,
      });
      setText("");
      toast("Напоминание создано");
      bumpItems();
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };

  const cancel = async (r: ReminderCardData) => {
    try {
      await api.cancelReminder(r.id);
      toast("Напоминание отменено");
      bumpItems();
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };

  return (
    <>
      <form className="surface add-form" onSubmit={add}>
        <label className="sr-only" htmlFor={`rem-text-${compact}`}>
          Текст напоминания
        </label>
        <input id={`rem-text-${compact}`} className="input grow" placeholder="О чём напомнить…" value={text} onChange={(e) => setText(e.target.value)} maxLength={500} />
        <label className="sr-only" htmlFor={`rem-when-${compact}`}>
          Когда
        </label>
        <input id={`rem-when-${compact}`} className="input grow" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
        {!compact && (
          <>
            <label className="sr-only" htmlFor="rem-rec">
              Повтор
            </label>
            <select id="rem-rec" className="select" value={rec} onChange={(e) => setRec(e.target.value as typeof rec)}>
              <option value="none">Без повтора</option>
              <option value="daily">Каждый день</option>
              <option value="weekly">По дням недели</option>
              <option value="monthly">Каждый месяц</option>
              <option value="quarterly">Раз в квартал</option>
              <option value="yearly">Каждый год</option>
            </select>
          </>
        )}
        <button type="submit" className="btn btn-solid" disabled={!text.trim() || !when} aria-label="Создать напоминание">
          <Plus size={16} aria-hidden="true" /> {!compact && "Создать"}
        </button>
        {rec === "weekly" && !compact && (
          <div className="swatches" role="group" aria-label="Дни недели" style={{ width: "100%" }}>
            {WEEKDAYS.map(([code, label]) => (
              <button
                key={code}
                type="button"
                className="chip"
                aria-pressed={days.includes(code)}
                onClick={() => setDays((d) => (d.includes(code) ? d.filter((x) => x !== code) : [...d, code]))}
              >
                {label}
              </button>
            ))}
          </div>
        )}
        <p className="hint" style={{ width: "100%" }}>
          Время — {tzShort()}
        </p>
      </form>
      <Segmented label="Фильтр напоминаний" value={status} onChange={setStatus} options={[["active", "Активные"], ["done", "Архив"]]} />
      <ul className="surface list">
        {items === null && <li className="list-empty">Загрузка…</li>}
        {items?.length === 0 && <li className="list-empty">{status === "active" ? "Активных напоминаний нет." : "Архив пуст."}</li>}
        {items?.map((r) => (
          <li key={r.id} className={`list-item ${r.status !== "active" ? "is-done" : ""}`}>
            <AlarmClock size={18} aria-hidden="true" style={{ color: "var(--accent)", flexShrink: 0 }} />
            <div className="main-col">
              <div className="title">{r.text}</div>
              <div className="meta">
                <span>
                  {r.next_fire_at
                    ? formatDateTime(r.next_fire_at)
                    : r.status === "cancelled"
                      ? "Отменено"
                      : r.last_fired_at
                        ? `Сработало ${formatDateTime(r.last_fired_at)}`
                        : "Выполнено"}
                </span>
                {r.recurrence.kind !== "none" && (
                  <span>
                    <Repeat size={11} aria-hidden="true" /> {r.recurrence_label}
                  </span>
                )}
              </div>
            </div>
            {r.status === "active" && (
              <div className="row-actions">
                <button type="button" className="icon-btn plain" onClick={() => cancel(r)} aria-label={`Отменить напоминание ${r.text}`}>
                  <X size={17} aria-hidden="true" />
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </>
  );
}

export function RemindersPage() {
  return (
    <div className="page scroll">
      <div className="page-inner">
        <div className="page-header sr-only">
          <h2>Напоминания</h2>
        </div>
        <ReminderList />
      </div>
    </div>
  );
}

/* =============================================================== memory */

export function MemoryPage() {
  const { toast, bumpItems } = useStore();
  const { items } = useList<FactCardData>(() => api.facts(), []);
  const [text, setText] = useState("");
  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim()) return;
    try {
      await api.createFact(text.trim());
      setText("");
      bumpItems();
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };
  const forget = async (f: FactCardData) => {
    try {
      await api.deleteFact(f.id);
      toast("Факт удалён из памяти");
      bumpItems();
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };
  return (
    <div className="page scroll">
      <div className="page-inner">
        <div className="page-header sr-only">
          <h2>Память</h2>
        </div>
        <p className="hint">Эти факты Jarvis учитывает в каждом разговоре. Удалите всё, что больше не актуально.</p>
        <form className="surface add-form" onSubmit={add}>
          <label className="sr-only" htmlFor="fact-text">
            Новый факт
          </label>
          <input id="fact-text" className="input grow" placeholder="Например: пью кофе без сахара" value={text} onChange={(e) => setText(e.target.value)} maxLength={500} />
          <button type="submit" className="btn btn-solid" disabled={!text.trim()}>
            <Plus size={16} aria-hidden="true" /> Запомнить
          </button>
        </form>
        <ul className="surface list">
          {items === null && <li className="list-empty">Загрузка…</li>}
          {items?.length === 0 && <li className="list-empty">Пока ничего не сохранено.</li>}
          {items?.map((f) => (
            <li key={f.id} className="list-item">
              <Brain size={18} aria-hidden="true" style={{ color: "var(--accent)", flexShrink: 0 }} />
              <div className="main-col">
                <div className="title">{f.text}</div>
              </div>
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => forget(f)} aria-label={`Забыть: ${f.text}`}>
                Забыть
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
