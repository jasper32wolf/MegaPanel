import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, useAuth } from "../lib/auth";

type TokenResponse = { access_token: string; refresh_token: string };

export function LoginPage() {
  const { setToken } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("admin@demo.local");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const data = await api<TokenResponse>("/api/v1/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
      });
      setToken(data.access_token);
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
        <p>Мультипанель массовой генерации сайтов услуг — SEO, drip и лиды в одном контуре.</p>
      </section>
      <div className="login-panel">
        <form className="login-box" onSubmit={onSubmit}>
          <h2>Вход</h2>
          <p className="muted" style={{ marginTop: 0, marginBottom: "1.25rem" }}>
            Используйте учётную запись tenant или demo.
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
          {error && <p className="error">{error}</p>}
          <button className="btn" type="submit" disabled={loading}>
            {loading ? "Вход…" : "Войти"}
          </button>
        </form>
      </div>
    </div>
  );
}
