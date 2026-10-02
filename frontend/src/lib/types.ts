export type Role = "user" | "assistant" | "tool" | "notification";

export interface Recurrence {
  kind: "none" | "daily" | "weekly" | "monthly" | "yearly";
  time?: string;
  weekdays?: number[];
  anchor_day?: number;
}

export interface ReminderCardData {
  type: "reminder";
  id: number;
  text: string;
  status: "active" | "done" | "cancelled";
  next_fire_at: string | null;
  recurrence: Recurrence;
  recurrence_label: string;
  last_fired_at: string | null;
}

export interface TaskCardData {
  type: "task";
  id: number;
  title: string;
  notes: string | null;
  status: "open" | "done";
  due_at: string | null;
  due_has_time: boolean;
  completed_at: string | null;
}

export interface FactCardData {
  type: "fact";
  id: number;
  text: string;
  created_at?: string;
}

export type CardData =
  | ReminderCardData
  | TaskCardData
  | FactCardData
  | { type: "reminder_list"; items: ReminderCardData[] }
  | { type: "task_list"; items: TaskCardData[] }
  | { type: "fact_list"; items: FactCardData[] }
  | { type: "fact_forgotten"; id: number; text: string }
  | {
      type: "reminder_fired";
      reminder_id: number;
      text: string;
      overdue: boolean;
      scheduled_for: string | null;
      fired_at: string;
      recurrence_label: string;
      next_fire_at: string | null;
    };

export interface ChatMessage {
  id: number;
  conversation_id: number;
  role: Role;
  text: string;
  cards: CardData[];
  created_at: string;
}

export interface Conversation {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
  preview: string | null;
  unread: number;
}

export interface Me {
  authenticated: boolean;
  csrf_token?: string;
  assistant_name: string;
  timezone?: string;
  llm_provider?: string;
  llm_warning?: string | null;
  totp_required?: boolean;
  /** "content" = content calendar only (e.g. a partner's access). */
  scope?: "all" | "content";
}

export interface UISettings {
  theme: "dark" | "light" | "system";
  accent: string;
}

export interface NotificationItem {
  id: number;
  kind: string;
  title: string;
  body: string;
  overdue: boolean;
  conversation_id: number | null;
  message_id?: number | null;
  read?: boolean;
  created_at?: string;
}

/** Events streamed by POST /api/conversations/{id}/messages */
export type ChatEvent =
  | { type: "user_message"; message: ChatMessage }
  | { type: "delta"; text: string }
  | { type: "assistant_message"; message: ChatMessage }
  | { type: "tool_start"; name: string; label: string }
  | { type: "tool_result"; name: string; is_error: boolean; card: CardData | null; error: string | null }
  | { type: "tool_message"; message: ChatMessage }
  | { type: "error"; message: string; retryable?: boolean }
  | { type: "done" };

/** Events on GET /api/events */
export type LiveEvent =
  | { type: "notification"; notification: NotificationItem; message: ChatMessage | null }
  | { type: "conversation_updated"; conversation_id: number }
  | { type: "notifications_read" };
