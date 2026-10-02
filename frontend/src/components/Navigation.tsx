import { useEffect, useMemo, useRef, useState } from "react";
import { AlarmClock, Brain, CalendarHeart, Ellipsis, Lightbulb, ListChecks, MessageSquare, Plus, Search, Settings, Wallet, X } from "lucide-react";
import { useStore, type Route } from "../lib/store";
import { Orb } from "./Orb";

function stripMarkdown(s: string) {
  return s.replace(/[*_`#>~|]+/g, "").replace(/\s+/g, " ").trim();
}

export function Sidebar() {
  const { conversations, route, navigate, sidebarOpen, setPaletteOpen } = useStore();
  const current = route.name === "chat" ? route.id : null;
  const pages: { r: Route; label: string; icon: React.ReactNode }[] = [
    { r: { name: "tasks" }, label: "Задачи", icon: <ListChecks size={17} aria-hidden="true" /> },
    { r: { name: "reminders" }, label: "Напоминания", icon: <AlarmClock size={17} aria-hidden="true" /> },
    { r: { name: "finance" }, label: "Финансы", icon: <Wallet size={17} aria-hidden="true" /> },
    { r: { name: "content" }, label: "Контент-план", icon: <CalendarHeart size={17} aria-hidden="true" /> },
    { r: { name: "notes" }, label: "Заметки", icon: <Lightbulb size={17} aria-hidden="true" /> },
    { r: { name: "memory" }, label: "Память", icon: <Brain size={17} aria-hidden="true" /> },
    { r: { name: "settings" }, label: "Настройки", icon: <Settings size={17} aria-hidden="true" /> },
  ];
  return (
    <nav className={`sidebar ${sidebarOpen ? "open" : ""}`} aria-label="Навигация">
      <div className="brand">
        <Orb small size={28} />
        АТЛАС
      </div>
      <button type="button" className="btn btn-primary" onClick={() => navigate({ name: "chat", id: null })}>
        <Plus size={16} aria-hidden="true" /> Новый чат
      </button>
      <button type="button" className="nav-link desktop-only" onClick={() => setPaletteOpen(true)}>
        <Search size={16} aria-hidden="true" /> Поиск и переходы <span className="kbd">Ctrl K</span>
      </button>
      <div className="section-label">Диалоги</div>
      <div className="conv-list scroll">
        {conversations.length === 0 && <p className="hint" style={{ padding: "0 8px" }}>Здесь появятся ваши диалоги.</p>}
        {conversations.map((c) => (
          <button
            key={c.id}
            type="button"
            className="conv-item"
            aria-current={c.id === current ? "true" : undefined}
            onClick={() => navigate({ name: "chat", id: c.id })}
          >
            <span className="conv-title">{c.title}</span>
            {c.preview && <span className="conv-preview">{stripMarkdown(c.preview)}</span>}
            {c.unread > 0 && <span className="badge" aria-label={`${c.unread} непрочитанных`}>{c.unread}</span>}
          </button>
        ))}
      </div>
      <div className="nav-links">
        {pages.map((p) => (
          <button key={p.r.name} type="button" className="nav-link" aria-current={route.name === p.r.name ? "page" : undefined} onClick={() => navigate(p.r)}>
            {p.icon}
            {p.label}
          </button>
        ))}
      </div>
    </nav>
  );
}

export function BottomNav() {
  const { route, navigate, conversations, setSidebarOpen } = useStore();
  const unread = conversations.reduce((n, c) => n + c.unread, 0);
  const lastId = conversations[0]?.id ?? null;
  const items: { r: Route | null; label: string; icon: React.ReactNode; active: boolean; badge?: number }[] = [
    { r: { name: "chat", id: route.name === "chat" ? route.id : lastId }, label: "Чат", icon: <MessageSquare size={20} aria-hidden="true" />, active: route.name === "chat", badge: unread },
    { r: { name: "tasks" }, label: "Задачи", icon: <ListChecks size={20} aria-hidden="true" />, active: route.name === "tasks" },
    { r: { name: "content" }, label: "Контент", icon: <CalendarHeart size={20} aria-hidden="true" />, active: false },
    { r: { name: "finance" }, label: "Финансы", icon: <Wallet size={20} aria-hidden="true" />, active: route.name === "finance" },
    // "More" opens the side menu: reminders, memory, settings and conversations.
    { r: null, label: "Ещё", icon: <Ellipsis size={20} aria-hidden="true" />, active: ["reminders", "memory", "settings"].includes(route.name) },
  ];
  return (
    <nav className="bottom-nav" aria-label="Основные разделы">
      {items.map((it) => (
        <button key={it.label} type="button" aria-current={it.active ? "page" : undefined} onClick={() => (it.r ? navigate(it.r) : setSidebarOpen(true))}>
          {it.icon}
          {it.label}
          {!!it.badge && <span className="badge">{it.badge}</span>}
        </button>
      ))}
    </nav>
  );
}

/** Ctrl/Cmd+K: jump to a conversation or page. */
export function CommandPalette() {
  const { paletteOpen, setPaletteOpen, conversations, navigate } = useStore();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const restoreFocus = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(!paletteOpen);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [paletteOpen, setPaletteOpen]);

  useEffect(() => {
    if (paletteOpen) {
      restoreFocus.current = document.activeElement as HTMLElement | null;
      setQ("");
      setSel(0);
      requestAnimationFrame(() => input.current?.focus());
    } else {
      restoreFocus.current?.focus?.();
    }
  }, [paletteOpen]);

  const results = useMemo(() => {
    const s = q.trim().toLowerCase();
    const pages: { key: string; label: string; icon: React.ReactNode; go: Route; group: string }[] = [
      { key: "new", label: "Новый чат", icon: <Plus size={16} />, go: { name: "chat", id: null }, group: "Действия" },
      { key: "tasks", label: "Задачи", icon: <ListChecks size={16} />, go: { name: "tasks" }, group: "Разделы" },
      { key: "reminders", label: "Напоминания", icon: <AlarmClock size={16} />, go: { name: "reminders" }, group: "Разделы" },
      { key: "finance", label: "Финансы", icon: <Wallet size={16} />, go: { name: "finance" }, group: "Разделы" },
      { key: "content", label: "Контент-план", icon: <CalendarHeart size={16} />, go: { name: "content" }, group: "Разделы" },
      { key: "notes", label: "Заметки", icon: <Lightbulb size={16} />, go: { name: "notes" }, group: "Разделы" },
      { key: "memory", label: "Память", icon: <Brain size={16} />, go: { name: "memory" }, group: "Разделы" },
      { key: "settings", label: "Настройки", icon: <Settings size={16} />, go: { name: "settings" }, group: "Разделы" },
    ];
    const convs = conversations.map((c) => ({
      key: `c${c.id}`,
      label: c.title,
      icon: <MessageSquare size={16} />,
      go: { name: "chat", id: c.id } as Route,
      group: "Диалоги",
    }));
    return [...pages, ...convs].filter((x) => !s || x.label.toLowerCase().includes(s)).slice(0, 30);
  }, [q, conversations]);

  if (!paletteOpen) return null;
  const choose = (i: number) => {
    const r = results[i];
    if (r) {
      navigate(r.go);
      setPaletteOpen(false);
    }
  };
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && setPaletteOpen(false)}>
      <div className="palette" role="dialog" aria-modal="true" aria-label="Поиск и переходы">
        <input
          ref={input}
          value={q}
          placeholder="Найти диалог или раздел…"
          aria-label="Поиск"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant={results[sel] ? `pal-${results[sel].key}` : undefined}
          onChange={(e) => {
            setQ(e.target.value);
            setSel(0);
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setSel((s) => Math.min(s + 1, results.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setSel((s) => Math.max(s - 1, 0));
            } else if (e.key === "Enter") {
              e.preventDefault();
              choose(sel);
            } else if (e.key === "Escape") {
              setPaletteOpen(false);
            }
          }}
        />
        <ul id="palette-list" role="listbox" className="scroll">
          {results.length === 0 && <li className="group-label">Ничего не найдено</li>}
          {results.map((r, i) => (
            <li key={r.key} role="presentation">
              {(i === 0 || results[i - 1].group !== r.group) && <div className="group-label">{r.group}</div>}
              <button type="button" id={`pal-${r.key}`} role="option" aria-selected={i === sel} onMouseEnter={() => setSel(i)} onClick={() => choose(i)}>
                {r.icon}
                {r.label}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function Toasts() {
  const { toasts, dismissToast } = useStore();
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind === "error" ? "error" : ""}`}>
          <span className="grow">{t.text}</span>
          {t.action && (
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                t.action!.run();
                dismissToast(t.id);
              }}
            >
              {t.action.label}
            </button>
          )}
          <button type="button" className="icon-btn plain" style={{ width: 32, height: 32 }} onClick={() => dismissToast(t.id)} aria-label="Закрыть">
            <X size={15} aria-hidden="true" />
          </button>
        </div>
      ))}
    </div>
  );
}
