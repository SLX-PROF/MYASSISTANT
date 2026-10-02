import { useEffect, useState } from "react";
import { api, setCsrf } from "../lib/api";
import { setTimezone } from "../lib/format";
import { useStore } from "../lib/store";
import { Circuit, Orb, type OrbState } from "../components/Orb";

export function LoginPage() {
  const { setMe, me } = useStore();
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [state, setState] = useState<OrbState>("idle");

  // After logout/expiry we may not know yet whether a second factor is required.
  useEffect(() => {
    if (me?.totp_required === undefined) {
      api.me().then((m) => !m.authenticated && setMe(m)).catch(() => undefined);
    }
  }, [me?.totp_required, setMe]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password) return;
    setState("thinking");
    setError("");
    try {
      const res = await api.login(password, me?.totp_required ? code : undefined);
      setCsrf(res.csrf_token);
      setTimezone(res.timezone);
      setMe({ ...res, authenticated: true });
    } catch (err) {
      setError((err as Error).message);
      setState("idle");
    }
  };

  return (
    <>
      <Circuit />
      <main className="login">
        <div className="login-card">
          <Orb state={state} />
          <h1>{(me?.assistant_name ?? "Атлас").toUpperCase()}</h1>
          <p className="hint">Личный ассистент. Войдите, чтобы продолжить.</p>
          <form onSubmit={submit}>
            <label htmlFor="password" className="sr-only">
              Пароль
            </label>
            <input
              id="password"
              className="input"
              type="password"
              autoComplete="current-password"
              placeholder="Пароль"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoFocus
              aria-invalid={!!error}
              aria-describedby="login-error"
            />
            {me?.totp_required && (
              <>
                <label htmlFor="totp" className="sr-only">
                  Код из приложения-аутентификатора
                </label>
                <input
                  id="totp"
                  className="input"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  pattern="[0-9]*"
                  maxLength={6}
                  placeholder="Код из приложения"
                  value={code}
                  onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                />
              </>
            )}
            <button
              type="submit"
              className="btn btn-solid"
              disabled={!password || (me?.totp_required && code.length !== 6) || state === "thinking"}
            >
              {state === "thinking" ? "Проверяю…" : "Войти"}
            </button>
            <p id="login-error" className="form-error" role="alert">
              {error}
            </p>
          </form>
        </div>
      </main>
    </>
  );
}
