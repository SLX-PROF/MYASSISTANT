import { useCallback, useEffect, useState } from "react";
import { Menu, PanelRight, Search, WifiOff, X } from "lucide-react";
import { api, setCsrf } from "./lib/api";
import { setTimezone } from "./lib/format";
import { useStore } from "./lib/store";
import type { LiveEvent } from "./lib/types";
import { Circuit, Orb, type OrbState } from "./components/Orb";
import { BottomNav, CommandPalette, Sidebar, Toasts } from "./components/Navigation";
import { ChatPage } from "./pages/ChatPage";
import { MemoryPage, ReminderList, RemindersPage, TaskList, TasksPage } from "./pages/ItemsPages";
import { LoginPage } from "./pages/LoginPage";
import { SettingsPage } from "./pages/SettingsPage";
import { ShowcasePage } from "./pages/ShowcasePage";

async function showSystemNotification(title: string, body: string, url: string) {
  if (!("Notification" in window) || Notification.permission !== "granted") return false;
  try {
    const reg = await navigator.serviceWorker?.getRegistration();
    if (reg) await reg.showNotification(title, { body, tag: url, data: { url }, icon: "/icons/icon-192.png", badge: "/icons/badge-96.png" });
    else new Notification(title, { body, tag: url });
    return true;
  } catch {
    return false;
  }
}

function SidePanel() {
  const { setPanelOpen } = useStore();
  const [tab, setTab] = useState<"reminders" | "tasks">("reminders");
  return (
    <aside className="panel" aria-label="Напоминания и задачи">
      <div className="panel-head">
        <div className="segmented" role="tablist" aria-label="Раздел панели" style={{ flex: 1 }}>
          <button type="button" role="tab" aria-selected={tab === "reminders"} onClick={() => setTab("reminders")} style={{ flex: 1 }}>
            Напоминания
          </button>
          <button type="button" role="tab" aria-selected={tab === "tasks"} onClick={() => setTab("tasks")} style={{ flex: 1 }}>
            Задачи
          </button>
        </div>
        <button type="button" className="icon-btn plain" onClick={() => setPanelOpen(false)} aria-label="Закрыть панель">
          <X size={18} aria-hidden="true" />
        </button>
      </div>
      <div className="panel-body scroll" role="tabpanel">
        {tab === "reminders" ? <ReminderList compact /> : <TaskList compact />}
      </div>
    </aside>
  );
}

const TITLES: Record<string, string> = {
  tasks: "Задачи",
  reminders: "Напоминания",
  memory: "Память",
  settings: "Настройки",
  showcase: "Витрина",
};

export function App() {
  const store = useStore();
  const { me, setMe, route, navigate, conversations, refreshConversations, setUi, online, sidebarOpen, setSidebarOpen, panelOpen, setPanelOpen, setPaletteOpen, toast, emitLiveMessage, bumpItems } = store;
  const [orbState, setOrbState] = useState<OrbState>("idle");
  const onOrbState = useCallback((s: OrbState) => setOrbState(s), []);

  // Session bootstrap.
  useEffect(() => {
    api
      .me()
      .then((m) => {
        setCsrf(m.csrf_token);
        setTimezone(m.timezone);
        setMe(m);
      })
      .catch(() => setMe({ authenticated: false, assistant_name: "Атлас" }));
    const onUnauthorized = () => setMe({ authenticated: false, assistant_name: "Атлас" });
    window.addEventListener("atlas:unauthorized", onUnauthorized);
    return () => window.removeEventListener("atlas:unauthorized", onUnauthorized);
  }, [setMe]);

  // After login: settings, conversations, live events.
  useEffect(() => {
    if (!me?.authenticated) return;
    api.uiSettings().then(setUi).catch(() => undefined);
    refreshConversations();

    const es = new EventSource("/api/events");
    es.onmessage = async (e) => {
      let ev: LiveEvent;
      try {
        ev = JSON.parse(e.data);
      } catch {
        return;
      }
      if (ev.type === "notification") {
        const n = ev.notification;
        if (ev.message) emitLiveMessage(ev.message);
        refreshConversations();
        bumpItems();
        const url = n.conversation_id ? `/c/${n.conversation_id}` : "/reminders";
        const shown = (document.hidden || !document.hasFocus()) && (await showSystemNotification(n.title, n.body, url));
        if (!shown) {
          toast(`${n.overdue ? "Просрочено: " : ""}${n.body}`, "info", n.conversation_id ? { label: "Открыть", run: () => navigate({ name: "chat", id: n.conversation_id }) } : undefined);
        }
      } else if (ev.type === "conversation_updated") {
        refreshConversations();
        window.dispatchEvent(new CustomEvent("atlas:conversation-updated", { detail: ev.conversation_id }));
      } else if (ev.type === "notifications_read") {
        refreshConversations();
      }
    };
    return () => es.close();
  }, [me?.authenticated, setUi, refreshConversations, emitLiveMessage, bumpItems, toast, navigate]);

  // Open the latest conversation on "/" once they are loaded.
  useEffect(() => {
    if (route.name === "chat" && route.id === null && location.pathname === "/" && conversations.length && !sessionStorage.getItem("atlas.fresh")) {
      sessionStorage.setItem("atlas.fresh", "1");
      navigate({ name: "chat", id: conversations[0].id }, true);
    }
  }, [route, conversations, navigate]);

  // Unread badge on the installed app icon.
  useEffect(() => {
    const unread = conversations.reduce((n, c) => n + c.unread, 0);
    const nav = navigator as Navigator & { setAppBadge?: (n: number) => Promise<void>; clearAppBadge?: () => Promise<void> };
    (unread ? nav.setAppBadge?.(unread) : nav.clearAppBadge?.())?.catch(() => undefined);
    document.title = unread ? `(${unread}) Атлас` : "Атлас";
  }, [conversations]);

  if (route.name === "showcase") {
    return (
      <>
        <Circuit />
        <div className="app-shell" style={{ gridTemplateColumns: "minmax(0,1fr)" }}>
          <main className="main">
            <ShowcasePage />
          </main>
        </div>
        <Toasts />
      </>
    );
  }
  if (me === null) {
    return (
      <div className="boot">
        <Orb state="thinking" />
      </div>
    );
  }
  if (!me.authenticated) return <LoginPage />;

  const conv = route.name === "chat" && route.id ? conversations.find((c) => c.id === route.id) : null;
  const title = route.name === "chat" ? (conv?.title ?? "Новый чат") : TITLES[route.name];

  return (
    <>
      <Circuit />
      <div className="app-shell">
        <Sidebar />
        <main className="main">
          {!online && (
            <div className="banner" role="status">
              <WifiOff size={14} aria-hidden="true" /> Нет соединения. Изменения отправятся, когда связь вернётся.
            </div>
          )}
          {me.llm_warning && online && (
            <div className="banner" role="status">
              Демо-режим: {me.llm_warning}
            </div>
          )}
          <header className="topbar">
            <button type="button" className="icon-btn mobile-only" onClick={() => setSidebarOpen(true)} aria-label="Открыть меню">
              <Menu size={18} aria-hidden="true" />
            </button>
            <Orb state={route.name === "chat" ? orbState : "idle"} />
            <div className="topbar-title">
              <h1>{title}</h1>
              <div className={`status ${orbState !== "idle" ? "busy" : ""} ${!online ? "off" : ""}`}>
                {!online ? "нет сети" : orbState === "thinking" ? "думает…" : orbState === "speaking" ? "отвечает…" : "на связи"}
              </div>
            </div>
            <button type="button" className="icon-btn desktop-only" onClick={() => setPaletteOpen(true)} aria-label="Поиск (Ctrl+K)">
              <Search size={17} aria-hidden="true" />
            </button>
            <button type="button" className="icon-btn" aria-pressed={panelOpen} onClick={() => setPanelOpen(!panelOpen)} aria-label="Панель напоминаний и задач">
              <PanelRight size={17} aria-hidden="true" />
            </button>
          </header>
          {route.name === "chat" && <ChatPage conversationId={route.id} onOrbState={onOrbState} />}
          {route.name === "tasks" && <TasksPage />}
          {route.name === "reminders" && <RemindersPage />}
          {route.name === "memory" && <MemoryPage />}
          {route.name === "settings" && <SettingsPage />}
          <BottomNav />
        </main>
        {panelOpen && <SidePanel />}
      </div>
      {(sidebarOpen || (panelOpen && !matchMedia("(min-width: 1280px)").matches)) && (
        <div
          className="drawer-backdrop"
          onClick={() => {
            setSidebarOpen(false);
            if (!matchMedia("(min-width: 1280px)").matches) setPanelOpen(false);
          }}
          aria-hidden="true"
        />
      )}
      <CommandPalette />
      <Toasts />
    </>
  );
}
