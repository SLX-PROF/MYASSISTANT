import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ExternalLink, EyeOff, Inbox, MailX, RefreshCw, Sparkles } from "lucide-react";
import "../styles/mail.css";
import { useStore } from "../lib/store";
import { MONTHS_GEN, mailApi, type MailService, type MailStatus } from "../lib/modules";

const ORDER = ["paid", "account", "newsletter", "shop", "work", "social", "travel", "finance", "gov", "other", "private", ""];

function letters(n: number): string {
  const d = n % 10;
  const h = n % 100;
  const w = d === 1 && h !== 11 ? "письмо" : d >= 2 && d <= 4 && (h < 12 || h > 14) ? "письма" : "писем";
  return `${n} ${w}`;
}

function when(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]} ${d.getFullYear()}`;
}

export function MailPage() {
  const { toast } = useStore();
  const [st, setSt] = useState<MailStatus | null>(null);
  const [rows, setRows] = useState<MailService[]>([]);
  const [filter, setFilter] = useState<string>("all");
  const [showDone, setShowDone] = useState(false);
  const [digest, setDigest] = useState<string | null>(null);
  const [busyDigest, setBusyDigest] = useState(false);

  const load = useCallback(async () => {
    try {
      const s = await mailApi.status();
      setSt(s);
      if (s.enabled) setRows(await mailApi.services());
    } catch (e) {
      toast((e as Error).message, "error");
    }
  }, [toast]);

  useEffect(() => {
    load();
  }, [load]);

  const busy = st?.enabled && (st.busy || st.boxes.some((b) => b.pending));
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(load, 2500);
    return () => clearInterval(t);
  }, [busy, load]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of rows) if (r.status !== "hidden") c[r.category] = (c[r.category] ?? 0) + 1;
    return c;
  }, [rows]);

  if (!st) return <p className="hint" style={{ padding: 24 }}>Загружаю…</p>;
  if (!st.enabled) {
    return (
      <div className="page scroll">
        <div className="page-inner">
          <section className="surface mail-block">
            <h2>Почта не подключена</h2>
            <p className="hint">
              Впишите в <code>.env</code> строку <code>MAIL_ACCOUNTS=адрес:пароль_приложения</code> (можно несколько через запятую) и перезапустите Атлас. Атлас только читает почту: ничего не удаляет и не
              отправляет.
            </p>
          </section>
        </div>
      </div>
    );
  }

  const p = st.progress;
  const act = (fn: () => Promise<unknown>, ok?: string) =>
    fn()
      .then(() => {
        if (ok) toast(ok, "info");
        load();
      })
      .catch((e) => toast((e as Error).message, "error"));

  const visible = rows
    .filter((r) => (showDone ? true : r.status === "new"))
    .filter((r) => filter === "all" || r.category === filter)
    .sort((a, b) => ORDER.indexOf(a.category) - ORDER.indexOf(b.category) || b.count - a.count);

  return (
    <div className="page scroll mail">
      <div className="page-inner">
        <section className="surface mail-block">
          <div className="mail-head">
            <div>
              <h2>Разбор почты</h2>
              <span className="hint">{st.accounts.join(", ")}</span>
            </div>
          </div>
          {st.boxes
            .filter((b) => b.oauth)
            .map((b) => (
              <div key={b.address} className="mail-confirm">
                {b.connected ? (
                  <span>
                    {b.address}: вход через Microsoft выполнен <Check size={14} aria-hidden="true" />
                  </span>
                ) : b.pending ? (
                  <span>
                    Откройте{" "}
                    <a href={b.pending.verification_uri} target="_blank" rel="noreferrer noopener">
                      {b.pending.verification_uri.replace("https://", "")}
                    </a>{" "}
                    и введите код <b className="mail-code">{b.pending.user_code}</b>. Войдите в {b.address} и разрешите доступ к почте. Страница обновится сама.
                  </span>
                ) : (
                  <>
                    <span>
                      {b.address}: Outlook подключается через вход Microsoft.{b.error ? ` ${b.error}` : ""}
                    </span>
                    <button type="button" className="btn btn-primary" onClick={() => act(() => mailApi.outlookStart(b.address))}>
                      Войти в Outlook
                    </button>
                  </>
                )}
              </div>
            ))}
          {p.state === "idle" && (
            <>
              <p className="hint">Шаг 1 бесплатный: Атлас прочитает только заголовки всех писем (отправитель, тема, дата) и сгруппирует их по сервисам. В модель ничего не уходит.</p>
              <button type="button" className="btn btn-primary" onClick={() => act(mailApi.collect)}>
                <Inbox size={15} aria-hidden="true" /> Собрать список
              </button>
            </>
          )}
          {(p.state === "collecting" || p.state === "classifying") && (
            <div className="mail-progress" aria-live="polite">
              <RefreshCw size={16} className="spin" aria-hidden="true" />
              {p.state === "collecting"
                ? `${p.message ?? "Читаю…"} ${p.letters ?? 0}${p.total ? ` из ${p.total}` : ""}`
                : `Модель разбирает список: ${p.classified ?? 0} из ${p.pending ?? "…"}`}
            </div>
          )}
          {p.state === "error" && <p className="mail-error">{p.message}</p>}
          {(p.state === "collected" || p.state === "done" || p.state === "error") && (
            <>
              <p className="hint">
                Прочитано {letters(p.letters ?? 0)}: сервисов {p.services ?? rows.length}, писем от людей {p.people ?? 0} (их Атлас не разбирает).
              </p>
              {(p.pending ?? 0) > 0 && (
                <div className="mail-confirm">
                  <span>
                    Шаг 2: отдать модели список из <b>{p.pending}</b> сервисов (домен, число писем, даты, до 3 тем). Примерная стоимость <b>${(p.estimate_usd ?? 0).toFixed(2)}</b>
                    {p.model ? ` (${p.model})` : ""}.
                  </span>
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => {
                      if (window.confirm(`Запустить разбор? Это стоит примерно $${(p.estimate_usd ?? 0).toFixed(2)}.`)) act(mailApi.classify);
                    }}
                  >
                    <Sparkles size={15} aria-hidden="true" /> Разобрать
                  </button>
                </div>
              )}
              <button type="button" className="btn btn-sm" onClick={() => act(mailApi.collect)}>
                <RefreshCw size={14} aria-hidden="true" /> Пересобрать список
              </button>
            </>
          )}
        </section>

        {rows.length > 0 && (
          <>
            <div className="mail-filters" role="tablist" aria-label="Категории">
              <button type="button" role="tab" aria-selected={filter === "all"} onClick={() => setFilter("all")}>
                Все
              </button>
              {ORDER.filter((c) => counts[c]).map((c) => (
                <button key={c} type="button" role="tab" aria-selected={filter === c} onClick={() => setFilter(c)}>
                  {c ? st.categories[c] : "Не разобрано"} · {counts[c]}
                </button>
              ))}
            </div>
            <label className="mail-toggle">
              <input type="checkbox" checked={showDone} onChange={(e) => setShowDone(e.target.checked)} /> Показать обработанные
            </label>
            <ul className="mail-list">
              {visible.length === 0 && <li className="hint">Здесь пусто.</li>}
              {visible.map((r) => (
                <li key={r.id} className={`surface mail-row ${r.status !== "new" ? "handled" : ""}`}>
                  <div className="mail-row-head">
                    <div className="mail-row-name">
                      <b>{r.name || r.domain}</b>
                      <span className="hint">
                        {r.domain} · {letters(r.count)} · последнее {when(r.last_seen)}
                      </span>
                    </div>
                    {r.suggest && <span className={`mail-badge s-${r.suggest}`}>{r.suggest_label}</span>}
                  </div>
                  {(r.price || r.note) && (
                    <p className="mail-note">
                      {r.price && <b>{r.price}</b>}
                      {r.price && r.note ? " · " : ""}
                      {r.note}
                    </p>
                  )}
                  {r.subjects.length > 0 && <p className="hint mail-subj">«{r.subjects.slice(0, 2).join("», «")}»</p>}
                  <div className="mail-actions">
                    {r.status === "unsubscribed" && <span className="mail-done">Отписан ✓</span>}
                    {r.status === "new" && r.one_click && r.unsubscribe.startsWith("https://") && (
                      <button type="button" className="btn btn-sm btn-primary" onClick={() => act(() => mailApi.unsubscribe(r.id), `Отписка от ${r.name}: готово`)}>
                        <MailX size={14} aria-hidden="true" /> Отписаться
                      </button>
                    )}
                    {r.status === "new" && !r.one_click && r.unsubscribe.startsWith("https://") && (
                      <a className="btn btn-sm" href={r.unsubscribe} target="_blank" rel="noreferrer noopener" onClick={() => act(() => mailApi.setStatus(r.id, "unsubscribed"))}>
                        <ExternalLink size={14} aria-hidden="true" /> Ссылка отписки
                      </a>
                    )}
                    {r.status === "new" && r.unsubscribe.startsWith("mailto:") && <span className="hint">отписка письмом: {r.unsubscribe.slice(7, 60)}</span>}
                    {r.status === "new" ? (
                      <>
                        <button type="button" className="btn btn-sm" onClick={() => act(() => mailApi.setStatus(r.id, "keep"))}>
                          <Check size={14} aria-hidden="true" /> Нужно
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => act(() => mailApi.setStatus(r.id, "done"))}>
                          Разобрался
                        </button>
                        <button type="button" className="icon-btn plain" aria-label={`Скрыть ${r.name}`} onClick={() => act(() => mailApi.setStatus(r.id, "hidden"))}>
                          <EyeOff size={15} aria-hidden="true" />
                        </button>
                      </>
                    ) : (
                      <button type="button" className="btn btn-sm" onClick={() => act(() => mailApi.setStatus(r.id, "new"))}>
                        Вернуть в список
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </>
        )}

        <section className="surface mail-block">
          <div className="mail-head">
            <h2>Сводка почты</h2>
            <button
              type="button"
              className="btn btn-sm"
              disabled={busyDigest}
              onClick={() => {
                setBusyDigest(true);
                mailApi
                  .digest()
                  .then((r) => setDigest(r.text))
                  .catch((e) => toast((e as Error).message, "error"))
                  .finally(() => setBusyDigest(false));
              }}
            >
              <RefreshCw size={14} aria-hidden="true" className={busyDigest ? "spin" : ""} /> Сейчас
            </button>
          </div>
          <p className="hint">{st.digest_time ? `Каждый день в ${st.digest_time} сводка приходит в Telegram. В боте: /mail.` : "Ежедневная сводка выключена (MAIL_DIGEST_TIME)."}</p>
          {digest && <pre className="mail-digest">{digest}</pre>}
        </section>
      </div>
    </div>
  );
}

export default MailPage;
