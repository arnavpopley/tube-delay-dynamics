const DEFAULT_ORACLE_STATUS = "http://132.145.52.100:8080/status";

export default async function handler(req, res) {
  const url = process.env.COLLECTOR_STATUS_URL || DEFAULT_ORACLE_STATUS;
  res.setHeader("Cache-Control", "s-maxage=10, stale-while-revalidate=30");
  res.setHeader("Access-Control-Allow-Origin", "*");

  try {
    const upstream = await fetch(url, { signal: AbortSignal.timeout(8000) });
    const payload = await upstream.json();
    res.status(200).json({
      reachable: upstream.ok,
      source: "oracle",
      ...payload,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : "collector unreachable";
    res.status(200).json({
      reachable: false,
      healthy: false,
      source: "oracle",
      error: message,
      hint: "Timeout means the Oracle VCN or iptables is still dropping TCP 8080. Collection on the VM can still be running.",
    });
  }
}
