import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, useAuth } from "../lib/auth";

type LoginResponse = { ok: boolean };

export function LoginPage() {
  const { markAuthenticated } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await api<LoginResponse>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password, ...(totpCode ? { totp_code: totpCode } : {}) }),
      });
      markAuthenticated();
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-wrap">
      <section className="login-hero" aria-label="Site Panel">
        <h1 className="brand">Site Panel</h1>
        <p>Панель одного оператора для создания, публикации и сопровождения сайтов услуг.</p>
      </section>
      <div className="login-panel">
        <form className="login-box" onSubmit={onSubmit}>
          <h2>Вход</h2>
          <p className="muted" style={{ marginTop: 0, marginBottom: "1.25rem" }}>
            Войдите с учётной записью оператора.
          </p>
          <label className="field">
            Email
            <input value={email} onChange={(e) => setEmail(e.target.value)} type="email" required autoComplete="username" />
          </label>
          <label className="field">
            Пароль
            <input
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              type="password"
              required
              minLength={10}
              autoComplete="current-password"
            />
          </label>
          <label className="field">
            Код из приложения-аутентификатора <span className="muted">(если включён TOTP)</span>
            <input
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, "").slice(0, 8))}
              inputMode="numeric"
              autoComplete="one-time-code"
            />
          </label>
          {error && <p className="error">{error}</p>}
          <button className="btn" type="submit" disabled={loading}>
            {loading ? "Вход…" : "Войти"}
          </button>
        </form>
      </div>
    </div>
  );
}
