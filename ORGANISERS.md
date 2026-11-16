# Running the event: organiser API

Everything organisers do goes through `/api/v1/admin/*` with the organiser token. Until
an organiser screen exists, these `curl` commands are the controls. The token is
`ADMIN_TOKEN` in the server's `.env`; with it unset, the organiser API doesn't exist.

```bash
export PX=http://127.0.0.1:8000/api/v1/admin      # the event server's address on the day
export AUTH="Authorization: Bearer $ADMIN_TOKEN"
```

Never paste the token into a participant-facing screen or a shared chat.

## Phases

```bash
curl -s -H "$AUTH" $PX/phases                                  # all phases, time left
curl -s -H "$AUTH" -X POST $PX/phases/solo/start               # start (ends the current one)
curl -s -H "$AUTH" -X POST $PX/phases/current/pause            # freeze the clock, close interviews
curl -s -H "$AUTH" -X POST $PX/phases/current/resume
curl -s -H "$AUTH" -X POST $PX/phases/current/extend -H 'Content-Type: application/json' -d '{"seconds": 300}'
curl -s -H "$AUTH" -X POST $PX/phases/current/end
```

A timed phase closes interviews by itself when its time runs out; it stays listed as
running until you end it or start the next one. Phases with no timer (`planned_seconds`
0) run until you end them.

## Watching

```bash
curl -s -H "$AUTH" $PX/overview        # phase, questions asked, deflections, lockouts, spend, provider errors
curl -s -H "$AUTH" $PX/teams           # per team: trust, tier, strikes, lockouts, quota, cost
curl -s -H "$AUTH" $PX/teams/A01/threads/cfo   # a thread, with what the backend decided per question
curl -s -H "$AUTH" $PX/costs           # spend by model and purpose, last 10 provider errors
curl -s -H "$AUTH" $PX/live            # how many tabs are connected for live updates
```

Each question in a thread carries `meta`: the classifier's label and topics, the trust
change, strikes, the evidence the stakeholder was given, any failed checks, and whether
the answer was a deflection. That's the record to use when a team disputes an outcome.

## Fixing

```bash
# one team, one stakeholder: set trust, give questions back, or lift an HR lockout
curl -s -H "$AUTH" -X PATCH $PX/teams/A01/stakeholders/cfo -H 'Content-Type: application/json' \
  -d '{"trust": 50, "quota": 12, "clear_lockout": true}'

# every team: raise the question quota (never below questions already used)
curl -s -H "$AUTH" -X POST $PX/quota -H 'Content-Type: application/json' -d '{"quota": 12}'
```

## Live settings

```bash
curl -s -H "$AUTH" $PX/config
curl -s -H "$AUTH" -X PATCH $PX/config -H 'Content-Type: application/json' \
  -d '{"hr": {"lockout_seconds": 180}, "tier_thresholds": {"neutral": 35, "open": 65}}'
```

Changeable live: `tier_thresholds`, `trust_rules`, `hr`, `evidence_token_budget`,
`max_passages`, `models`. `trust_start` and `default_quota` apply to teams created
afterwards. Anything else is refused.

## Content

```bash
curl -s -H "$AUTH" -X POST $PX/content/reload -H 'Content-Type: application/json' \
  -d '{"path": "content/case.toml"}'
```

The file must be a `.toml` under `content/` (git-ignored) or `tests/fixtures/`. It's
validated first; if anything is wrong, nothing changes and the response lists the
problems. New stakeholders get state rows for every team. Teams' threads, trust and
evidence are kept.
