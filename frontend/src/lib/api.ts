import type {
  ChatEvent,
  ChatMessage,
  Conversation,
  FactCardData,
  Me,
  NotificationItem,
  ReminderCardData,
  TaskCardData,
  UISettings,
} from "./types";

let csrfToken = "";
export function setCsrf(token: string | undefined) {
  csrfToken = token ?? "";
}

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return "Проверьте введённые данные.";
  }
  return fallback;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: {
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" ? { "X-CSRF-Token": csrfToken } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Нет соединения с сервером.", 0);
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) {
    if (res.status === 401 && path !== "/api/auth/login") {
      window.dispatchEvent(new CustomEvent("atlas:unauthorized"));
    }
    throw new ApiError(detailOf(data, `Ошибка ${res.status}`), res.status);
  }
  return data as T;
}

const get = <T>(p: string) => request<T>("GET", p);
const post = <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {});
const patch = <T>(p: string, b: unknown) => request<T>("PATCH", p, b);
const put = <T>(p: string, b: unknown) => request<T>("PUT", p, b);
const del = <T>(p: string) => request<T>("DELETE", p);

export const api = {
  me: () => get<Me>("/api/auth/me"),
  login: (password: string, code?: string) =>
    post<Me & { csrf_token: string }>("/api/auth/login", { password, ...(code ? { code } : {}) }),
  logout: () => post<{ ok: boolean }>("/api/auth/logout"),

  conversations: () => get<Conversation[]>("/api/conversations"),
  createConversation: () => post<Conversation>("/api/conversations", {}),
  renameConversation: (id: number, title: string) => patch<Conversation>(`/api/conversations/${id}`, { title }),
  deleteConversation: (id: number) => del<{ ok: boolean }>(`/api/conversations/${id}`),
  messages: (id: number, beforeId?: number) =>
    get<{ messages: ChatMessage[]; has_more: boolean; running: boolean }>(
      `/api/conversations/${id}/messages${beforeId ? `?before_id=${beforeId}` : ""}`,
    ),

  reminders: (status = "active") => get<ReminderCardData[]>(`/api/reminders?status=${status}`),
  createReminder: (b: { text: string; when: string; recurrence?: string; weekdays?: string[]; interval_months?: number }) =>
    post<ReminderCardData>("/api/reminders", b),
  cancelReminder: (id: number) => post<ReminderCardData>(`/api/reminders/${id}/cancel`),

  tasks: (status = "open") => get<TaskCardData[]>(`/api/tasks?status=${status}`),
  createTask: (b: { title: string; due?: string | null; notes?: string | null }) => post<TaskCardData>("/api/tasks", b),
  updateTask: (id: number, b: Partial<{ title: string; notes: string; due: string; clear_due: boolean; done: boolean }>) =>
    patch<TaskCardData>(`/api/tasks/${id}`, b),
  deleteTask: (id: number) => del<{ ok: boolean }>(`/api/tasks/${id}`),

  facts: () => get<FactCardData[]>("/api/facts"),
  createFact: (text: string) => post<FactCardData>("/api/facts", { text }),
  deleteFact: (id: number) => del<{ ok: boolean }>(`/api/facts/${id}`),

  notifications: (unreadOnly = false) => get<NotificationItem[]>(`/api/notifications?unread_only=${unreadOnly}`),
  markRead: (b: { ids?: number[]; conversation_id?: number; all?: boolean }) =>
    post<{ updated: number }>("/api/notifications/read", b),

  uiSettings: () => get<UISettings>("/api/settings/ui"),
  saveUiSettings: (s: UISettings) => put<UISettings>("/api/settings/ui", s),
};

/** POST a chat message and consume the Server-Sent Events stream. */
export async function streamChat(
  conversationId: number,
  text: string,
  onEvent: (e: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api/conversations/${conversationId}/messages`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken, Accept: "text/event-stream" },
      body: JSON.stringify({ text }),
      signal,
    });
  } catch {
    throw new ApiError("Нет соединения с сервером.", 0);
  }
  if (!res.ok || !res.body) {
    const data = await res.json().catch(() => null);
    if (res.status === 401) window.dispatchEvent(new CustomEvent("atlas:unauthorized"));
    throw new ApiError(detailOf(data, `Ошибка ${res.status}`), res.status);
  }
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += value;
    let idx: number;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of chunk.split("\n")) {
        if (line.startsWith("data: ")) {
          try {
            onEvent(JSON.parse(line.slice(6)) as ChatEvent);
          } catch {
            /* ignore malformed event */
          }
        }
      }
    }
  }
}
