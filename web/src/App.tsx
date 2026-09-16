import { useEffect, useState } from 'react'
import './index.css'

const TUBE_COLOURS: Record<string, string> = {
  bakerloo: '#B36305',
  central: '#E32017',
  circle: '#FFD300',
  district: '#00782A',
  'hammersmith-city': '#F3A9BB',
  jubilee: '#A0A5A9',
  metropolitan: '#9B0056',
  northern: '#000000',
  piccadilly: '#003688',
  victoria: '#0098D4',
  'waterloo-city': '#95CDBA',
}

type LineStatus = {
  id: string
  name: string
  lineStatuses: Array<{
    statusSeverity: number
    statusSeverityDescription: string
    reason?: string
  }>
}

type CollectorStatus = {
  reachable?: boolean
  healthy?: boolean
  last_success_ts?: string | null
  last_arrival_count?: number | null
  seconds_since_success?: number | null
  last_error?: string | null
  error?: string
  hint?: string
}

function londonTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleString('en-GB', {
    timeZone: 'Europe/London',
    day: 'numeric',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function ageLabel(seconds: number | null | undefined): string {
  if (seconds == null) return '—'
  if (seconds < 90) return `${Math.round(seconds)}s ago`
  return `${Math.round(seconds / 60)} min ago`
}

export default function App() {
  const [lines, setLines] = useState<LineStatus[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fetchedAt, setFetchedAt] = useState<string | null>(null)
  const [collector, setCollector] = useState<CollectorStatus | null>(null)

  useEffect(() => {
    const ac = new AbortController()
    fetch('https://api.tfl.gov.uk/Line/Mode/tube/Status', { signal: ac.signal })
      .then(async (res) => {
        if (!res.ok) throw new Error(`TfL HTTP ${res.status}`)
        return res.json() as Promise<LineStatus[]>
      })
      .then((data) => {
        setLines(data)
        setFetchedAt(new Date().toISOString())
      })
      .catch((err: unknown) => {
        if (err instanceof DOMException && err.name === 'AbortError') return
        setError(err instanceof Error ? err.message : 'TfL request failed')
      })
    return () => ac.abort()
  }, [])

  useEffect(() => {
    let cancelled = false
    const load = () => {
      fetch('/api/collector-status')
        .then(async (res) => {
          if (!res.ok) throw new Error(`status HTTP ${res.status}`)
          return res.json() as Promise<CollectorStatus>
        })
        .then((data) => {
          if (!cancelled) setCollector(data)
        })
        .catch((err: unknown) => {
          if (!cancelled) {
            setCollector({
              reachable: false,
              healthy: false,
              error: err instanceof Error ? err.message : 'status unreachable',
            })
          }
        })
    }
    load()
    const id = window.setInterval(load, 15000)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [])

  const collecting = Boolean(collector?.reachable && collector?.healthy)

  return (
    <div className="wrap">
      <header className="hero">
        <p className="kicker">London Underground · research dataset</p>
        <h1>Tube Delay Dynamics</h1>
        <p className="lede">
          Rank the eleven lines by how they absorb and recover from delay, then
          forecast the error in TfL’s own live arrivals — scored by horizon
          against their countdown board.
        </p>

        <div className={`status-card ${collecting ? 'is-ok' : 'is-down'}`}>
          <div className="status-top">
            <span className={`pill ${collecting ? 'ok' : 'down'}`}>
              {collecting ? 'Collecting' : 'Collector not reaching this page'}
            </span>
            <span className="meta">Oracle VM · refreshes every 15s</span>
          </div>
          <div className="stats">
            <div>
              <div className="stat-label">Last successful poll</div>
              <div className="stat-value">
                {londonTime(collector?.last_success_ts)}
              </div>
              <div className="meta">{ageLabel(collector?.seconds_since_success)}</div>
            </div>
            <div>
              <div className="stat-label">Predictions that poll</div>
              <div className="stat-value">
                {collector?.last_arrival_count ?? '—'}
              </div>
              <div className="meta">one row per train–station forecast</div>
            </div>
          </div>
          {collector?.error ? (
            <p className="err">
              {collector.error}
              {collector.hint ? ` ${collector.hint}` : ''}
            </p>
          ) : null}
          {!collecting ? (
            <pre className="cmd">{`cd /opt/tube-delay-dynamics && git pull
sudo collector/deploy/install-systemd.sh
curl -sS http://127.0.0.1:8080/status

# Oracle Cloud → tfl-collector → subnet → Security List
# AND the VNIC Network Security Group (if one is attached):
# Ingress  TCP  8080  source 0.0.0.0/0`}</pre>
          ) : null}
          <p className="meta">
            This card is our archive heartbeat, not TfL’s public board below.
            Raw JSONL never leaves the VM. A red card does not mean polling
            stopped — only that this page cannot see port 8080.
          </p>
        </div>
      </header>

      <div className="grid two">
        <section>
          <h2>What the dataset is for</h2>
          <ol className="outputs">
            <li>
              <strong>Line ranking.</strong> How fast each line self-regulates
              back to even headways, and how much downstream delay a shock
              creates. Expected punchline: the tightest-run lines are the most
              fragile.
            </li>
            <li>
              <strong>Forecasting study.</strong> Predict{' '}
              <code>actual − TfL promised</code> using headways, trains ahead,
              dwells. The deliverable is the horizon where TfL stops being
              unbeatable — not a claim of beating them everywhere.
            </li>
          </ol>
        </section>

        <section>
          <h2>Live TfL board</h2>
          <p className="meta">
            TfL’s public line status in your browser. Separate from the 30-second
            archive on Oracle.
          </p>
          {error ? <p className="err">{error}</p> : null}
          {!error && !lines ? <p className="empty">Loading line status…</p> : null}
          {lines ? (
            <>
              <p className="meta">Fetched {fetchedAt}</p>
              <ul className="lines">
                {lines.map((line) => {
                  const st = line.lineStatuses[0]
                  const ok = st?.statusSeverity === 10
                  return (
                    <li key={line.id}>
                      <span
                        className="swatch"
                        style={{ background: TUBE_COLOURS[line.id] ?? '#888' }}
                      />
                      <div>
                        <div className="status-name">{line.name}</div>
                        {st?.reason ? (
                          <div className="status-desc">{st.reason}</div>
                        ) : null}
                      </div>
                      <span className={ok ? 'good' : 'bad'}>
                        {st?.statusSeverityDescription ?? 'Unknown'}
                      </span>
                    </li>
                  )
                })}
              </ul>
            </>
          ) : null}
        </section>
      </div>

      <footer>
        Standing brief is PROJECT.md. Reconstruction waits until a full day of
        uninterrupted data exists.
      </footer>
    </div>
  )
}
