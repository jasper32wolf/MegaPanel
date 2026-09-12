/**
 * Lead Hub + auth login burst.
 * Requires seeded demo user: admin@demo.local / DemoPass123!
 *
 *   k6 run -e BASE=http://localhost:8000 infra/k6/leads.js
 */
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: 3,
  duration: "20s",
  thresholds: {
    http_req_failed: ["rate<0.1"],
    http_req_duration: ["p(95)<1200"],
  },
};

const BASE = __ENV.BASE || "http://localhost:8000";

export function setup() {
  const res = http.post(
    `${BASE}/api/v1/auth/login`,
    JSON.stringify({ email: "admin@demo.local", password: "DemoPass123!" }),
    { headers: { "Content-Type": "application/json" } },
  );
  if (res.status !== 200) {
    return { token: null };
  }
  return { token: res.json("access_token") };
}

export default function (data) {
  if (!data.token) {
    sleep(1);
    return;
  }
  const inbox = http.get(`${BASE}/api/v1/leads/inbox`, {
    headers: { Authorization: `Bearer ${data.token}` },
  });
  check(inbox, { "inbox ok": (r) => r.status === 200 });

  const summary = http.get(`${BASE}/api/v1/panel/reports/summary`, {
    headers: { Authorization: `Bearer ${data.token}` },
  });
  check(summary, { "summary ok": (r) => r.status === 200 });
  sleep(0.5);
}
