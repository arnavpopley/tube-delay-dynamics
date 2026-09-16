const HEARTBEAT_API =
  process.env.COLLECTOR_HEARTBEAT_URL ||
  "https://api.github.com/repos/arnavpopley/tube-delay-dynamics-heartbeat/contents/status.json";
const DEFAULT_ORACLE_STATUS = "http://132.145.52.100:8080/status";

async function readHeartbeat(timeoutMs) {
  const upstream = await fetch(HEARTBEAT_API, {
    signal: AbortSignal.timeout(timeoutMs),
    cache: "no-store",
    headers: {
      Accept: "application/vnd.github.raw+json",
      "User-Agent": "tube-delay-dynamics-dashboard",
    },
  });
  if (!upstream.ok) {
    throw new Error(`heartbeat HTTP ${upstream.status}`);
  }
  const payload = await upstream.json();
  if (payload && typeof payload.content === "string" && payload.encoding === "base64") {
    return JSON.parse(Buffer.from(payload.content, "base64").toString("utf8"));
  }
  return payload;
}

async function readJson(url, timeoutMs) {
  const upstream = await fetch(url, {
    signal: AbortSignal.timeout(timeoutMs),
    cache: "no-store",
    headers: { Accept: "application/json", "Cache-Control": "no-cache" },
  });
  if (!upstream.ok) {
    throw new Error(`${url} HTTP ${upstream.status}`);
  }
  return upstream.json();
}

function withAge(payload, source) {
  const last = payload?.last_success_ts;
  const parsed = last ? Date.parse(last) : NaN;
  const age = Number.isFinite(parsed) ? (Date.now() - parsed) / 1000 : null;
  const staleAfter = Number(payload?.stale_after_seconds) || 600;
  const healthy = age != null && age <= staleAfter;
  return {
    reachable: healthy || Boolean(payload?.last_success_ts) || Boolean(payload?.healthy),
    source,
    ...payload,
    seconds_since_success: age,
    healthy,
  };
}

export default async function handler(req, res) {
  res.setHeader("Cache-Control", "s-maxage=20, stale-while-revalidate=60");
  res.setHeader("Access-Control-Allow-Origin", "*");

  try {
    const payload = await readHeartbeat(8000);
    const body = withAge(payload, "github-heartbeat");
    if (payload?.last_success_ts || payload?.healthy) {
      res.status(200).json(body);
      return;
    }
  } catch (err) {
    // Fall through to the Oracle :8080 proxy; that path needs inbound TCP.
  }

  const oracleUrl = process.env.COLLECTOR_STATUS_URL || DEFAULT_ORACLE_STATUS;
  try {
    const payload = await readJson(oracleUrl, 4000);
    res.status(200).json(withAge(payload, "oracle"));
  } catch (err) {
    const message = err instanceof Error ? err.message : "collector unreachable";
    res.status(200).json({
      reachable: false,
      healthy: false,
      source: "github-heartbeat",
      error: message,
      hint: "Waiting for the Oracle collector to publish its outbound heartbeat. On the VM: git pull && sudo collector/deploy/install-systemd.sh",
    });
  }
}
