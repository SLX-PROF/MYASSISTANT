import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ArrowLeft, BookOpen, Briefcase, Brush, Camera, Check, ChevronLeft, ChevronRight, Coffee, Copy, Dumbbell, Flower2, Gift, Globe,
  Heart, House, ImagePlus, Link2, Moon, Music, Paperclip, Pencil, Plane, Plus, Shirt, ShoppingBag, Smile, Sparkles, Star, Sun,
  Trash2, Utensils, X, type LucideIcon,
} from "lucide-react";
import "@fontsource/cormorant-garamond/500.css";
import "@fontsource/cormorant-garamond/600.css";
import "@fontsource/caveat/400.css";
import "@fontsource/manrope/400.css";
import "@fontsource/manrope/500.css";
import "@fontsource/manrope/600.css";
import "@fontsource/manrope/700.css";
import "../styles/content.css";
import { useStore } from "../lib/store";
import { tg } from "../lib/telegram";
import {
  addDays, contentApi, fromIso, iso, mondayOf, MONTHS, MONTHS_GEN, WD,
  type ContentItem, type ContentMeta, type ContentRef, type ItemFields, type Platform, type Rubric, type Stage,
} from "../lib/modules";

const RUBRICS: [Rubric, string, string][] = [
  ["beauty", "бьюти", "#e8b4bc"],
  ["lifestyle", "лайфстайл", "#a9b8a0"],
  ["office", "офис", "#d9c3a5"],
  ["habits", "привычки", "#b9b0d6"],
  ["other", "другое", "#d6cfcd"],
];
const RUBRIC = Object.fromEntries(RUBRICS.map(([k, label, color]) => [k, { label, color }])) as Record<Rubric, { label: string; color: string }>;
const STAGES: [Stage, string][] = [
  ["idea", "Идея"],
  ["script", "Сценарий"],
  ["filmed", "Снято"],
  ["published", "Опубликовано"],
];
const PLATFORMS: [Platform, string, string][] = [
  ["tiktok", "TikTok", "TT"],
  ["instagram", "Instagram", "IG"],
  ["youtube", "YouTube", "YT"],
  ["vk", "VK", "VK"],
  ["telegram", "Telegram", "TG"],
  ["pinterest", "Pinterest", "P"],
];
const PLATFORM = Object.fromEntries(PLATFORMS.map(([k, l, s]) => [k, { label: l, short: s }])) as Record<Platform, { label: string; short: string }>;

/** A reference being prepared before upload: a photo or a link, each with its own comment. */
type RefDraft =
  | { key: number; kind: "photo"; file: File; preview: string; caption: string }
  | { key: number; kind: "link"; url: string; caption: string };
let draftKey = 0;

const ICONS: Record<string, LucideIcon> = {
  sparkles: Sparkles, heart: Heart, camera: Camera, coffee: Coffee, moon: Moon, bag: ShoppingBag, briefcase: Briefcase, sun: Sun,
  flower: Flower2, utensils: Utensils, shirt: Shirt, book: BookOpen, dumbbell: Dumbbell, plane: Plane, home: House, gift: Gift,
  star: Star, music: Music, brush: Brush, smile: Smile,
};
const iconOf = (name: string) => ICONS[name] ?? Sparkles;
const isDone = (it: ContentItem) => it.stage === "filmed" || it.stage === "published";
const label = (d: Date) => `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}`;

type View = "week" | "month" | "bank" | "stats";
type Sheet =
  | { mode: "new"; day: string | null }
  | { mode: "edit"; item: ContentItem }
  | { mode: "move"; item: ContentItem }
  | { mode: "refs"; item: ContentItem }
  | { mode: "day"; day: string }
  | { mode: "meta" }
  | { mode: "theme"; week: string }
  | null;

/** Downscale a photo before upload (keeps the server and Telegram fast). */
async function preparePhoto(file: File): Promise<Blob> {
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, 1600 / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d")!.drawImage(bmp, 0, 0, canvas.width, canvas.height);
    return await new Promise<Blob>((res, rej) => canvas.toBlob((b) => (b ? res(b) : rej(new Error())), "image/jpeg", 0.86));
  } catch {
    return file; // the server checks the type
  }
}

/** Long-press (touch) or press-and-move (mouse) an idea, drop it on a [data-drop-day] element. */
function useDragMove(onDrop: (id: number, day: string) => void) {
  const [ghost, setGhost] = useState<{ x: number; y: number; title: string; over: string | null } | null>(null);
  const st = useRef<{ id: number; title: string; sx: number; sy: number; pid: number; timer: number; active: boolean; y: number } | null>(null);
  const suppressClick = useRef(false);
  const drop = useRef(onDrop);
  drop.current = onDrop;

  useEffect(() => {
    const target = (x: number, y: number) =>
      (document.elementFromPoint(x, y) as HTMLElement | null)?.closest<HTMLElement>("[data-drop-day]")?.dataset.dropDay ?? null;
    const move = (e: PointerEvent) => {
      const s = st.current;
      if (!s || e.pointerId !== s.pid) return;
      if (!s.active) {
        if (Math.hypot(e.clientX - s.sx, e.clientY - s.sy) > 8) {
          clearTimeout(s.timer);
          st.current = null;
        }
        return;
      }
      s.y = e.clientY;
      setGhost({ x: e.clientX, y: e.clientY, title: s.title, over: target(e.clientX, e.clientY) });
    };
    // Scroll the page while an idea is held near the top or bottom edge.
    const tick = window.setInterval(() => {
      const s = st.current;
      if (!s?.active) return;
      const scroller = document.querySelector(".cp");
      const edge = 90;
      if (s.y > window.innerHeight - edge) scroller?.scrollBy(0, 14);
      else if (s.y < edge) scroller?.scrollBy(0, -14);
    }, 16);
    const up = (e: PointerEvent) => {
      const s = st.current;
      if (!s || e.pointerId !== s.pid) return;
      clearTimeout(s.timer);
      st.current = null;
      if (!s.active) return;
      setGhost(null);
      suppressClick.current = true;
      setTimeout(() => (suppressClick.current = false), 50);
      const day = e.type === "pointerup" ? target(e.clientX, e.clientY) : null;
      if (day) drop.current(s.id, day);
    };
    const touchmove = (e: TouchEvent) => {
      if (st.current?.active) e.preventDefault();
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
    document.addEventListener("touchmove", touchmove, { passive: false });
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      document.removeEventListener("touchmove", touchmove);
      clearInterval(tick);
    };
  }, []);

  const bind = (id: number, title: string) => ({
    onPointerDown: (e: React.PointerEvent) => {
      if (e.button !== 0) return;
      const s = { id, title, sx: e.clientX, sy: e.clientY, pid: e.pointerId, timer: 0, active: false, y: e.clientY };
      s.timer = window.setTimeout(
        () => {
          s.active = true;
          navigator.vibrate?.(15);
          setGhost({ x: s.sx, y: s.sy, title, over: null });
        },
        e.pointerType === "mouse" ? 180 : 380,
      );
      st.current = s;
    },
    onClickCapture: (e: React.MouseEvent) => {
      if (suppressClick.current) {
        e.stopPropagation();
        e.preventDefault();
      }
    },
    onContextMenu: (e: React.MouseEvent) => e.preventDefault(),
  });
  return { ghost, bind };
}

export default function ContentPage({ onExit, exitLabel }: { onExit: () => void; exitLabel?: string }) {
  const { toast } = useStore();
  const [view, setView] = useState<View>("week");
  const [anchor, setAnchor] = useState(() => new Date());
  const [items, setItems] = useState<ContentItem[] | null>(null);
  const [meta, setMeta] = useState<ContentMeta | null>(null);
  const [detail, setDetail] = useState<ContentItem | null>(null);
  const [sheet, setSheet] = useState<Sheet>(null);
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  const today = iso(new Date());

  // Pastel browser chrome and no dark strip under the tab bar while the calendar is open.
  useEffect(() => {
    const root = document.documentElement;
    const meta = document.querySelector('meta[name="theme-color"]');
    const prev = { bg: root.style.background, body: document.body.style.background, meta: meta?.getAttribute("content") };
    root.style.background = "#f7f0ee";
    document.body.style.background = "#f7f0ee";
    meta?.setAttribute("content", "#f7f0ee");
    tg.setColors("#f7f0ee");
    return () => {
      root.style.background = prev.bg;
      document.body.style.background = prev.body;
      if (prev.meta) meta?.setAttribute("content", prev.meta);
    };
  }, []);

  const range = useMemo(() => {
    if (view === "week") {
      const s = mondayOf(anchor);
      return [s, addDays(s, 6)] as const;
    }
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const last = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
    return view === "month" ? ([mondayOf(first), addDays(mondayOf(last), 6)] as const) : ([first, last] as const);
  }, [view, anchor]);

  useEffect(() => {
    let alive = true;
    const p = view === "bank" ? contentApi.bank() : contentApi.range(iso(range[0]), iso(range[1]));
    p.then((r) => {
      if (!alive) return;
      setItems(r.items);
      setMeta(r.meta);
    }).catch((e) => toast((e as Error).message, "error"));
    return () => {
      alive = false;
    };
  }, [view, range, version, toast]);

  const act = async <T,>(fn: () => Promise<T>, ok?: string): Promise<T | undefined> => {
    try {
      const r = await fn();
      if (ok) toast(ok, "info");
      reload();
      return r;
    } catch (e) {
      toast((e as Error).message, "error");
      return undefined;
    }
  };

  const update = async (it: ContentItem, f: ItemFields & { to_bank?: boolean }, ok?: string) => {
    const r = await act(() => contentApi.update(it.id, f), ok);
    if (r && detail?.id === r.id) setDetail(r);
    return r;
  };

  const uploadDrafts = async (itemId: number, drafts: RefDraft[]): Promise<ContentRef[]> => {
    const added: ContentRef[] = [];
    for (const d of drafts) {
      try {
        if (d.kind === "photo") added.push(await contentApi.addPhoto(itemId, await preparePhoto(d.file), d.caption.trim()));
        else if (d.url.trim()) added.push(await contentApi.addLink(itemId, d.url.trim(), d.caption.trim()));
      } catch (e) {
        toast((e as Error).message, "error");
      }
    }
    if (added.length) reload();
    return added;
  };

  const { ghost, bind } = useDragMove((id, day) => {
    const it = items?.find((x) => x.id === id);
    if (it && it.day !== day) update(it, { day }, `Перенесено на ${label(fromIso(day))}`);
  });

  const byDay = useMemo(() => {
    const m = new Map<string, ContentItem[]>();
    for (const it of items ?? []) if (it.day) m.set(it.day, [...(m.get(it.day) ?? []), it]);
    return m;
  }, [items]);

  const shift = (dir: number) =>
    setAnchor((a) => (view === "week" ? addDays(a, dir * 7) : new Date(a.getFullYear(), a.getMonth() + dir, 1)));

  return (
    <div className="cp">
      <div className="cp-col">
        <header className="cp-head">
          <div className="cp-head-row">
            {exitLabel ? (
              <button type="button" className="cp-pill" onClick={onExit}>
                {exitLabel}
              </button>
            ) : (
              <button type="button" className="cp-round" onClick={onExit} aria-label="Вернуться в Атлас">
                <ArrowLeft size={18} aria-hidden="true" />
              </button>
            )}
            <button type="button" className="cp-round" onClick={() => setSheet({ mode: "meta" })} aria-label="Изменить шапку плана">
              <Pencil size={16} aria-hidden="true" />
            </button>
          </div>
          {(meta?.goal || meta?.motto) && (
            <div className="cp-hand-row">
              <span className="cp-hand cp-hand-left">{meta?.goal}</span>
              <span className="cp-hand cp-hand-right">{meta?.motto}</span>
            </div>
          )}
          <h1 className="cp-title">{(meta?.title || "Контент-план").toUpperCase()}</h1>
          {!!meta?.tags.length && <p className="cp-tags">{meta.tags.map((t) => t.toUpperCase()).join(" · ")}</p>}
        </header>

        {(view === "week" || view === "month" || view === "stats") && (
          <div className="cp-nav">
            <button type="button" className="cp-round" onClick={() => shift(-1)} aria-label="Назад">
              <ChevronLeft size={18} aria-hidden="true" />
            </button>
            <span className="cp-nav-label">
              {view === "week" ? `${label(range[0])} — ${label(range[1])}` : `${MONTHS[anchor.getMonth()]} ${anchor.getFullYear()}`}
            </span>
            <button type="button" className="cp-round" onClick={() => shift(1)} aria-label="Вперёд">
              <ChevronRight size={18} aria-hidden="true" />
            </button>
            <button type="button" className="cp-pill" onClick={() => setAnchor(new Date())}>
              Сегодня
            </button>
          </div>
        )}

        {items === null ? (
          <p className="cp-muted cp-center">Загружаю…</p>
        ) : view === "week" ? (
          <WeekView
            start={range[0]}
            byDay={byDay}
            today={today}
            theme={meta?.week_themes[iso(range[0])] ?? ""}
            bind={bind}
            over={ghost?.over ?? null}
            onOpen={setDetail}
            onAdd={(day) => setSheet({ mode: "new", day })}
            onToggle={(it) => update(it, { stage: isDone(it) ? "script" : "filmed" })}
            onTheme={() => setSheet({ mode: "theme", week: iso(range[0]) })}
          />
        ) : view === "month" ? (
          <MonthView
            anchor={anchor}
            start={range[0]}
            byDay={byDay}
            today={today}
            bind={bind}
            over={ghost?.over ?? null}
            onOpen={setDetail}
            onDay={(d) => setSheet({ mode: "day", day: d })}
          />
        ) : view === "bank" ? (
          <BankView items={items} onOpen={setDetail} onAdd={() => setSheet({ mode: "new", day: null })} onPlan={(it) => setSheet({ mode: "move", item: it })} />
        ) : (
          <StatsView start={range[0]} end={range[1]} version={version} />
        )}
      </div>

      {(view === "week" || view === "bank") && !detail && (
        <button type="button" className="cp-fab" onClick={() => setSheet({ mode: "new", day: view === "bank" ? null : today })}>
          <Plus size={18} aria-hidden="true" /> Идея
        </button>
      )}

      <nav className="cp-tabs" aria-label="Разделы плана">
        {(
          [
            ["week", "Неделя"],
            ["month", "Месяц"],
            ["bank", "Банк идей"],
            ["stats", "Итоги"],
          ] as [View, string][]
        ).map(([v, l]) => (
          <button key={v} type="button" aria-current={view === v ? "page" : undefined} onClick={() => setView(v)}>
            {l}
          </button>
        ))}
      </nav>

      {ghost && (
        <div className="cp-ghost" style={{ left: ghost.x, top: ghost.y }} aria-hidden="true">
          {ghost.title}
        </div>
      )}

      {detail && (
        <Detail
          item={detail}
          onClose={() => setDetail(null)}
          onChange={(f, ok) => update(detail, f, ok)}
          onEdit={() => setSheet({ mode: "edit", item: detail })}
          onMove={() => setSheet({ mode: "move", item: detail })}
          onAddRefs={() => setSheet({ mode: "refs", item: detail })}
          onDuplicate={async () => {
            const r = await act(() => contentApi.duplicate(detail.id), "Создана копия");
            if (r) setDetail(r);
          }}
          onDelete={async () => {
            if (await act(() => contentApi.remove(detail.id), "Удалено")) setDetail(null);
          }}
          onRemoveRef={async (refId) => {
            if (await act(() => contentApi.removeRef(refId))) setDetail((d) => (d ? { ...d, refs: d.refs.filter((r) => r.id !== refId) } : d));
          }}
        />
      )}

      {sheet && (
        <SheetFrame onClose={() => setSheet(null)}>
          {sheet.mode === "new" || sheet.mode === "edit" ? (
            <ItemForm
              item={sheet.mode === "edit" ? sheet.item : null}
              day={sheet.mode === "new" ? sheet.day : sheet.item.day}
              onCancel={() => setSheet(null)}
              onSave={async (f, drafts) => {
                if (sheet.mode === "edit") {
                  const r = await update(sheet.item, f, "Сохранено");
                  if (r) setSheet(null);
                  return;
                }
                const created = await act(() => contentApi.create({ ...f, title: f.title ?? "" }));
                if (!created) return;
                await uploadDrafts(created.id, drafts);
                toast("Идея добавлена", "info");
                setSheet(null);
              }}
            />
          ) : sheet.mode === "move" ? (
            <MoveForm
              item={sheet.item}
              onCancel={() => setSheet(null)}
              onSave={async (day) => {
                const r = await update(sheet.item, day ? { day } : { to_bank: true }, day ? `Перенесено на ${label(fromIso(day))}` : "Убрано в банк идей");
                if (r) setSheet(null);
              }}
            />
          ) : sheet.mode === "refs" ? (
            <RefsForm
              title={sheet.item.title}
              onCancel={() => setSheet(null)}
              onSave={async (drafts) => {
                const added = await uploadDrafts(sheet.item.id, drafts);
                if (added.length) {
                  setDetail((d) => (d ? { ...d, refs: [...d.refs, ...added] } : d));
                  toast(`Добавлено референсов: ${added.length}`, "info");
                }
                setSheet(null);
              }}
            />
          ) : sheet.mode === "day" ? (
            <DayList
              day={sheet.day}
              items={byDay.get(sheet.day) ?? []}
              onOpen={(it) => {
                setSheet(null);
                setDetail(it);
              }}
              onAdd={() => setSheet({ mode: "new", day: sheet.day })}
              onWeek={() => {
                setAnchor(fromIso(sheet.day));
                setView("week");
                setSheet(null);
              }}
            />
          ) : sheet.mode === "meta" ? (
            <MetaForm
              meta={meta}
              onCancel={() => setSheet(null)}
              onSave={async (m) => {
                const r = await act(() => contentApi.setMeta(m), "Сохранено");
                if (r) {
                  setMeta(r);
                  setSheet(null);
                }
              }}
            />
          ) : (
            <ThemeForm
              value={meta?.week_themes[sheet.week] ?? ""}
              onCancel={() => setSheet(null)}
              onSave={async (text) => {
                const r = await act(() => contentApi.setMeta({ week_of: sheet.week, week_theme: text }));
                if (r) {
                  setMeta(r);
                  setSheet(null);
                }
              }}
            />
          )}
        </SheetFrame>
      )}
    </div>
  );
}

/* ================================================================ views */

type Bind = ReturnType<typeof useDragMove>["bind"];

function WeekView(props: {
  start: Date;
  byDay: Map<string, ContentItem[]>;
  today: string;
  theme: string;
  bind: Bind;
  over: string | null;
  onOpen: (it: ContentItem) => void;
  onAdd: (day: string) => void;
  onToggle: (it: ContentItem) => void;
  onTheme: () => void;
}) {
  const days = Array.from({ length: 7 }, (_, i) => addDays(props.start, i));
  const all = days.flatMap((d) => props.byDay.get(iso(d)) ?? []);
  return (
    <>
      <button type="button" className="cp-theme" onClick={props.onTheme}>
        <span className="cp-theme-text">
          <b>ТЕМА НЕДЕЛИ</b>
          <span>{props.theme || "Нажмите, чтобы задать тему"}</span>
        </span>
        <span className="cp-hand-sm">
          {all.filter(isDone).length} / {all.length} снято
        </span>
      </button>
      <section className="cp-days">
        {days.map((d, i) => {
          const key = iso(d);
          const its = props.byDay.get(key) ?? [];
          return (
            <div key={key} className={`cp-day ${key === props.today ? "is-today" : ""} ${props.over === key ? "is-over" : ""}`} data-drop-day={key}>
              <div className="cp-date">
                <span>{WD[i]}</span>
                <b>{d.getDate()}</b>
              </div>
              <div className="cp-day-items">
                {its.length === 0 && <span className="cp-free">свободный день</span>}
                {its.map((it) => {
                  const Icon = iconOf(it.icon);
                  return (
                    <div key={it.id} className="cp-item" {...props.bind(it.id, it.title)}>
                      <button type="button" className="cp-item-main" onClick={() => props.onOpen(it)}>
                        <span className="cp-item-title">{it.title}</span>
                        <span className="cp-item-meta">
                          <span className="cp-dot" style={{ background: RUBRIC[it.rubric].color }} />
                          {RUBRIC[it.rubric].label}
                          {it.publish_time && ` · ${it.publish_time}`}
                          {it.platforms.length > 0 && ` · ${it.platforms.map((p) => PLATFORM[p].short).join(" ")}`}
                          {it.refs.length > 0 && (
                            <>
                              {" · "}
                              <Paperclip size={11} aria-hidden="true" /> {it.refs.length}
                            </>
                          )}
                        </span>
                      </button>
                      <Icon size={20} strokeWidth={1.4} aria-hidden="true" className="cp-item-icon" />
                      <button
                        type="button"
                        className={`cp-check ${isDone(it) ? "on" : ""}`}
                        onClick={() => props.onToggle(it)}
                        aria-label={isDone(it) ? "Отметить как не снятое" : "Отметить как снятое"}
                      >
                        <Check size={14} strokeWidth={3} aria-hidden="true" />
                      </button>
                    </div>
                  );
                })}
              </div>
              <button type="button" className="cp-add" onClick={() => props.onAdd(key)} aria-label={`Добавить идею на ${label(d)}`}>
                <Plus size={16} aria-hidden="true" />
              </button>
            </div>
          );
        })}
      </section>
      <p className="cp-hint">Удерживайте идею и перетащите на другой день.</p>
    </>
  );
}

function MonthView(props: {
  anchor: Date;
  start: Date;
  byDay: Map<string, ContentItem[]>;
  today: string;
  bind: Bind;
  over: string | null;
  onOpen: (it: ContentItem) => void;
  onDay: (day: string) => void;
}) {
  const cells = Array.from({ length: 42 }, (_, i) => addDays(props.start, i));
  const rows = cells[35].getMonth() !== props.anchor.getMonth() && cells[35] > cells[34] && cells[35].getDate() <= 7 ? 35 : 42;
  const month = props.anchor.getMonth();
  return (
    <>
      <div className="cp-legend">
        {RUBRICS.slice(0, 4).map(([k, l, c]) => (
          <span key={k}>
            <span className="cp-dot" style={{ background: c }} />
            {l}
          </span>
        ))}
      </div>
      <div className="cp-month">
        {WD.map((w) => (
          <span key={w} className="cp-month-wd">
            {w}
          </span>
        ))}
        {cells.slice(0, rows).map((d) => {
          const key = iso(d);
          const its = props.byDay.get(key) ?? [];
          return (
            <div
              key={key}
              className={`cp-cell ${d.getMonth() !== month ? "is-out" : ""} ${key === props.today ? "is-today" : ""} ${props.over === key ? "is-over" : ""}`}
              data-drop-day={key}
            >
              <button type="button" className="cp-cell-num" onClick={() => props.onDay(key)} aria-label={`Идеи на ${label(d)}`}>
                {d.getDate()}
              </button>
              {its.slice(0, 3).map((it) => (
                <button
                  type="button"
                  key={it.id}
                  className={`cp-chip ${isDone(it) ? "done" : ""}`}
                  style={{ background: RUBRIC[it.rubric].color }}
                  title={it.title}
                  onClick={() => props.onOpen(it)}
                  {...props.bind(it.id, it.title)}
                >
                  {it.title}
                </button>
              ))}
              {its.length > 3 && (
                <button type="button" className="cp-more" onClick={() => props.onDay(key)}>
                  +{its.length - 3}
                </button>
              )}
            </div>
          );
        })}
      </div>
      <p className="cp-hint">Нажмите на идею — откроется её карточка, на число — все идеи дня. Идею можно перетащить на другой день.</p>
    </>
  );
}

function BankView(props: { items: ContentItem[]; onOpen: (it: ContentItem) => void; onAdd: () => void; onPlan: (it: ContentItem) => void }) {
  return (
    <section className="cp-bank">
      <p className="cp-hand-sm cp-center">идеи без даты — на потом</p>
      {props.items.length === 0 && (
        <div className="cp-empty-card">
          <p>Банк идей пуст.</p>
          <button type="button" className="cp-btn" onClick={props.onAdd}>
            Добавить идею
          </button>
        </div>
      )}
      {props.items.map((it) => {
        const Icon = iconOf(it.icon);
        return (
          <div key={it.id} className="cp-item cp-bank-item">
            <Icon size={20} strokeWidth={1.4} aria-hidden="true" className="cp-item-icon" />
            <button type="button" className="cp-item-main" onClick={() => props.onOpen(it)}>
              <span className="cp-item-title">{it.title}</span>
              <span className="cp-item-meta">
                <span className="cp-dot" style={{ background: RUBRIC[it.rubric].color }} />
                {RUBRIC[it.rubric].label}
                {it.refs.length > 0 && ` · референсов: ${it.refs.length}`}
              </span>
            </button>
            <button type="button" className="cp-pill" onClick={() => props.onPlan(it)}>
              В план
            </button>
          </div>
        );
      })}
    </section>
  );
}

function StatsView({ start, end, version }: { start: Date; end: Date; version: number }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof contentApi.stats>> | null>(null);
  useEffect(() => {
    contentApi.stats(iso(start), iso(end)).then(setData).catch(() => setData(null));
  }, [start, end, version]);
  if (!data) return <p className="cp-muted cp-center">Считаю…</p>;
  const total = Math.max(1, data.total);
  return (
    <section className="cp-stats">
      <div className="cp-stat-row">
        <div className="cp-stat">
          <b>{data.total}</b>
          <span>идей в месяце</span>
        </div>
        <div className="cp-stat">
          <b>{data.by_stage.filmed + data.by_stage.published}</b>
          <span>снято</span>
        </div>
        <div className="cp-stat">
          <b>{data.by_stage.published}</b>
          <span>опубликовано</span>
        </div>
      </div>
      <div className="cp-card">
        <h2 className="cp-label">ЭТАПЫ</h2>
        {STAGES.map(([k, l]) => (
          <div key={k} className="cp-bar-row">
            <span>{l}</span>
            <span className="cp-bar">
              <span style={{ width: `${(data.by_stage[k] / total) * 100}%`, background: "#9c4f5d" }} />
            </span>
            <b>{data.by_stage[k]}</b>
          </div>
        ))}
      </div>
      <div className="cp-card">
        <h2 className="cp-label">РУБРИКИ</h2>
        {RUBRICS.map(([k, l, c]) => (
          <div key={k} className="cp-bar-row">
            <span>{l}</span>
            <span className="cp-bar">
              <span style={{ width: `${((data.by_rubric[k] ?? 0) / total) * 100}%`, background: c }} />
            </span>
            <b>{data.by_rubric[k] ?? 0}</b>
          </div>
        ))}
      </div>
      <p className="cp-hint">Запланировано дней: {data.days_planned}.</p>
    </section>
  );
}

/* ================================================================ detail */

function linkBadge(url: string): [string, string] {
  const h = (() => {
    try {
      return new URL(url).hostname;
    } catch {
      return "";
    }
  })();
  if (h.includes("tiktok")) return ["TT", "#2b2423"];
  if (h.includes("pinterest") || h.includes("pin.it")) return ["P", "#9c4f5d"];
  if (h.includes("instagram")) return ["IG", "#b0567a"];
  if (h.includes("youtu")) return ["YT", "#8f2f3f"];
  return ["", "#6e6360"];
}

function Detail(props: {
  item: ContentItem;
  onClose: () => void;
  onChange: (f: ItemFields, ok?: string) => void;
  onEdit: () => void;
  onMove: () => void;
  onAddRefs: () => void;
  onDuplicate: () => void;
  onDelete: () => void;
  onRemoveRef: (id: number) => void;
}) {
  const it = props.item;
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [viewer, setViewer] = useState<string | null>(null);
  const photos = it.refs.filter((r) => r.kind === "photo");
  const links = it.refs.filter((r) => r.kind === "link");
  const stageIdx = STAGES.findIndex(([k]) => k === it.stage);
  const day = it.day ? fromIso(it.day) : null;
  return (
    <div className="cp-detail" role="dialog" aria-label={it.title}>
      <div className="cp-col">
        <header className="cp-detail-head">
          <button type="button" className="cp-round" onClick={props.onClose} aria-label="Назад">
            <ArrowLeft size={18} aria-hidden="true" />
          </button>
          <span className="cp-label">{day ? `${WD[(day.getDay() + 6) % 7]}, ${label(day).toUpperCase()}` : "БАНК ИДЕЙ"}</span>
          <button type="button" className="cp-round" onClick={props.onEdit} aria-label="Редактировать">
            <Pencil size={16} aria-hidden="true" />
          </button>
        </header>

        <h1 className="cp-detail-title">{it.title}</h1>
        <div className="cp-chips">
          <span className="cp-chip-lg" style={{ background: RUBRIC[it.rubric].color }}>
            {RUBRIC[it.rubric].label}
          </span>
          {it.publish_time && <span className="cp-chip-lg outline">публикация {it.publish_time}</span>}
          {it.platforms.map((p) => (
            <span key={p} className="cp-chip-lg outline">
              {PLATFORM[p].label}
            </span>
          ))}
        </div>

        <div className="cp-stages" role="group" aria-label="Этап">
          {STAGES.map(([k, l], i) => (
            <button key={k} type="button" className={`cp-stage ${i <= stageIdx ? "on" : ""} ${i === stageIdx + 1 ? "next" : ""}`} onClick={() => props.onChange({ stage: k })} aria-pressed={k === it.stage}>
              <span className="cp-stage-dot">{i <= stageIdx && <Check size={13} strokeWidth={3} aria-hidden="true" />}</span>
              {l}
            </button>
          ))}
        </div>

        {it.hook && (
          <section className="cp-hook">
            <span className="cp-label">ХУК · ПЕРВЫЕ 2 СЕКУНДЫ</span>
            <p>«{it.hook}»</p>
          </section>
        )}

        <section className="cp-refs">
          <div className="cp-refs-head">
            <h2 className="cp-label">РЕФЕРЕНСЫ · {it.refs.length}</h2>
            <button type="button" className="cp-pill" onClick={props.onAddRefs}>
              <Plus size={14} aria-hidden="true" /> Добавить
            </button>
          </div>
          {photos.length > 0 && (
            <div className="cp-photos">
              {photos.map((p) => (
                <figure key={p.id} className="cp-photo-fig">
                  <div className="cp-photo">
                    <button type="button" className="cp-photo-open" onClick={() => setViewer(p.url)} aria-label="Открыть фото">
                      <img src={p.url} alt={p.caption || "Референс"} loading="lazy" />
                    </button>
                    <button type="button" className="cp-x" onClick={() => props.onRemoveRef(p.id)} aria-label="Удалить фото">
                      <X size={12} aria-hidden="true" />
                    </button>
                  </div>
                  {p.caption && <figcaption>{p.caption}</figcaption>}
                </figure>
              ))}
            </div>
          )}
          {links.map((l) => {
            const [badge, color] = linkBadge(l.url);
            return (
              <div key={l.id} className="cp-link">
                <span className="cp-link-badge" style={{ background: color }}>
                  {badge || <Globe size={15} aria-hidden="true" />}
                </span>
                <a href={l.url} target="_blank" rel="noopener noreferrer" className="cp-link-text">
                  <b>{l.url.replace(/^https?:\/\/(www\.)?/, "")}</b>
                  {l.caption && <span>{l.caption}</span>}
                </a>
                <button type="button" className="cp-x static" onClick={() => props.onRemoveRef(l.id)} aria-label="Удалить ссылку">
                  <X size={12} aria-hidden="true" />
                </button>
              </div>
            );
          })}
          {it.refs.length === 0 && <p className="cp-muted">Добавьте фото, скриншоты или ссылки на ролики, которые вдохновляют.</p>}
        </section>

        {it.note && (
          <section className="cp-card">
            <h2 className="cp-label">ЗАМЕТКА / СЦЕНАРИЙ</h2>
            <p className="cp-note">{it.note}</p>
          </section>
        )}

        <div className="cp-actions">
          <button type="button" className="cp-btn ghost" onClick={props.onMove}>
            Перенести
          </button>
          <button type="button" className="cp-btn ghost" onClick={props.onDuplicate}>
            <Copy size={14} aria-hidden="true" /> Копия
          </button>
          {confirmDelete ? (
            <button type="button" className="cp-btn danger" onClick={props.onDelete}>
              Точно удалить
            </button>
          ) : (
            <button type="button" className="cp-btn ghost danger-text" onClick={() => setConfirmDelete(true)}>
              <Trash2 size={14} aria-hidden="true" /> Удалить
            </button>
          )}
        </div>
      </div>

      {viewer && (
        <button type="button" className="cp-viewer" onClick={() => setViewer(null)} aria-label="Закрыть фото">
          <img src={viewer} alt="" />
        </button>
      )}
    </div>
  );
}

/* ================================================================ sheets */

function SheetFrame({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="cp-sheet-wrap" onClick={onClose}>
      <div className="cp-sheet" role="dialog" onClick={(e) => e.stopPropagation()}>
        <span className="cp-grip" aria-hidden="true" />
        {children}
      </div>
    </div>
  );
}

function ItemForm(props: {
  item: ContentItem | null;
  day: string | null;
  onCancel: () => void;
  onSave: (f: ItemFields & { title: string }, drafts: RefDraft[]) => void;
}) {
  const it = props.item;
  const [title, setTitle] = useState(it?.title ?? "");
  const [rubric, setRubric] = useState<Rubric>(it?.rubric ?? "lifestyle");
  const [icon, setIcon] = useState(it?.icon ?? "sparkles");
  const [day, setDay] = useState(props.day ?? "");
  const [time, setTime] = useState(it?.publish_time ?? "");
  const [hook, setHook] = useState(it?.hook ?? "");
  const [note, setNote] = useState(it?.note ?? "");
  const [platforms, setPlatforms] = useState<Platform[]>(it?.platforms ?? []);
  const [drafts, setDrafts] = useState<RefDraft[]>([]);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim() || busy) return;
    setBusy(true);
    const f: ItemFields & { title: string; to_bank?: boolean } = { title: title.trim(), rubric, icon, publish_time: time, hook, note, platforms };
    if (day) f.day = day;
    else if (it) f.to_bank = true;
    await props.onSave(f, drafts);
    setBusy(false);
  };

  return (
    <form className="cp-form" onSubmit={submit}>
      <div className="cp-form-head">
        <h2>{it ? "Редактировать" : "Новая идея"}</h2>
        <span className="cp-hand-sm">{day ? `на ${label(fromIso(day))}` : "в банк идей"}</span>
      </div>
      <label className="cp-field">
        <span className="cp-label">ЧТО СНИМАЕМ</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} placeholder="Nails day vlog" autoFocus required />
      </label>
      <div className="cp-field">
        <span className="cp-label">РУБРИКА</span>
        <div className="cp-chips">
          {RUBRICS.map(([k, l, c]) => (
            <button key={k} type="button" className="cp-chip-lg pick" style={rubric === k ? { background: c, borderColor: c } : undefined} aria-pressed={rubric === k} onClick={() => setRubric(k)}>
              {l}
            </button>
          ))}
        </div>
      </div>
      <div className="cp-field">
        <span className="cp-label">ПЛОЩАДКИ</span>
        <div className="cp-chips">
          {PLATFORMS.map(([k, l]) => {
            const on = platforms.includes(k);
            return (
              <button
                key={k}
                type="button"
                className={`cp-chip-lg pick ${on ? "on" : ""}`}
                aria-pressed={on}
                onClick={() => setPlatforms((ps) => (on ? ps.filter((x) => x !== k) : [...ps, k]))}
              >
                {l}
              </button>
            );
          })}
        </div>
      </div>
      <div className="cp-field">
        <span className="cp-label">ИКОНКА</span>
        <div className="cp-icons">
          {Object.entries(ICONS).map(([k, Icon]) => (
            <button key={k} type="button" className="cp-icon-btn" aria-pressed={icon === k} aria-label={k} onClick={() => setIcon(k)}>
              <Icon size={19} strokeWidth={1.5} aria-hidden="true" />
            </button>
          ))}
        </div>
      </div>
      <div className="cp-two">
        <label className="cp-field">
          <span className="cp-label">ДЕНЬ</span>
          <input type="date" value={day} onChange={(e) => setDay(e.target.value)} />
        </label>
        <label className="cp-field">
          <span className="cp-label">ПУБЛИКАЦИЯ</span>
          <input type="time" value={time} onChange={(e) => setTime(e.target.value)} />
        </label>
      </div>
      {day && (
        <button type="button" className="cp-linkbtn" onClick={() => setDay("")}>
          Без даты — в банк идей
        </button>
      )}
      <label className="cp-field">
        <span className="cp-label">ХУК · ПЕРВЫЕ 2 СЕКУНДЫ</span>
        <input value={hook} onChange={(e) => setHook(e.target.value)} maxLength={300} placeholder="«Пять вещей, без которых я…»" />
      </label>
      {!it && (
        <div className="cp-field">
          <span className="cp-label">РЕФЕРЕНСЫ</span>
          <RefDrafts drafts={drafts} setDrafts={setDrafts} />
        </div>
      )}
      <label className="cp-field">
        <span className="cp-label">ЗАМЕТКА / СЦЕНАРИЙ</span>
        <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={3} maxLength={4000} placeholder="Снять крупно руки, мягкий дневной свет" />
      </label>
      <div className="cp-form-actions">
        <button type="button" className="cp-btn ghost" onClick={props.onCancel}>
          Отмена
        </button>
        <button type="submit" className="cp-btn" disabled={!title.trim() || busy}>
          {busy ? "Сохраняю…" : it ? "Сохранить" : "Добавить в план"}
        </button>
      </div>
    </form>
  );
}

function MoveForm(props: { item: ContentItem; onCancel: () => void; onSave: (day: string | null) => void }) {
  const [day, setDay] = useState(props.item.day ?? iso(new Date()));
  return (
    <form
      className="cp-form"
      onSubmit={(e) => {
        e.preventDefault();
        props.onSave(day || null);
      }}
    >
      <div className="cp-form-head">
        <h2>Перенести</h2>
        <span className="cp-hand-sm">{props.item.title}</span>
      </div>
      <label className="cp-field">
        <span className="cp-label">НОВЫЙ ДЕНЬ</span>
        <input type="date" value={day} onChange={(e) => setDay(e.target.value)} required />
      </label>
      <div className="cp-form-actions">
        {props.item.day && (
          <button type="button" className="cp-btn ghost" onClick={() => props.onSave(null)}>
            В банк идей
          </button>
        )}
        <button type="submit" className="cp-btn">
          Перенести
        </button>
      </div>
    </form>
  );
}

function RefDrafts({ drafts, setDrafts }: { drafts: RefDraft[]; setDrafts: React.Dispatch<React.SetStateAction<RefDraft[]>> }) {
  const file = useRef<HTMLInputElement>(null);
  useEffect(
    () => () => drafts.forEach((d) => d.kind === "photo" && URL.revokeObjectURL(d.preview)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const patch = (key: number, caption: string) => setDrafts((ds) => ds.map((d) => (d.key === key ? { ...d, caption } : d)));
  return (
    <div className="cp-drafts">
      {drafts.map((d) => (
        <div key={d.key} className="cp-draft">
          {d.kind === "photo" ? (
            <img src={d.preview} alt="" className="cp-draft-thumb" />
          ) : (
            <span className="cp-draft-thumb link">
              <Link2 size={18} aria-hidden="true" />
            </span>
          )}
          <div className="cp-draft-fields">
            {d.kind === "link" && (
              <input
                value={d.url}
                onChange={(e) => setDrafts((ds) => ds.map((x) => (x.key === d.key && x.kind === "link" ? { ...x, url: e.target.value } : x)))}
                placeholder="https://…"
                inputMode="url"
                aria-label="Ссылка"
              />
            )}
            <input value={d.caption} onChange={(e) => patch(d.key, e.target.value)} maxLength={300} placeholder="Комментарий: что нравится" aria-label="Комментарий к референсу" />
          </div>
          <button
            type="button"
            className="cp-x static"
            aria-label="Убрать"
            onClick={() => {
              if (d.kind === "photo") URL.revokeObjectURL(d.preview);
              setDrafts((ds) => ds.filter((x) => x.key !== d.key));
            }}
          >
            <X size={12} aria-hidden="true" />
          </button>
        </div>
      ))}
      <div className="cp-ref-pick">
        <button type="button" className="cp-dashed wide" onClick={() => file.current?.click()}>
          <ImagePlus size={18} aria-hidden="true" /> Фото
        </button>
        <button type="button" className="cp-dashed wide" onClick={() => setDrafts((ds) => [...ds, { key: ++draftKey, kind: "link", url: "", caption: "" }])}>
          <Link2 size={18} aria-hidden="true" /> Ссылка
        </button>
      </div>
      <input
        ref={file}
        type="file"
        accept="image/*"
        multiple
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []).slice(0, 20);
          e.target.value = "";
          setDrafts((ds) => [...ds, ...files.map((f) => ({ key: ++draftKey, kind: "photo" as const, file: f, preview: URL.createObjectURL(f), caption: "" }))]);
        }}
      />
    </div>
  );
}

function RefsForm(props: { title: string; onCancel: () => void; onSave: (drafts: RefDraft[]) => Promise<void> }) {
  const [drafts, setDrafts] = useState<RefDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const ready = drafts.some((d) => d.kind === "photo" || d.url.trim());
  return (
    <form
      className="cp-form"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!ready || busy) return;
        setBusy(true);
        await props.onSave(drafts);
        setBusy(false);
      }}
    >
      <div className="cp-form-head">
        <h2>Референсы</h2>
        <span className="cp-hand-sm">{props.title}</span>
      </div>
      <p className="cp-muted cp-small">Добавьте сразу несколько фото и ссылок, у каждого — свой комментарий.</p>
      <RefDrafts drafts={drafts} setDrafts={setDrafts} />
      <div className="cp-form-actions">
        <button type="button" className="cp-btn ghost" onClick={props.onCancel}>
          Отмена
        </button>
        <button type="submit" className="cp-btn" disabled={!ready || busy}>
          {busy ? "Загружаю…" : "Сохранить"}
        </button>
      </div>
    </form>
  );
}

function DayList(props: { day: string; items: ContentItem[]; onOpen: (it: ContentItem) => void; onAdd: () => void; onWeek: () => void }) {
  const d = fromIso(props.day);
  return (
    <div className="cp-form">
      <div className="cp-form-head">
        <h2>{label(d)}</h2>
        <span className="cp-hand-sm">{WD[(d.getDay() + 6) % 7].toLowerCase()}</span>
      </div>
      {props.items.length === 0 && <p className="cp-muted">На этот день идей пока нет.</p>}
      {props.items.map((it) => {
        const Icon = iconOf(it.icon);
        return (
          <button key={it.id} type="button" className="cp-daylist-item" onClick={() => props.onOpen(it)}>
            <Icon size={20} strokeWidth={1.4} aria-hidden="true" />
            <span className="cp-item-main">
              <span className="cp-item-title">{it.title}</span>
              <span className="cp-item-meta">
                <span className="cp-dot" style={{ background: RUBRIC[it.rubric].color }} />
                {RUBRIC[it.rubric].label} · {STAGES.find(([k]) => k === it.stage)?.[1].toLowerCase()}
                {it.publish_time && ` · ${it.publish_time}`}
              </span>
            </span>
            <ChevronRight size={18} aria-hidden="true" />
          </button>
        );
      })}
      <div className="cp-form-actions">
        <button type="button" className="cp-btn ghost" onClick={props.onWeek}>
          Открыть неделю
        </button>
        <button type="button" className="cp-btn" onClick={props.onAdd}>
          <Plus size={16} aria-hidden="true" /> Идея
        </button>
      </div>
    </div>
  );
}

function MetaForm(props: { meta: ContentMeta | null; onCancel: () => void; onSave: (m: Partial<ContentMeta>) => void }) {
  const [title, setTitle] = useState(props.meta?.title ?? "Контент-план");
  const [goal, setGoal] = useState(props.meta?.goal ?? "");
  const [motto, setMotto] = useState(props.meta?.motto ?? "");
  const [tags, setTags] = useState((props.meta?.tags ?? []).join(", "));
  return (
    <form
      className="cp-form"
      onSubmit={(e) => {
        e.preventDefault();
        props.onSave({ title, goal, motto, tags: tags.split(",").map((t) => t.trim()).filter(Boolean) });
      }}
    >
      <div className="cp-form-head">
        <h2>Шапка плана</h2>
      </div>
      <label className="cp-field">
        <span className="cp-label">НАЗВАНИЕ</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={60} />
      </label>
      <label className="cp-field">
        <span className="cp-label">ЦЕЛЬ (ПОМЕТКА СЛЕВА)</span>
        <input value={goal} onChange={(e) => setGoal(e.target.value)} maxLength={60} placeholder="+5 000 за месяц ♡" />
      </label>
      <label className="cp-field">
        <span className="cp-label">ДЕВИЗ (ПОМЕТКА СПРАВА)</span>
        <input value={motto} onChange={(e) => setMotto(e.target.value)} maxLength={80} placeholder="жизнь после 19:00" />
      </label>
      <label className="cp-field">
        <span className="cp-label">ТЕМЫ ЧЕРЕЗ ЗАПЯТУЮ</span>
        <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="лайфстайл, бьюти, стиль, офис" />
      </label>
      <div className="cp-form-actions">
        <button type="button" className="cp-btn ghost" onClick={props.onCancel}>
          Отмена
        </button>
        <button type="submit" className="cp-btn">
          Сохранить
        </button>
      </div>
    </form>
  );
}

function ThemeForm(props: { value: string; onCancel: () => void; onSave: (text: string) => void }) {
  const [text, setText] = useState(props.value);
  return (
    <form
      className="cp-form"
      onSubmit={(e) => {
        e.preventDefault();
        props.onSave(text.trim());
      }}
    >
      <div className="cp-form-head">
        <h2>Тема недели</h2>
      </div>
      <label className="cp-field">
        <span className="cp-label">О ЧЁМ ЭТА НЕДЕЛЯ</span>
        <input value={text} onChange={(e) => setText(e.target.value)} maxLength={80} placeholder="Усиливаем офис + lifestyle" autoFocus />
      </label>
      <div className="cp-form-actions">
        <button type="button" className="cp-btn ghost" onClick={props.onCancel}>
          Отмена
        </button>
        <button type="submit" className="cp-btn">
          Сохранить
        </button>
      </div>
    </form>
  );
}
