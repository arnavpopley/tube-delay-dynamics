# Public heartbeat (dashboard only)

The Oracle VM cannot accept inbound TCP 8080 from Vercel (VCN / iptables).
Instead the collector **pushes** `status.json` to
https://github.com/arnavpopley/tube-delay-dynamics-heartbeat using the
write-only deploy key in this directory (rebuilt at install from
`heartbeat_key.a` + `heartbeat_key.b`; GitHub push protection blocks
committing the PEM file).

That key can overwrite the public heartbeat file. It cannot read
`data/raw/`, and it cannot push to the research repo. Heartbeat JSON has
no prediction rows.

`HEARTBEAT_PUSH=0` in `.env` disables the push. Do not delete the key
unless you replace the GitHub deploy key at the same time.
