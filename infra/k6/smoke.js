/**
 * k6 load smoke for Site Panel (TZ 13).
 *
 *   k6 run infra/k6/smoke.js
 *   k6 run -e BASE=http://localhost:8000 infra/k6/smoke.js
 */
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  scenarios: {
    api_smoke: {
      executor: "constant-vus",
      vus: 5,
      duration: "30s",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.05"],
    http_req_duration: ["p(95)<800"],
  },
};

const BASE = __ENV.BASE || "http://localhost:8000";

export default function () {
  const health = http.get(`${BASE}/api/v1/health`);
  check(health, { "health 200": (r) => r.status === 200 });

  const metrics = http.get(`${BASE}/api/v1/metrics`);
  check(metrics, { "metrics 200": (r) => r.status === 200 });

  const security = http.get(`${BASE}/.well-known/security.txt`);
  check(security, { "security.txt 200": (r) => r.status === 200 });

  // Public lead without consent should 400
  const lead = http.post(
    `${BASE}/api/v1/leads/public`,
    JSON.stringify({
      site_id: "00000000-0000-0000-0000-000000000001",
      phone: "+79991234567",
      consent: false,
      form_ts: Date.now() / 1000 - 5,
    }),
    { headers: { "Content-Type": "application/json" } },
  );
  check(lead, { "lead no-consent rejected": (r) => r.status === 400 || r.status === 404 });

  sleep(0.3);
}
