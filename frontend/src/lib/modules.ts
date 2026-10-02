// API and types for the content calendar and the finance planner.
import { request, upload } from "./api";

/* ------------------------------------------------------------ content */

export type Rubric = "beauty" | "lifestyle" | "office" | "habits" | "other";
export type Stage = "idea" | "script" | "filmed" | "published";
export type ContentRef = { id: number; kind: "link" | "photo"; url: string; caption: string };
export type ContentItem = {
  id: number;
  day: string | null;
  position: number;
  title: string;
  rubric: Rubric;
  icon: string;
  stage: Stage;
  publish_time: string;
  hook: string;
  note: string;
  platforms: Platform[];
  refs: ContentRef[];
};
export type Platform = "tiktok" | "instagram" | "youtube" | "vk" | "telegram" | "pinterest";
export type ContentMeta = { title: string; tags: string[]; goal: string; motto: string; week_themes: Record<string, string> };
export type ItemFields = Partial<Omit<ContentItem, "id" | "refs" | "position">>;

export const contentApi = {
  range: (start: string, end: string) => request<{ meta: ContentMeta; items: ContentItem[] }>("GET", `/api/content?start=${start}&end=${end}`),
  bank: () => request<{ meta: ContentMeta; items: ContentItem[] }>("GET", "/api/content?bank=true"),
  create: (f: ItemFields & { title: string }) => request<ContentItem>("POST", "/api/content/items", f),
  update: (id: number, f: ItemFields & { to_bank?: boolean }) => request<ContentItem>("PATCH", `/api/content/items/${id}`, f),
  duplicate: (id: number, day?: string | null) => request<ContentItem>("POST", `/api/content/items/${id}/duplicate`, { day: day ?? null }),
  remove: (id: number) => request<{ ok: boolean }>("DELETE", `/api/content/items/${id}`),
  addLink: (id: number, url: string, caption: string) => request<ContentRef>("POST", `/api/content/items/${id}/links`, { url, caption }),
  addPhoto: (id: number, file: Blob, caption = "") =>
    upload<ContentRef>(`/api/content/items/${id}/photos?caption=${encodeURIComponent(caption.slice(0, 300))}`, file),
  removeRef: (refId: number) => request<{ ok: boolean }>("DELETE", `/api/content/refs/${refId}`),
  setMeta: (m: Partial<Omit<ContentMeta, "week_themes">> & { week_of?: string; week_theme?: string }) => request<ContentMeta>("PUT", "/api/content/meta", m),
  stats: (start: string, end: string) =>
    request<{ total: number; by_stage: Record<Stage, number>; by_rubric: Record<string, number>; days_planned: number }>(
      "GET",
      `/api/content/stats?start=${start}&end=${end}`,
    ),
};

/* ------------------------------------------------------------ finance */

export type FinCategory = { id: number; name: string; kind: "expense" | "income"; monthly_limit: number; color: string; keywords: string; archived: boolean };
export type FinTx = { id: number; day: string; amount: number; amount_text: string; kind: "expense" | "income"; category_id: number | null; category: string; color: string; note: string };
export type FinRecurring = {
  id: number;
  title: string;
  amount: number;
  amount_text: string;
  category_id: number | null;
  category: string;
  day_of_month: number;
  interval_months: number;
  next_due: string;
  remind_days: number;
  active: boolean;
};
export type FinSummary = {
  month: string;
  income: number;
  expense: number;
  budget: number;
  remaining: number;
  per_day: number;
  days_left: number;
  categories: { id: number | null; name: string; color: string; spent: number; limit: number }[];
  upcoming: FinRecurring[];
  expense_shares: FinShare[];
  income_shares: FinShare[];
  goals: FinGoal[];
};
export type FinShare = { id: number | null; name: string; color: string; amount: number };
export type FinMonth = { month: string; income: number; expense: number; net: number };
export type FinGoal = {
  id: number;
  title: string;
  target: number;
  saved: number;
  left: number;
  pct: number;
  deadline: string | null;
  color: string;
  per_month: number;
  overdue: boolean;
  done: boolean;
};
export type Parsed = { amount: number; amount_text: string; kind: "expense" | "income"; day: string; note: string; category_id: number | null; category: string | null };

export const financeApi = {
  summary: (month: string) => request<FinSummary>("GET", `/api/finance/summary?month=${month}`),
  transactions: (month: string) => request<FinTx[]>("GET", `/api/finance/transactions?month=${month}`),
  parse: (text: string) => request<Parsed>("POST", "/api/finance/parse", { text }),
  add: (t: { amount: number; kind: string; day: string; category_id: number | null; note: string }) =>
    request<FinTx & { alerts: string[] }>("POST", "/api/finance/transactions", t),
  remove: (id: number) => request<{ ok: boolean }>("DELETE", `/api/finance/transactions/${id}`),
  categories: () => request<FinCategory[]>("GET", "/api/finance/categories"),
  addCategory: (c: { name: string; kind: string; monthly_limit: number; color: string }) => request<FinCategory>("POST", "/api/finance/categories", c),
  updateCategory: (id: number, c: Partial<FinCategory>) => request<FinCategory>("PATCH", `/api/finance/categories/${id}`, c),
  recurring: () => request<FinRecurring[]>("GET", "/api/finance/recurring"),
  addRecurring: (r: { title: string; amount: number; category_id: number | null; day_of_month: number; interval_months: number; remind_days: number }) =>
    request<FinRecurring>("POST", "/api/finance/recurring", r),
  updateRecurring: (id: number, r: Partial<FinRecurring>) => request<FinRecurring>("PATCH", `/api/finance/recurring/${id}`, r),
  removeRecurring: (id: number) => request<{ ok: boolean }>("DELETE", `/api/finance/recurring/${id}`),
  paid: (id: number) => request<{ recurring: FinRecurring; transaction: FinTx }>("POST", `/api/finance/recurring/${id}/paid`),
  history: (month: string, months = 6) => request<FinMonth[]>("GET", `/api/finance/history?month=${month}&months=${months}`),
  addGoal: (g: { title: string; target: number; saved: number; deadline: string | null }) => request<FinGoal>("POST", "/api/finance/goals", g),
  updateGoal: (id: number, g: { title?: string; target?: number; deadline?: string | null }) => request<FinGoal>("PATCH", `/api/finance/goals/${id}`, g),
  deposit: (id: number, amount: number) => request<FinGoal & { reached: boolean }>("POST", `/api/finance/goals/${id}/deposit`, { amount }),
  removeGoal: (id: number) => request<{ ok: boolean }>("DELETE", `/api/finance/goals/${id}`),
};

/* -------------------------------------------------------------- notes */

export type Note = { id: number; text: string; url: string; title: string; tags: string[]; source: string; created_at: string };

export const notesApi = {
  list: (q = "") => request<Note[]>("GET", `/api/notes${q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ""}`),
  add: (n: { text: string; url?: string; tags?: string }) => request<Note>("POST", "/api/notes", n),
  remove: (id: number) => request<{ ok: boolean }>("DELETE", `/api/notes/${id}`),
};

/* -------------------------------------------------------------- dates */

export function iso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
export function fromIso(s: string): Date {
  const [y, m, d] = s.split("-").map(Number);
  return new Date(y, m - 1, d);
}
export function addDays(d: Date, n: number): Date {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}
export function mondayOf(d: Date): Date {
  return addDays(d, -((d.getDay() + 6) % 7));
}
export const WD = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"];
export const MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"];
export const MONTHS_GEN = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];

export function rub(kop: number): string {
  const r = Math.round(Math.abs(kop) / 100);
  return `${r.toLocaleString("ru-RU").replace(/ /g, " ")} ₽`;
}

/* --------------------------------------------------------------- mail */

export type MailProgress = {
  state: "idle" | "collecting" | "collected" | "classifying" | "done" | "error";
  message?: string;
  letters?: number;
  total?: number;
  people?: number;
  services?: number;
  pending?: number;
  classified?: number;
  estimate_usd?: number;
  model?: string;
};
export type MailBox = { address: string; oauth: boolean; connected?: boolean; pending?: { user_code: string; verification_uri: string } | null; error?: string };
export type MailStatus =
  | { enabled: false }
  | { enabled: true; accounts: string[]; boxes: MailBox[]; busy: boolean; progress: MailProgress; categories: Record<string, string>; digest_time: string };
export type MailService = {
  id: number;
  domain: string;
  accounts: string;
  name: string;
  category: string;
  category_label: string;
  suggest: string;
  suggest_label: string;
  price: string;
  note: string;
  count: number;
  first_seen: string | null;
  last_seen: string | null;
  subjects: string[];
  unsubscribe: string;
  one_click: boolean;
  status: "new" | "keep" | "unsubscribed" | "done" | "hidden";
};

export const mailApi = {
  status: () => request<MailStatus>("GET", "/api/mail/status"),
  collect: () => request<{ ok: boolean }>("POST", "/api/mail/collect"),
  classify: () => request<{ ok: boolean }>("POST", "/api/mail/classify"),
  services: () => request<MailService[]>("GET", "/api/mail/services"),
  setStatus: (id: number, status: MailService["status"]) => request<MailService>("PATCH", `/api/mail/services/${id}`, { status }),
  unsubscribe: (id: number) => request<MailService>("POST", `/api/mail/services/${id}/unsubscribe`),
  digest: () => request<{ text: string }>("POST", "/api/mail/digest"),
  outlookStart: (address: string) => request<{ user_code: string; verification_uri: string }>("POST", `/api/mail/oauth/${encodeURIComponent(address)}/start`),
};
