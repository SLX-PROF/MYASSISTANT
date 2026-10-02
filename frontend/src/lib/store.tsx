import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { tg, tgColorScheme } from "./telegram";
import { api } from "./api";
import type { ChatMessage, Conversation, Me, UISettings } from "./types";

/* ------------------------------------------------------------ routing */

export type Route =
  | { name: "chat"; id: number | null }
  | { name: "tasks" }
  | { name: "reminders" }
  | { name: "memory" }
  | { name: "settings" }
  | { name: "finance" }
  | { name: "content" }
  | { name: "notes" }
  | { name: "mail" }
  | { name: "showcase" };

export function parseRoute(path: string): Route {
  const m = path.match(/^\/c\/(\d+)/);
  if (m) return { name: "chat", id: Number(m[1]) };
  switch (path.replace(/\/+$/, "")) {
    case "/tasks":
      return { name: "tasks" };
    case "/reminders":
      return { name: "reminders" };
    case "/memory":
      return { name: "memory" };
    case "/settings":
      return { name: "settings" };
    case "/finance":
      return { name: "finance" };
    case "/content":
      return { name: "content" };
    case "/notes":
      return { name: "notes" };
    case "/mail":
      return { name: "mail" };
    case "/showcase":
      return { name: "showcase" };
    default:
      return { name: "chat", id: null };
  }
}

export function routePath(r: Route): string {
  if (r.name === "chat") return r.id ? `/c/${r.id}` : "/";
  return `/${r.name}`;
}

/* -------------------------------------------------------------- theme */

export const DEFAULT_UI: UISettings = { theme: "dark", accent: "#22d3ee" };

export function applyUi(ui: UISettings) {
  const root = document.documentElement;
  // "System" inside Telegram follows the Telegram theme.
  const system = tgColorScheme() ?? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  const theme = ui.theme === "system" ? system : ui.theme;
  root.dataset.theme = theme;
  root.style.setProperty("--accent", ui.accent);
  const bg = theme === "light" ? "#f3f6fb" : "#04060c";
  document.querySelector('meta[name="theme-color"]')?.setAttribute("content", bg);
  tg.setColors(bg);
  try {
    localStorage.setItem("atlas.ui", JSON.stringify(ui));
  } catch {
    /* storage unavailable */
  }
}

function initialUi(): UISettings {
  try {
    return { ...DEFAULT_UI, ...JSON.parse(localStorage.getItem("atlas.ui") || "{}") };
  } catch {
    return DEFAULT_UI;
  }
}

/* ------------------------------------------------------------- toasts */

export interface Toast {
  id: number;
  text: string;
  kind: "info" | "error";
  action?: { label: string; run: () => void };
}

/* -------------------------------------------------------------- store */

type Listener = (m: ChatMessage) => void;

interface Store {
  me: Me | null;
  setMe: (m: Me | null) => void;
  ui: UISettings;
  setUi: (u: UISettings) => void;
  route: Route;
  navigate: (r: Route, replace?: boolean) => void;
  conversations: Conversation[];
  refreshConversations: () => Promise<void>;
  setConversations: React.Dispatch<React.SetStateAction<Conversation[]>>;
  toasts: Toast[];
  toast: (text: string, kind?: Toast["kind"], action?: Toast["action"]) => void;
  dismissToast: (id: number) => void;
  online: boolean;
  sidebarOpen: boolean;
  setSidebarOpen: (v: boolean) => void;
  panelOpen: boolean;
  setPanelOpen: (v: boolean) => void;
  paletteOpen: boolean;
  setPaletteOpen: (v: boolean) => void;
  /** Bumped whenever tasks/reminders/facts may have changed. */
  itemsVersion: number;
  bumpItems: () => void;
  /** Live chat messages pushed by the server (fired reminders). */
  onLiveMessage: (fn: Listener) => () => void;
  emitLiveMessage: (m: ChatMessage) => void;
}

const Ctx = createContext<Store | null>(null);

export function StoreProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [ui, setUiState] = useState<UISettings>(initialUi);
  const [route, setRoute] = useState<Route>(() => parseRoute(location.pathname));
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [online, setOnline] = useState(navigator.onLine);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [panelOpen, setPanelOpen] = useState(() => matchMedia("(min-width: 1280px)").matches);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [itemsVersion, setItemsVersion] = useState(0);
  const listeners = useRef(new Set<Listener>());
  const toastId = useRef(0);

  useEffect(() => {
    const onPop = () => setRoute(parseRoute(location.pathname));
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener("popstate", onPop);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    return () => {
      window.removeEventListener("popstate", onPop);
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
    };
  }, []);

  useEffect(() => {
    applyUi(ui);
    if (ui.theme !== "system") return;
    const mq = matchMedia("(prefers-color-scheme: light)");
    const h = () => applyUi(ui);
    mq.addEventListener("change", h);
    window.addEventListener("atlas:tg-theme", h);
    return () => {
      mq.removeEventListener("change", h);
      window.removeEventListener("atlas:tg-theme", h);
    };
  }, [ui]);

  const setUi = useCallback((u: UISettings) => {
    setUiState(u);
    api.saveUiSettings(u).catch(() => undefined);
  }, []);

  const navigate = useCallback((r: Route, replace = false) => {
    const path = routePath(r);
    if (path !== location.pathname) {
      if (replace) history.replaceState(null, "", path);
      else history.pushState(null, "", path);
    }
    setRoute(r);
    setSidebarOpen(false);
  }, []);

  const refreshConversations = useCallback(async () => {
    try {
      setConversations(await api.conversations());
    } catch {
      /* shown elsewhere */
    }
  }, []);

  const dismissToast = useCallback((id: number) => setToasts((t) => t.filter((x) => x.id !== id)), []);
  const toast = useCallback(
    (text: string, kind: Toast["kind"] = "info", action?: Toast["action"]) => {
      const id = ++toastId.current;
      setToasts((t) => [...t.slice(-2), { id, text, kind, action }]);
      setTimeout(() => dismissToast(id), kind === "error" ? 7000 : 4500);
    },
    [dismissToast],
  );

  const onLiveMessage = useCallback((fn: Listener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);
  const emitLiveMessage = useCallback((m: ChatMessage) => listeners.current.forEach((fn) => fn(m)), []);
  const bumpItems = useCallback(() => setItemsVersion((v) => v + 1), []);

  const value = useMemo<Store>(
    () => ({
      me,
      setMe,
      ui,
      setUi,
      route,
      navigate,
      conversations,
      refreshConversations,
      setConversations,
      toasts,
      toast,
      dismissToast,
      online,
      sidebarOpen,
      setSidebarOpen,
      panelOpen,
      setPanelOpen,
      paletteOpen,
      setPaletteOpen,
      itemsVersion,
      bumpItems,
      onLiveMessage,
      emitLiveMessage,
    }),
    [me, ui, setUi, route, navigate, conversations, refreshConversations, toasts, toast, dismissToast, online, sidebarOpen, panelOpen, paletteOpen, itemsVersion, bumpItems, onLiveMessage, emitLiveMessage],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside provider");
  return s;
}
