# Running the event: organiser API

Everything organisers do goes through `/api/v1/admin/*` with the organiser token. Until
an organiser screen exists, these `curl` commands are the controls. The token is
`ADMIN_TOKEN` in the server's `.env`; with it unset, the organiser API doesn't exist.

```bash
export PX=http://127.0.0.1:8000/api/v1/admin      # the event server's address on the day
export AUTH="Authorization: Bearer $ADMIN_TOKEN"
```

Never paste the token into a participant-facing screen or a shared chat.

**In a browser instead:** open `/docs` on the server (locally <http://127.0.0.1:8000/docs>),
click **Authorize**, paste the token, then use any endpoint under `organisers` with
"Try it out". The token stays in that tab until you close it or log out in the dialog.

## Before the event: teams

```bash
# register teams (codes T001, T002, ...); access codes are shown ONCE: print the cards now
curl -s -H "$AUTH" -X POST $PX/teams -H 'Content-Type: application/json' \
  -d '{"names": ["Team Alpha", "Team Beta", "Team Gamma"]}'

# deal every team without a company into the companies, balanced (counts differ by at most 1)
curl -s -H "$AUTH" -X POST $PX/teams/assign-companies -H 'Content-Type: application/json' -d '{"seed": 2026}'

# move one team by hand (refused once it has interviewed anyone)
curl -s -H "$AUTH" -X PUT $PX/teams/T004/company -H 'Content-Type: application/json' -d '{"company": "B"}'

# a team lost its card: new code, and its laptops are logged out
curl -s -H "$AUTH" -X POST $PX/teams/T004/access-code
```

`assign-companies` with `"only_unassigned": false` re-deals every team that hasn't asked
a question yet. The same `seed` gives the same draw, so you can rerun it in front of the
room; leave it out for a fresh random one.

## Rounds

Rounds are phases. The case file seeds the first set; add as many more as you need.

```bash
curl -s -H "$AUTH" -X POST $PX/phases -H 'Content-Type: application/json' \
  -d '{"key": "round3", "name": "Round 3", "planned_seconds": 1800, "capabilities": {"interview": true}}'
curl -s -H "$AUTH" -X POST $PX/phases -H 'Content-Type: application/json' \
  -d '{"key": "lunch", "name": "Lunch", "capabilities": {"interview": false}, "after": "solo"}'
curl -s -H "$AUTH" -X PATCH $PX/phases/round3 -H 'Content-Type: application/json' -d '{"name": "Semi-final", "planned_seconds": 2400}'
curl -s -H "$AUTH" -X PUT $PX/phases/order -H 'Content-Type: application/json' -d '{"keys": ["briefing", "solo", ...every round...]}'
curl -s -H "$AUTH" -X DELETE $PX/phases/lunch      # only a round that never started
```

Keys are lowercase letters, digits and `_`. A round can't be deleted once it has started,
or while the case unlocks evidence in it, or while documents/statuses point at it.
Evidence the case unlocks "from" a round stays unlocked in every round after it, including
rounds you add later.

## Documents

```bash
# a note for everyone, visible now
curl -s -H "$AUTH" -X POST $PX/documents -H 'Content-Type: application/json' \
  -d '{"title": "Round 2 rules", "body": "# Round 2\n...", "release_now": true}'
# a file for one company, released automatically when round3 starts
curl -s -H "$AUTH" -X POST $PX/documents -H 'Content-Type: application/json' \
  -d "{\"title\": \"Board pack\", \"company\": \"A\", \"round\": \"round3\", \"file\": {\"filename\": \"pack.pdf\", \"content_type\": \"application/pdf\", \"data_base64\": \"$(base64 -w0 pack.pdf)\"}}"
# named teams only
curl -s -H "$AUTH" -X POST $PX/documents -H 'Content-Type: application/json' \
  -d '{"title": "Your feedback", "body": "...", "teams": ["T004", "T009"], "release_now": true}'
curl -s -H "$AUTH" $PX/documents
curl -s -H "$AUTH" -X PATCH $PX/documents/<id> -H 'Content-Type: application/json' -d '{"released": true}'
curl -s -H "$AUTH" -X DELETE $PX/documents/<id>
```

Files up to 15 MB. A document is visible once released by hand, or when its round starts.
Teams can't tell a document meant for someone else from one that doesn't exist.

## Round results

```bash
# draft each team's status for a round (teams see nothing yet)
curl -s -H "$AUTH" -X PUT $PX/phases/round2/statuses -H 'Content-Type: application/json' \
  -d '{"entries": [{"team": "T001", "status": "advancing", "message": "See you in round 2."},
                   {"team": "T002", "status": "eliminated", "message": "Thank you for playing."}]}'
curl -s -H "$AUTH" $PX/phases/round2/statuses               # every team, drafted or not
curl -s -H "$AUTH" -X POST $PX/phases/round2/statuses/publish   # everyone sees theirs at once
```

Statuses: `advancing`, `eliminated`, `waitlisted`, `pending`. Add `"publish": true` to the
PUT to publish straight away. A published `eliminated` closes interviews for that team in
that round.

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
curl -s -H "$AUTH" $PX/teams           # per team: trust, HR per department, quota, clues, time back, cost
curl -s -H "$AUTH" $PX/teams/A01/threads/cfo   # a thread, with what the backend decided per question
curl -s -H "$AUTH" $PX/teams/A01/clues # which of its company's clues the team has found
curl -s -H "$AUTH" "$PX/alerts?since=2026-11-19T10:00:00Z"   # prompt attacks, hostility, blocked drafts
curl -s -H "$AUTH" "$PX/hr-flags?team=A01"                   # every HR strike, warning, lockout
curl -s -H "$AUTH" $PX/costs           # spend by model and purpose, last 10 provider errors
curl -s -H "$AUTH" $PX/live            # how many tabs are connected for live updates
```

Each question in a thread carries `meta`: the classifier's label and topics, the trust
change, strikes, the evidence the stakeholder was given, any failed checks, and whether
the answer was a deflection. That's the record to use when a team disputes an outcome.

**Alerts** are the live log of prompt-hacking attempts: poll with `since` set to the last
`at` you saw. A question is listed when the classifier called it `prompt_attack` or
`hostile`, or when the checks blocked a draft answer (the model tried to say something it
mustn't). Blocked drafts never reached the team.

**HR is per department.** A hostile or prompt-attack question is a strike against the
department of the person asked. The second strike gets a warning, the third a complaint:
nobody in that department talks to the team for 10 minutes (real time, it keeps running
through a pause). Every strike is kept in `hr-flags` for interview-efficiency scoring,
even after the lockout ends and the department's count resets.

## Fixing

```bash
# one team, one stakeholder: set trust, give questions back, or lift an HR lockout
# (clear_lockout lifts it for the stakeholder's whole department and resets its strikes)
curl -s -H "$AUTH" -X PATCH $PX/teams/A01/stakeholders/cfo -H 'Content-Type: application/json' \
  -d '{"trust": 50, "quota": 12, "clear_lockout": true}'

# every team: raise the question quota (never below questions already used)
curl -s -H "$AUTH" -X POST $PX/quota -H 'Content-Type: application/json' -d '{"quota": 12}'

# one team's laptop crashed: give it 3 minutes back in the current phase (negative takes back)
curl -s -H "$AUTH" -X POST $PX/teams/A01/time -H 'Content-Type: application/json' -d '{"seconds": 180}'
```

Time back moves only that team's countdown and interview gate; the phase and every other
team stay as they are. If you end the phase or start the next one, the time is gone.

## Announcements

```bash
curl -s -H "$AUTH" -X POST $PX/announcements -H 'Content-Type: application/json' \
  -d '{"text": "Alliance window opens in 5 minutes."}'
curl -s -H "$AUTH" -X POST $PX/announcements -H 'Content-Type: application/json' \
  -d '{"text": "Northfield teams: room 2.", "company": "A"}'   # one company only
curl -s -H "$AUTH" $PX/announcements                           # all of them, with ids
curl -s -H "$AUTH" -X DELETE $PX/announcements/<id>            # retract
```

Teams' screens update at once. Broadcasts to everyone also appear on the projector page
(`GET /api/v1/display`, no login), with the phase and clock.

## Live settings

```bash
curl -s -H "$AUTH" $PX/config
curl -s -H "$AUTH" -X PATCH $PX/config -H 'Content-Type: application/json' \
  -d '{"hr": {"lockout_seconds": 600}, "tier_thresholds": {"neutral": 35, "open": 65}}'
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
problems (including a brief that would give away a passage's fingerprint). New stakeholders get state rows for every team. Teams' threads, trust and
evidence are kept.
