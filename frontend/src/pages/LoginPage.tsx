import { useState } from "react";
import { api, setCsrf } from "../lib/api";
import { setTimezone } from "../lib/format";
import { useStore } from "../lib/store";
import { Circuit, Orb, type OrbState } from "../components/Orb";

export function LoginPage() {
  const { setMe, me } = useStore();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [state, setState] = useState<OrbState>("idle");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!password) return;
    setState("thinking");
    setError("");
    try {
      const res = await api.login(password);
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
          <h1>{(me?.assistant_name ?? "Jarvis").toUpperCase()}</h1>
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
            <button type="submit" className="btn btn-solid" disabled={!password || state === "thinking"}>
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
