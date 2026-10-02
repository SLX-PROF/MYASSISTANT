import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronLeft, ChevronRight, Plus, Trash2, X } from "lucide-react";
import "../styles/finance.css";
import { useStore } from "../lib/store";
import { financeApi, fromIso, MONTHS, MONTHS_GEN, rub, type FinCategory, type FinRecurring, type FinSummary, type FinTx, type Parsed } from "../lib/modules";

type Tab = "overview" | "ops" | "setup";

const ym = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const dayLabel = (s: string) => {
  const d = fromIso(s);
  return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}`;
};

/** "1 234,50" or "1234.5" -> kopecks */
function toKop(v: string): number {
  const n = Number(v.replace(/\s/g, "").replace(",", "."));
  return Number.isFinite(n) && n > 0 ? Math.round(n * 100) : 0;
}

export function FinancePage() {
  const { toast } = useStore();
  const [tab, setTab] = useState<Tab>("overview");
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const [version, setVersion] = useState(0);
  const reload = useCallback(() => setVersion((v) => v + 1), []);
  const shift = (n: number) => setMonth((m) => new Date(m.getFullYear(), m.getMonth() + n, 1));

  return (
    <div className="page scroll fin">
      <div className="page-inner">
        <div className="fin-top">
          <div className="segmented" role="tablist" aria-label="Раздел финансов">
            {(
              [
                ["overview", "Обзор"],
                ["ops", "Операции"],
                ["setup", "Настройки"],
              ] as [Tab, string][]
            ).map(([k, l]) => (
              <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
                {l}
              </button>
            ))}
          </div>
          {tab !== "setup" && (
            <div className="fin-month">
              <button type="button" className="icon-btn" onClick={() => shift(-1)} aria-label="Предыдущий месяц">
                <ChevronLeft size={18} aria-hidden="true" />
              </button>
              <span>
                {MONTHS[month.getMonth()]} {month.getFullYear()}
              </span>
              <button type="button" className="icon-btn" onClick={() => shift(1)} aria-label="Следующий месяц">
                <ChevronRight size={18} aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
        {tab === "overview" && <Overview month={ym(month)} version={version} reload={reload} toast={toast} onSetup={() => setTab("setup")} />}
        {tab === "ops" && <Operations month={ym(month)} version={version} reload={reload} toast={toast} />}
        {tab === "setup" && <Setup toast={toast} reload={reload} version={version} />}
      </div>
    </div>
  );
}

type Toast = ReturnType<typeof useStore>["toast"];

/* ============================================================= overview */

function Ring({ pct }: { pct: number }) {
  const r = 52;
  const c = 2 * Math.PI * r;
  const shown = Math.min(1, pct);
  const color = pct > 1 ? "var(--danger)" : pct >= 0.8 ? "var(--warning)" : "var(--accent)";
  return (
    <svg width="124" height="124" viewBox="0 0 124 124" role="img" aria-label={`Потрачено ${Math.round(pct * 100)}% бюджета`}>
      <circle cx="62" cy="62" r={r} fill="none" stroke="var(--border-strong)" strokeWidth="12" />
      <circle cx="62" cy="62" r={r} fill="none" stroke={color} strokeWidth="12" strokeLinecap="round" strokeDasharray={`${shown * c} ${c}`} transform="rotate(-90 62 62)" />
      <text x="62" y="60" textAnchor="middle" className="fin-ring-num">
        {Math.round(pct * 100)}%
      </text>
      <text x="62" y="80" textAnchor="middle" className="fin-ring-sub">
        бюджета
      </text>
    </svg>
  );
}

function Overview({ month, version, reload, toast, onSetup }: { month: string; version: number; reload: () => void; toast: Toast; onSetup: () => void }) {
  const [s, setS] = useState<FinSummary | null>(null);
  useEffect(() => {
    financeApi.summary(month).then(setS).catch((e) => toast((e as Error).message, "error"));
  }, [month, version, toast]);
  if (!s) return <p className="hint">Загружаю…</p>;
  const pct = s.budget ? s.expense / s.budget : 0;
  return (
    <>
      <section className="surface fin-hero">
        {s.budget ? (
          <>
            <Ring pct={pct} />
            <div className="fin-hero-text">
              <span className="fin-k">{s.remaining >= 0 ? "Осталось на месяц" : "Перерасход"}</span>
              <b className={`fin-big ${s.remaining < 0 ? "neg" : ""}`}>{rub(Math.abs(s.remaining))}</b>
              {s.per_day > 0 && (
                <span className="fin-k">
                  Можно тратить <b>{rub(s.per_day)}</b> в день
                </span>
              )}
              <span className="fin-k">
                Потрачено {rub(s.expense)} из {rub(s.budget)}
              </span>
            </div>
          </>
        ) : (
          <div className="fin-hero-text">
            <span className="fin-k">Расходы за месяц</span>
            <b className="fin-big">{rub(s.expense)}</b>
            <span className="fin-k">Бюджет не задан: поставьте лимиты категориям, и здесь появится остаток на месяц и на день.</span>
            <button type="button" className="btn btn-sm" onClick={onSetup}>
              Задать лимиты
            </button>
          </div>
        )}
      </section>
      <div className="fin-pair">
        <div className="surface fin-mini">
          <span className="fin-k">Доходы</span>
          <b className="pos">+{rub(s.income)}</b>
        </div>
        <div className="surface fin-mini">
          <span className="fin-k">Расходы</span>
          <b>−{rub(s.expense)}</b>
        </div>
      </div>

      <section className="surface fin-block">
        <h2>Категории</h2>
        {s.categories.length === 0 && <p className="hint">Пока нет расходов. Напишите Атласу «кофе 350» или добавьте во вкладке «Операции».</p>}
        {s.categories.map((c) => {
          const p = c.limit ? c.spent / c.limit : 0;
          const state = !c.limit ? "" : p > 1 ? "over" : p >= 0.8 ? "warn" : "";
          return (
            <div key={c.name} className="fin-cat">
              <div className="fin-cat-row">
                <span>
                  <span className="fin-dot" style={{ background: c.color }} />
                  {c.name}
                </span>
                <span className={`fin-cat-sum ${state}`}>
                  {rub(c.spent)}
                  {c.limit ? ` / ${rub(c.limit)}` : ""}
                </span>
              </div>
              {c.limit > 0 && (
                <div className="fin-bar">
                  <span className={state} style={{ width: `${Math.min(100, p * 100)}%` }} />
                </div>
              )}
            </div>
          );
        })}
      </section>

      <section className="surface fin-block">
        <h2>Скоро платежи</h2>
        {s.upcoming.length === 0 && <p className="hint">Регулярных платежей нет. Добавьте аренду, подписки и кредиты в «Настройках» — Атлас напомнит заранее.</p>}
        {s.upcoming.map((r) => (
          <div key={r.id} className="fin-pay">
            <div>
              <b>{r.title}</b>
              <span className="fin-k">
                {dayLabel(r.next_due)} · {r.amount_text}
              </span>
            </div>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() =>
                financeApi
                  .paid(r.id)
                  .then(() => {
                    toast(`Оплачено: ${r.title}`, "info");
                    reload();
                  })
                  .catch((e) => toast((e as Error).message, "error"))
              }
            >
              <Check size={14} aria-hidden="true" /> Оплачено
            </button>
          </div>
        ))}
      </section>
    </>
  );
}

/* ============================================================ operations */

function Operations({ month, version, reload, toast }: { month: string; version: number; reload: () => void; toast: Toast }) {
  const [txs, setTxs] = useState<FinTx[] | null>(null);
  const [cats, setCats] = useState<FinCategory[]>([]);
  const [text, setText] = useState("");
  const [preview, setPreview] = useState<Parsed | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    financeApi.transactions(month).then(setTxs).catch((e) => toast((e as Error).message, "error"));
  }, [month, version, toast]);
  useEffect(() => {
    financeApi.categories().then(setCats).catch(() => undefined);
  }, [version]);

  const groups = useMemo(() => {
    const m = new Map<string, FinTx[]>();
    for (const t of txs ?? []) m.set(t.day, [...(m.get(t.day) ?? []), t]);
    return [...m.entries()];
  }, [txs]);
  const income = (txs ?? []).filter((t) => t.kind === "income").reduce((a, t) => a + t.amount, 0);
  const expense = (txs ?? []).filter((t) => t.kind === "expense").reduce((a, t) => a + t.amount, 0);

  const parse = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      setPreview(await financeApi.parse(text));
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const save = async () => {
    if (!preview) return;
    setBusy(true);
    try {
      const r = await financeApi.add({ amount: preview.amount, kind: preview.kind, day: preview.day, category_id: preview.category_id, note: preview.note });
      toast(r.alerts[0] ?? `Записано: ${preview.note || preview.category} ${preview.amount_text}`, r.alerts.length ? "error" : "info");
      setPreview(null);
      setText("");
      reload();
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="fin-pair">
        <div className="surface fin-mini">
          <span className="fin-k">Доходы за месяц</span>
          <b className="pos">+{rub(income)}</b>
        </div>
        <div className="surface fin-mini">
          <span className="fin-k">Расходы</span>
          <b>−{rub(expense)}</b>
        </div>
      </div>

      <form className="surface fin-quick" onSubmit={parse}>
        <label htmlFor="fin-quick" className="sr-only">
          Новая операция
        </label>
        <input
          id="fin-quick"
          className="input grow"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setPreview(null);
          }}
          placeholder="такси 640 вчера · зарплата 150 000"
          autoComplete="off"
        />
        <button type="submit" className="btn btn-primary" disabled={!text.trim() || busy}>
          Разобрать
        </button>
        {preview && (
          <div className="fin-preview">
            <span className="fin-k">Атлас понял:</span>
            <div className="fin-preview-row">
              <span className={`fin-tag ${preview.kind === "income" ? "pos" : ""}`}>
                {preview.kind === "income" ? "+" : "−"}
                {preview.amount_text}
              </span>
              {preview.note && <span className="fin-tag">{preview.note}</span>}
              <span className="fin-tag">{dayLabel(preview.day)}</span>
              <select
                className="select"
                aria-label="Категория"
                value={preview.category_id ?? ""}
                onChange={(e) => {
                  const c = cats.find((x) => x.id === Number(e.target.value));
                  setPreview({ ...preview, category_id: c?.id ?? null, category: c?.name ?? null, kind: c?.kind ?? preview.kind });
                }}
              >
                {cats.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.kind === "income" ? "＋ " : ""}
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="fin-preview-row">
              <button type="button" className="btn btn-primary" onClick={save} disabled={busy}>
                <Check size={15} aria-hidden="true" /> Сохранить
              </button>
              <button type="button" className="btn" onClick={() => setPreview(null)}>
                Отмена
              </button>
            </div>
          </div>
        )}
      </form>

      {txs === null && <p className="hint">Загружаю…</p>}
      {txs?.length === 0 && <p className="hint">В этом месяце операций нет.</p>}
      {groups.map(([day, list]) => {
        const total = list.reduce((a, t) => a + (t.kind === "income" ? t.amount : -t.amount), 0);
        return (
          <section key={day} className="fin-day">
            <div className="fin-day-head">
              <span>{dayLabel(day)}</span>
              <span>
                {total >= 0 ? "+" : "−"}
                {rub(Math.abs(total))}
              </span>
            </div>
            {list.map((t) => (
              <div key={t.id} className="surface fin-tx">
                <span className="fin-badge" style={{ color: t.color, background: `color-mix(in oklab, ${t.color} 18%, transparent)` }}>
                  {t.category.slice(0, 1)}
                </span>
                <div className="fin-tx-main">
                  <b>{t.note || t.category}</b>
                  <span className="fin-k">{t.category}</span>
                </div>
                <b className={t.kind === "income" ? "pos" : ""}>
                  {t.kind === "income" ? "+" : "−"}
                  {t.amount_text}
                </b>
                <button
                  type="button"
                  className="icon-btn plain"
                  aria-label="Удалить операцию"
                  onClick={() =>
                    financeApi
                      .remove(t.id)
                      .then(reload)
                      .catch((e) => toast((e as Error).message, "error"))
                  }
                >
                  <Trash2 size={15} aria-hidden="true" />
                </button>
              </div>
            ))}
          </section>
        );
      })}
    </>
  );
}

/* ================================================================ setup */

function Setup({ toast, reload, version }: { toast: Toast; reload: () => void; version: number }) {
  const [cats, setCats] = useState<FinCategory[]>([]);
  const [recs, setRecs] = useState<FinRecurring[]>([]);
  const [newCat, setNewCat] = useState("");
  const [rec, setRec] = useState({ title: "", amount: "", day: "1", interval: "1", remind: "2", category: "" });

  useEffect(() => {
    financeApi.categories().then(setCats).catch((e) => toast((e as Error).message, "error"));
    financeApi.recurring().then(setRecs).catch(() => undefined);
  }, [version, toast]);

  const run = (p: Promise<unknown>, ok?: string) =>
    p
      .then(() => {
        if (ok) toast(ok, "info");
        reload();
      })
      .catch((e) => toast((e as Error).message, "error"));

  return (
    <>
      <section className="surface fin-block">
        <h2>Лимиты по категориям</h2>
        <p className="hint">Сумма в рублях на месяц. При 80% и 100% Атлас предупредит в Telegram. 0 — без лимита.</p>
        {cats
          .filter((c) => c.kind === "expense")
          .map((c) => (
            <div key={c.id} className="fin-limit">
              <span>
                <span className="fin-dot" style={{ background: c.color }} />
                {c.name}
              </span>
              <input
                className="input"
                inputMode="numeric"
                aria-label={`Лимит: ${c.name}`}
                defaultValue={c.monthly_limit || ""}
                placeholder="0"
                onBlur={(e) => {
                  const v = Math.max(0, Math.round(Number(e.target.value.replace(/\s/g, "")) || 0));
                  if (v !== c.monthly_limit) run(financeApi.updateCategory(c.id, { monthly_limit: v }), "Лимит сохранён");
                }}
              />
            </div>
          ))}
        <form
          className="fin-add-row"
          onSubmit={(e) => {
            e.preventDefault();
            if (!newCat.trim()) return;
            run(financeApi.addCategory({ name: newCat.trim(), kind: "expense", monthly_limit: 0, color: "#94a3b8" }), "Категория добавлена");
            setNewCat("");
          }}
        >
          <input className="input grow" value={newCat} onChange={(e) => setNewCat(e.target.value)} placeholder="Новая категория расходов" aria-label="Новая категория" />
          <button type="submit" className="btn" disabled={!newCat.trim()}>
            <Plus size={15} aria-hidden="true" /> Добавить
          </button>
        </form>
      </section>

      <section className="surface fin-block">
        <h2>Регулярные платежи</h2>
        <p className="hint">Аренда, подписки, кредиты. Атлас напомнит заранее, а кнопка «Оплачено» запишет расход и сдвинет срок.</p>
        {recs.map((r) => (
          <div key={r.id} className={`fin-pay ${r.active ? "" : "off"}`}>
            <div>
              <b>{r.title}</b>
              <span className="fin-k">
                {r.amount_text} · {r.interval_months === 1 ? "каждый месяц" : r.interval_months === 12 ? "раз в год" : `раз в ${r.interval_months} мес.`}, {r.day_of_month}-го · ближайший {dayLabel(r.next_due)}
              </span>
            </div>
            <button type="button" className="btn btn-sm" onClick={() => run(financeApi.updateRecurring(r.id, { active: !r.active }))}>
              {r.active ? "Пауза" : "Включить"}
            </button>
            <button type="button" className="icon-btn plain" aria-label={`Удалить: ${r.title}`} onClick={() => run(financeApi.removeRecurring(r.id), "Удалено")}>
              <X size={15} aria-hidden="true" />
            </button>
          </div>
        ))}
        <form
          className="fin-rec-form"
          onSubmit={(e) => {
            e.preventDefault();
            const amount = toKop(rec.amount);
            if (!rec.title.trim() || !amount) return toast("Нужны название и сумма", "error");
            run(
              financeApi.addRecurring({
                title: rec.title.trim(),
                amount,
                category_id: rec.category ? Number(rec.category) : null,
                day_of_month: Number(rec.day),
                interval_months: Number(rec.interval),
                remind_days: Number(rec.remind),
              }),
              "Платёж добавлен",
            );
            setRec({ title: "", amount: "", day: "1", interval: "1", remind: "2", category: "" });
          }}
        >
          <input className="input" value={rec.title} onChange={(e) => setRec({ ...rec, title: e.target.value })} placeholder="Название (аренда)" aria-label="Название платежа" />
          <input className="input" value={rec.amount} onChange={(e) => setRec({ ...rec, amount: e.target.value })} placeholder="Сумма, ₽" inputMode="decimal" aria-label="Сумма" />
          <label className="fin-inline">
            Число
            <input className="input" type="number" min={1} max={31} value={rec.day} onChange={(e) => setRec({ ...rec, day: e.target.value })} />
          </label>
          <label className="fin-inline">
            Повтор
            <select className="select" value={rec.interval} onChange={(e) => setRec({ ...rec, interval: e.target.value })}>
              <option value="1">каждый месяц</option>
              <option value="3">раз в квартал</option>
              <option value="12">раз в год</option>
            </select>
          </label>
          <label className="fin-inline">
            Напомнить за
            <select className="select" value={rec.remind} onChange={(e) => setRec({ ...rec, remind: e.target.value })}>
              <option value="0">в день платежа</option>
              <option value="1">1 день</option>
              <option value="2">2 дня</option>
              <option value="3">3 дня</option>
              <option value="7">неделю</option>
            </select>
          </label>
          <label className="fin-inline">
            Категория
            <select className="select" value={rec.category} onChange={(e) => setRec({ ...rec, category: e.target.value })}>
              <option value="">по названию</option>
              {cats
                .filter((c) => c.kind === "expense")
                .map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name}
                  </option>
                ))}
            </select>
          </label>
          <button type="submit" className="btn btn-primary">
            <Plus size={15} aria-hidden="true" /> Добавить платёж
          </button>
        </form>
      </section>
    </>
  );
}

export default FinancePage;
