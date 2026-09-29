import { Bell, Plus } from "lucide-react";
import { MessageList, type UIMessage } from "../components/MessageList";
import { Orb } from "../components/Orb";
import { Checkbox } from "../components/Cards";
import { useStore } from "../lib/store";

/** Component gallery on sample data (no backend calls on render). Route: /showcase */
const now = Date.now();
const at = (min: number) => new Date(now - min * 60_000).toISOString();
const inHours = (h: number) => new Date(now + h * 3_600_000).toISOString();

const SAMPLE: UIMessage[] = [
  { id: 1, conversation_id: 0, role: "user", text: "Напомни завтра в 10 про оплату интернета", cards: [], created_at: at(30) },
  { id: 2, conversation_id: 0, role: "assistant", text: "Готово. Поставил напоминание.", cards: [], created_at: at(29) },
  {
    id: 3,
    conversation_id: 0,
    role: "tool",
    text: "",
    created_at: at(29),
    cards: [
      {
        type: "reminder",
        id: 1,
        text: "Оплата интернета",
        status: "active",
        next_fire_at: inHours(20),
        recurrence: { kind: "none" },
        recurrence_label: "однократно",
        last_fired_at: null,
      },
    ],
  },
  { id: 4, conversation_id: 0, role: "user", text: "Добавь задачу: купить продукты на завтра", cards: [], created_at: at(12) },
  {
    id: 5,
    conversation_id: 0,
    role: "tool",
    text: "",
    created_at: at(12),
    cards: [{ type: "task", id: 1, title: "Купить продукты", notes: null, status: "open", due_at: inHours(24), due_has_time: false, completed_at: null }],
  },
  {
    id: 6,
    conversation_id: 0,
    role: "assistant",
    text: "Добавил. Вот что на этой неделе:\n\n| День | Дело |\n|---|---|\n| Пн | Спортзал, 19:00 |\n| Ср | Созвон с командой |\n\nИ пример кода:\n\n```bash\ndocker compose up -d\n```",
    cards: [],
    created_at: at(11),
  },
  { id: 7, conversation_id: 0, role: "user", text: "Запомни, что я пью кофе без сахара", cards: [], created_at: at(3) },
  { id: 8, conversation_id: 0, role: "tool", text: "", created_at: at(3), cards: [{ type: "fact", id: 1, text: "Пьёт кофе без сахара" }] },
  {
    id: 9,
    conversation_id: 0,
    role: "notification",
    text: "Напоминание",
    created_at: at(1),
    cards: [
      {
        type: "reminder_fired",
        reminder_id: 2,
        text: "Выпить воды",
        overdue: false,
        scheduled_for: at(1),
        fired_at: at(1),
        recurrence_label: "однократно",
        next_fire_at: null,
      },
    ],
  },
];

export function ShowcasePage() {
  const { ui, setUi } = useStore();
  return (
    <div className="page scroll">
      <div className="page-inner" style={{ maxWidth: 900 }}>
        <div className="page-header">
          <h2>Витрина компонентов</h2>
          <button type="button" className="btn btn-sm" onClick={() => setUi({ ...ui, theme: ui.theme === "light" ? "dark" : "light" })}>
            Тема: {ui.theme === "light" ? "светлая" : "тёмная"}
          </button>
        </div>

        <section className="surface settings-group">
          <h3>Орб: ожидание · думает · отвечает</h3>
          <div style={{ display: "flex", gap: 40, alignItems: "center", flexWrap: "wrap" }}>
            <Orb size={88} state="idle" />
            <Orb size={88} state="thinking" />
            <Orb size={88} state="speaking" />
            <Orb small size={26} />
          </div>
        </section>

        <section className="surface settings-group">
          <h3>Кнопки и поля</h3>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
            <button type="button" className="btn btn-solid">
              <Plus size={16} aria-hidden="true" /> Основная
            </button>
            <button type="button" className="btn btn-primary">Акцентная</button>
            <button type="button" className="btn">Обычная</button>
            <button type="button" className="btn btn-ghost">Призрачная</button>
            <button type="button" className="icon-btn" aria-label="Иконка">
              <Bell size={17} aria-hidden="true" />
            </button>
            <Checkbox checked onChange={() => undefined} label="Пример" />
            <span className="chip">Чип</span>
            <span className="badge">3</span>
          </div>
          <input className="input" placeholder="Поле ввода" aria-label="Пример поля" />
        </section>

        <section className="surface" style={{ padding: "var(--sp-5) var(--sp-4)" }}>
          <div className="messages-inner">
            <MessageList
              messages={SAMPLE}
              draft={{ text: "", tools: [{ label: "Смотрю задачи…", state: "run" }], cards: [] }}
              orbState="thinking"
            />
            <MessageList messages={[]} draft={{ text: "", tools: [], cards: [] }} orbState="idle" />
          </div>
        </section>
      </div>
    </div>
  );
}
