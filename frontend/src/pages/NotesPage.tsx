import { useEffect, useState } from "react";
import { ExternalLink, Lightbulb, Link2, Plus, Search, Trash2 } from "lucide-react";
import { useStore } from "../lib/store";
import { MONTHS_GEN, notesApi, type Note } from "../lib/modules";

const SOURCE: Record<string, string> = { forward: "переслано", voice: "голосом", web: "", chat: "", telegram: "" };

function when(isoStr: string): string {
  const d = new Date(isoStr);
  return `${d.getDate()} ${MONTHS_GEN[d.getMonth()]}`;
}

export function NotesPage() {
  const { toast } = useStore();
  const [notes, setNotes] = useState<Note[] | null>(null);
  const [q, setQ] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => {
      notesApi
        .list(q)
        .then(setNotes)
        .catch((e) => toast((e as Error).message, "error"));
    }, q ? 250 : 0);
    return () => clearTimeout(t);
  }, [q, version, toast]);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      await notesApi.add({ text: text.trim() });
      setText("");
      setQ("");
      setVersion((v) => v + 1);
    } catch (err) {
      toast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (n: Note) => {
    try {
      await notesApi.remove(n.id);
      setVersion((v) => v + 1);
    } catch (err) {
      toast((err as Error).message, "error");
    }
  };

  return (
    <div className="page scroll">
      <div className="page-inner">
        <div className="page-header sr-only">
          <h2>Заметки</h2>
        </div>
        <p className="hint">
          Мысли и ссылки. Перешлите боту любое сообщение — оно сохранится сюда. Найти потом можно и в чате: «что я сохранял про рекламу?»
        </p>
        <form className="surface add-form" onSubmit={add}>
          <label className="sr-only" htmlFor="note-text">
            Новая заметка
          </label>
          <input id="note-text" className="input grow" placeholder="Идея или ссылка" value={text} onChange={(e) => setText(e.target.value)} maxLength={4000} />
          <button type="submit" className="btn btn-solid" disabled={!text.trim() || busy}>
            <Plus size={16} aria-hidden="true" /> Сохранить
          </button>
        </form>
        <div className="surface add-form">
          <Search size={16} aria-hidden="true" style={{ color: "var(--muted)", flexShrink: 0 }} />
          <label className="sr-only" htmlFor="note-q">
            Поиск по заметкам
          </label>
          <input id="note-q" className="input grow" placeholder="Поиск" value={q} onChange={(e) => setQ(e.target.value)} type="search" />
        </div>
        <ul className="surface list">
          {notes === null && <li className="list-empty">Загрузка…</li>}
          {notes?.length === 0 && <li className="list-empty">{q ? "Ничего не нашлось." : "Пока пусто."}</li>}
          {notes?.map((n) => (
            <li key={n.id} className="list-item">
              {n.url ? (
                <Link2 size={18} aria-hidden="true" style={{ color: "var(--accent)", flexShrink: 0 }} />
              ) : (
                <Lightbulb size={18} aria-hidden="true" style={{ color: "var(--accent)", flexShrink: 0 }} />
              )}
              <div className="main-col">
                {n.title && <div className="title">{n.title}</div>}
                {n.text && <div className={n.title ? "meta note-text" : "title note-text"}>{n.text}</div>}
                <div className="meta">
                  {when(n.created_at)}
                  {SOURCE[n.source] ? ` · ${SOURCE[n.source]}` : ""}
                  {n.tags.length > 0 && ` · ${n.tags.map((t) => `#${t}`).join(" ")}`}
                  {n.url && (
                    <>
                      {" · "}
                      <a href={n.url} target="_blank" rel="noreferrer noopener">
                        {new URL(n.url).hostname.replace(/^www\./, "")} <ExternalLink size={11} aria-hidden="true" />
                      </a>
                    </>
                  )}
                </div>
              </div>
              <button type="button" className="icon-btn plain" onClick={() => remove(n)} aria-label="Удалить заметку">
                <Trash2 size={15} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export default NotesPage;
