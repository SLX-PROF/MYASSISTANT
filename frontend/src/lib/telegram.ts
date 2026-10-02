// Minimal Telegram Mini App bridge. Telegram passes launch data in the URL
// hash and talks to the page through a small event protocol; we implement
// just what we use, so no third-party script is loaded and CSP stays 'self'.
// https://core.telegram.org/api/web-events

type Handler = (data: unknown) => void;

const params: URLSearchParams | null = (() => {
  const raw = location.hash.startsWith("#") ? location.hash.slice(1) : "";
  const p = new URLSearchParams(raw);
  if (p.get("tgWebAppData")) {
    try {
      sessionStorage.setItem("atlas.tg", raw);
    } catch {
      /* storage unavailable */
    }
    // Don't keep the signed launch data in the address bar.
    history.replaceState(null, "", location.pathname + location.search);
    return p;
  }
  try {
    const saved = sessionStorage.getItem("atlas.tg");
    return saved ? new URLSearchParams(saved) : null;
  } catch {
    return null;
  }
})();

export const inTelegram = params !== null;
export const tgInitData = params?.get("tgWebAppData") ?? "";

let themeParams: Record<string, string> = (() => {
  try {
    return JSON.parse(params?.get("tgWebAppThemeParams") || "{}");
  } catch {
    return {};
  }
})();

/** Light/dark as in the user's Telegram theme, or null outside Telegram. */
export function tgColorScheme(): "light" | "dark" | null {
  const bg = themeParams.bg_color;
  if (!inTelegram || !bg || !/^#[0-9a-f]{6}$/i.test(bg)) return null;
  const n = parseInt(bg.slice(1), 16);
  const lum = 0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
  return lum > 128 ? "light" : "dark";
}

const handlers = new Map<string, Set<Handler>>();

function receive(eventType: string, eventData: unknown) {
  if (eventType === "theme_changed") {
    const t = (eventData as { theme_params?: Record<string, string> } | null)?.theme_params;
    if (t) themeParams = t;
    window.dispatchEvent(new CustomEvent("atlas:tg-theme"));
  }
  handlers.get(eventType)?.forEach((h) => h(eventData));
}

type TgWindow = Window & {
  TelegramWebviewProxy?: { postEvent(type: string, data: string): void };
  Telegram?: { WebView?: { receiveEvent?: typeof receive } };
  TelegramGameProxy_receiveEvent?: typeof receive;
};

if (inTelegram) {
  const w = window as TgWindow;
  w.Telegram = { ...(w.Telegram ?? {}), WebView: { ...(w.Telegram?.WebView ?? {}), receiveEvent: receive } };
  w.TelegramGameProxy_receiveEvent = receive;
  window.addEventListener("message", (e) => {
    if (e.origin !== "https://web.telegram.org") return;
    try {
      const m = JSON.parse(e.data);
      receive(m.eventType, m.eventData);
    } catch {
      /* not ours */
    }
  });
}

function post(eventType: string, eventData: object = {}) {
  if (!inTelegram) return;
  const w = window as TgWindow;
  try {
    if (w.TelegramWebviewProxy) w.TelegramWebviewProxy.postEvent(eventType, JSON.stringify(eventData));
    else if (window.parent !== window) window.parent.postMessage(JSON.stringify({ eventType, eventData }), "https://web.telegram.org");
  } catch {
    /* older clients ignore unknown events */
  }
}

export function onTelegram(eventType: string, h: Handler): () => void {
  if (!handlers.has(eventType)) handlers.set(eventType, new Set());
  handlers.get(eventType)!.add(h);
  return () => handlers.get(eventType)?.delete(h);
}

export const tg = {
  ready: () => post("web_app_ready"),
  expand: () => post("web_app_expand"),
  noVerticalSwipes: () => post("web_app_setup_swipe_behavior", { allow_vertical_swipe: false }),
  setColors: (color: string) => {
    post("web_app_set_header_color", { color });
    post("web_app_set_background_color", { color });
    post("web_app_set_bottom_bar_color", { color });
  },
  backButton: (visible: boolean) => post("web_app_setup_back_button", { is_visible: visible }),
};
