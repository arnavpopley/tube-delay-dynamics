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

export default function App() {
  const [lines, setLines] = useState<LineStatus[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [fetchedAt, setFetchedAt] = useState<string | null>(null)

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
        <div className="banner">
          <strong>This page is not the collector.</strong> Vercel and GitHub
          Pages are static hosts: they sleep, they have no durable disk, and
          they cannot poll TfL every 30 seconds. The research log has to run on
          a machine that stays on — Oracle Always Free or a cheap VPS. See
          <code> collector/deploy/DEPLOY.md</code>.
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
          <p>
            Predictions are logged point-in-time with our clock. Raw files are
            append-only. Failed polls are written as records. None of that can
            live in a serverless function.
          </p>
        </section>

        <section>
          <h2>Live TfL board</h2>
          <p className="meta">
            Browser fetch of TfL’s public line status. This is <em>their</em>{' '}
            feed right now — not our 30-second archive, and not a prediction
            we stored.
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

      <section style={{ marginTop: '1.25rem' }}>
        <h2>Where collection actually runs</h2>
        <p>
          Free 24/7 option: an Oracle Cloud Always Free ARM VM (home region,
          ~50GB boot volume). Not Vercel, not this Cursor agent, not GitHub
          Actions.
        </p>
        <pre className="cmd">{`# on the always-on VM
git clone https://github.com/<you>/tube-delay-dynamics.git
cd tube-delay-dynamics
cp .env.example .env   # TFL_APP_KEY=...
python3 collector/tfl_collector.py`}</pre>
      </section>

      <footer>
        Standing brief is PROJECT.md. Reconstruction is not started until a
        full day of uninterrupted data exists.
      </footer>
    </div>
  )
}
