import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { AlarmClock, Brain, ListChecks, Plus, SendHorizontal } from "lucide-react";

export interface ComposerHandle {
  insert: (text: string) => void;
  focus: () => void;
}

interface Props {
  busy: boolean;
  onSend: (text: string) => void;
  placeholder?: string;
}

const CHIPS: { label: string; icon: React.ReactNode; insert?: string; send?: string }[] = [
  { label: "Напоминание", icon: <Plus size={14} aria-hidden="true" />, insert: "Напомни " },
  { label: "Мои задачи", icon: <ListChecks size={14} aria-hidden="true" />, send: "Покажи мои задачи" },
  { label: "Напоминания", icon: <AlarmClock size={14} aria-hidden="true" />, send: "Какие у меня напоминания?" },
  { label: "Запомнить", icon: <Brain size={14} aria-hidden="true" />, insert: "Запомни, что " },
];

export const Composer = forwardRef<ComposerHandle, Props>(function Composer({ busy, onSend, placeholder }, ref) {
  const [text, setText] = useState("");
  const area = useRef<HTMLTextAreaElement>(null);

  useImperativeHandle(ref, () => ({
    insert: (t: string) => {
      setText(t);
      requestAnimationFrame(() => {
        const el = area.current;
        if (el) {
          el.focus();
          el.setSelectionRange(t.length, t.length);
        }
      });
    },
    focus: () => area.current?.focus(),
  }));

  // Auto-grow up to max-height (CSS).
  useEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  const submit = () => {
    const t = text.trim();
    if (!t || busy) return;
    onSend(t);
    setText("");
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  };

  const setKeyboard = (open: boolean) => {
    if (matchMedia("(max-width: 899px)").matches) document.body.classList.toggle("keyboard-open", open);
  };

  return (
    <div className="composer">
      <div className="composer-inner">
        <div className="chips" role="toolbar" aria-label="Быстрые действия">
          {CHIPS.map((c) => (
            <button
              key={c.label}
              type="button"
              className="chip"
              disabled={busy && !!c.send}
              onClick={() => (c.send ? onSend(c.send) : c.insert && (setText(c.insert), area.current?.focus()))}
            >
              {c.icon}
              {c.label}
            </button>
          ))}
        </div>
        <form
          className="composer-box"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <label htmlFor="composer-input" className="sr-only">
            Сообщение
          </label>
          <textarea
            id="composer-input"
            ref={area}
            rows={1}
            value={text}
            placeholder={placeholder ?? "Сообщение для Атласа…"}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKeyDown}
            onFocus={() => setKeyboard(true)}
            onBlur={() => setKeyboard(false)}
            enterKeyHint="send"
            autoComplete="off"
          />
          {/* Reserved for the voice button (stage "voice"). */}
          <span className="mic-slot" aria-hidden="true" />
          <button type="submit" className="send-btn" disabled={!text.trim() || busy} aria-label="Отправить">
            <SendHorizontal size={18} strokeWidth={2.4} aria-hidden="true" />
          </button>
        </form>
        <div className="composer-hint">Enter — отправить · Shift+Enter — новая строка</div>
      </div>
    </div>
  );
});
