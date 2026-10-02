import { useEffect, useState } from "react";
import { Bell, Brain, Download, LogOut, Monitor, Moon, Sun } from "lucide-react";
import { api } from "../lib/api";
import { getTimezone } from "../lib/format";
import { useStore } from "../lib/store";
import type { UISettings } from "../lib/types";

const ACCENTS = [
  ["#22d3ee", "Циан"],
  ["#3b82f6", "Синий"],
  ["#a855f7", "Фиолетовый"],
  ["#f59e0b", "Янтарный"],
  ["#10b981", "Изумрудный"],
] as const;

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
}
let deferredInstall: BeforeInstallPromptEvent | null = null;
window.addEventListener("beforeinstallprompt", (e) => {
  e.preventDefault();
  deferredInstall = e as BeforeInstallPromptEvent;
});

export function SettingsPage() {
  const { ui, setUi, me, setMe, navigate, toast } = useStore();
  const [perm, setPerm] = useState<NotificationPermission | "unsupported">(
    "Notification" in window ? Notification.permission : "unsupported",
  );
  const [canInstall, setCanInstall] = useState(!!deferredInstall);
  useEffect(() => {
    const h = () => setCanInstall(true);
    window.addEventListener("beforeinstallprompt", h);
    return () => window.removeEventListener("beforeinstallprompt", h);
  }, []);

  const update = (patch: Partial<UISettings>) => setUi({ ...ui, ...patch });

  const askNotifications = async () => {
    if (!("Notification" in window)) return;
    const p = await Notification.requestPermission();
    setPerm(p);
    if (p === "granted") toast("Уведомления включены");
  };

  const logout = async () => {
    try {
      await api.logout();
    } finally {
      setMe({ authenticated: false, assistant_name: me?.assistant_name ?? "Атлас" });
      navigate({ name: "chat", id: null }, true);
    }
  };

  return (
    <div className="page scroll">
      <div className="page-inner">
        <div className="page-header sr-only">
          <h2>Настройки</h2>
        </div>

        <section className="surface settings-group" aria-labelledby="s-look">
          <h3 id="s-look">Оформление</h3>
          <div className="field">
            <span className="label" id="theme-label">
              Тема
            </span>
            <div className="segmented" role="group" aria-labelledby="theme-label">
              {(
                [
                  ["dark", "Тёмная", <Moon key="m" size={14} aria-hidden="true" />],
                  ["light", "Светлая", <Sun key="s" size={14} aria-hidden="true" />],
                  ["system", "Как в системе", <Monitor key="c" size={14} aria-hidden="true" />],
                ] as const
              ).map(([v, l, icon]) => (
                <button key={v} type="button" aria-pressed={ui.theme === v} onClick={() => update({ theme: v })} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                  {icon}
                  {l}
                </button>
              ))}
            </div>
          </div>
          <div className="field">
            <span className="label" id="accent-label">
              Акцентный цвет
            </span>
            <div className="swatches" role="group" aria-labelledby="accent-label">
              {ACCENTS.map(([c, name]) => (
                <button key={c} type="button" className="swatch" aria-pressed={ui.accent === c} aria-label={name} onClick={() => update({ accent: c })} style={{ background: c }} />
              ))}
              <label className="btn btn-sm" style={{ position: "relative" }}>
                Свой…
                <input
                  type="color"
                  value={ui.accent}
                  onChange={(e) => update({ accent: e.target.value })}
                  style={{ position: "absolute", inset: 0, opacity: 0, cursor: "pointer" }}
                  aria-label="Выбрать свой цвет"
                />
              </label>
            </div>
          </div>
        </section>

        <section className="surface settings-group" aria-labelledby="s-notify">
          <h3 id="s-notify">Уведомления и приложение</h3>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button type="button" className="btn" onClick={askNotifications} disabled={perm !== "default"}>
              <Bell size={16} aria-hidden="true" />
              {perm === "granted" ? "Уведомления включены" : perm === "denied" ? "Уведомления запрещены в браузере" : perm === "unsupported" ? "Браузер не поддерживает" : "Включить уведомления"}
            </button>
            {canInstall && (
              <button
                type="button"
                className="btn"
                onClick={async () => {
                  await deferredInstall?.prompt();
                  deferredInstall = null;
                  setCanInstall(false);
                }}
              >
                <Download size={16} aria-hidden="true" /> Установить приложение
              </button>
            )}
          </div>
          <p className="hint">
            На iPhone: откройте Атлас в Safari → «Поделиться» → «На экран Домой». Уведомления о напоминаниях приходят, пока открыта вкладка или приложение.
          </p>
        </section>

        <section className="surface settings-group" aria-labelledby="s-about">
          <h3 id="s-about">Система</h3>
          <p className="hint">
            Часовой пояс: <b>{getTimezone()}</b> · Модель: <b>{me?.llm_provider === "fake" ? "демо-режим" : me?.llm_provider}</b>
          </p>
          {me?.llm_warning && <p className="hint" style={{ color: "var(--warning)" }}>{me.llm_warning}</p>}
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <button type="button" className="btn mobile-only" onClick={() => navigate({ name: "memory" })}>
              <Brain size={16} aria-hidden="true" /> Память
            </button>
            <button type="button" className="btn btn-danger" onClick={logout}>
              <LogOut size={16} aria-hidden="true" /> Выйти
            </button>
          </div>
        </section>
      </div>
    </div>
  );
}
