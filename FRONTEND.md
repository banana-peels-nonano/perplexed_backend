# Notes for the frontend

The frontend now lives in this repo under `web/` and is served by the backend (see
README). This file stays as the API contract it is built on.

What you need to know before building against this backend. The full, always-current
API reference is the OpenAPI page at `/docs` on a running server (`uvicorn app.main:app`,
then <http://127.0.0.1:8000/docs>). Every path below is under `/api/v1`.

## 1. Same origin, cookie sessions

- `POST /auth/login` with `{"access_code": "...", "device_label": "..."}` sets a session
  cookie (`pxd_session`): **HttpOnly, SameSite=Strict**.
- Serve the frontend and `/api` from **one domain**; we will proxy `/api` to the backend.
  On a different domain the cookie is never sent and login appears to do nothing.
- Use `fetch(url, { credentials: "include" })` on every call.
- At most **2 logged-in laptops per team**; a third login gets `SESSION_LIMIT`.
- `POST /auth/logout` ends this laptop's session. `GET /me` tells you who you are.

## 2. Errors

Every error has the same shape. Switch on `code`, show `message`:

```json
{
  "error": {
    "code": "QUOTA_EXHAUSTED",
    "message": "no questions left for this person",
    "details": {}
  }
}
```

| code                            | status | what to do                                                                                                                                                                                                                                    |
| ------------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `INVALID_CODE`                  | 401    | wrong access code                                                                                                                                                                                                                             |
| `SESSION_LIMIT`                 | 409    | team already has 2 laptops logged in                                                                                                                                                                                                          |
| `UNAUTHENTICATED`               | 401    | session missing or expired: back to login                                                                                                                                                                                                     |
| `COMPANY_NOT_ASSIGNED`          | 409    | team not yet assigned a company                                                                                                                                                                                                               |
| `PHASE_CLOSED`                  | 409    | interviews aren't open in this phase                                                                                                                                                                                                          |
| `QUOTA_EXHAUSTED`               | 409    | no questions left for this stakeholder                                                                                                                                                                                                        |
| `QUESTION_IN_FLIGHT`            | 409    | a teammate's question to this person is still being answered                                                                                                                                                                                  |
| `HR_LOCKOUT`                    | 409    | someone in this person's department filed an HR complaint over the team's conduct; the whole department is closed to the team. `details.retry_after_seconds` says when it reopens, `details.department` which one (the attempt isn't charged) |
| `PROVIDER_BUSY`                 | 503    | try again in a few seconds (the question was not charged)                                                                                                                                                                                     |
| `BOARD_CONFLICT`                | 409    | someone saved the board first: reload, reapply, save                                                                                                                                                                                          |
| `UNKNOWN_EVIDENCE`, `BAD_QUOTE` | 422    | the board refers to something the team can't use                                                                                                                                                                                              |
| `INVALID_REQUEST`               | 422    | malformed body; `details.errors` lists what                                                                                                                                                                                                   |
| `NOT_FOUND`                     | 404    | includes other companies' stakeholders, by design                                                                                                                                                                                             |

## 3. Interviews

- `GET /stakeholders` lists the team's company's stakeholders with `questions_remaining`
  and `look` (portrait art direction, `{}` when the case gives none). `web/lib.js`
  `Portrait` draws a flat SVG bust from it; `resolveLook()` fills gaps from the id.
- `POST /stakeholders/{id}/questions` with `{"client_request_id": "<uuid>", "text": "..."}`
  (text up to 400 characters).
  - Generate a **new UUID per question** and **reuse it if you retry** the same question:
    a retry returns the original answer instead of charging again.
  - Answers take **3–5 seconds**. Show a "… is thinking" state rather than a spinner.
  - Only **one question per stakeholder per team** at a time (`QUESTION_IN_FLIGHT`).
  - The response has `answer`, `evidence` (cards), `questions_remaining`, `question_id`,
    `answer_id` and `kind`: `"answer"`, or `"hr_notice"` when the stakeholder ends the
    interview over hostile questioning (show it as a notice, not as their speech).
- `GET /stakeholders/{id}/messages` is the team's whole thread with that person, the same
  on every laptop. Messages have `role` `question`, `answer` or `notice` (the HR walk-out).
- Each stakeholder in `GET /stakeholders` and `GET /status` has `department` and
  `available_in_seconds`: 0 normally, or the time left on an HR lockout. **A lockout
  covers the whole department** (10 minutes by default), so every stakeholder in it shows
  the same countdown. Show the HR pop-up on the `hr_notice`, then the countdown on that
  department's stakeholders, and disable asking them.

## 4. Evidence cards

Each card has `passage_id`, `record_id`, `record_title`, `excerpt`, `mode` and `new`.

- `mode` is `"quote"` or `"paraphrase"`. Paraphrased excerpts contain `[withheld]` where a
  figure is hidden. Label paraphrased cards; don't hide the marker.
- A card can later come back as `"quote"` with the full text (the team earned it). Treat
  `new: true` on a card you already have as an upgrade.
- `GET /evidence` lists every card the team has, from any stakeholder, with who disclosed
  it, when, and the record's date.

`GET /records` is the records tab: `{mode: "off" | "titles" | "full", records: [{id,
company_id, partner, kind, title, date, passages: [{passage_id, text}]}]}`. `passages` is
empty unless the mode is `full`; then each passage may be pinned and cited exactly like a
card from `/evidence` (the server accepts them). Hide the tab when the mode is `off`, and
refetch on the live event `{"type": "records"}` (organisers changed the mode). `web/lib.js`
`evidencePool(s)` merges the team's evidence, its partner's, and the tab into one list.

## 5. Brief and announcements

- `GET /brief` → `{"case": "...", "company": {"id", "name", "brief"} | null}`. Both texts are
  markdown. `company` is `null` until the team is assigned one.
- `GET /announcements` → organiser broadcasts for this team, newest first
  (`id`, `text`, `created_at`). Some go to one company only; the server filters.
- `GET /display` needs **no login**: it's for the projector. Event name, phase and clock,
  and the five newest broadcasts meant for everyone. Nothing team-specific.

- `GET /rounds` → every round in order (`key`, `name`, `status`, `planned_seconds`,
  `capabilities`) with `my_status` (`advancing` / `eliminated` / `waitlisted` / `pending`
  - `message`) once organisers publish it. Show it prominently: it's how teams learn
    whether they're through.
- `GET /documents` → what the team has been handed; `GET /documents/{id}` returns
  `{"title", "body"}` for a note (markdown) or the file itself (link to it directly).
- Error `NOT_IN_ROUND` (409): the team was eliminated from the current round.

- **Alliance:** `GET /alliance` (`can_request`, `locked`, `incoming`, `outgoing`),
  `GET /alliance/teams`, `POST /alliance/requests {"team"}`,
  `POST /alliance/requests/{id}/accept`, `DELETE /alliance/requests/{id}`,
  `GET /alliance/evidence` (partners' cards, with `team_code`; only once locked).
- **Findings sheet:** `GET /submission`, `PUT /submission {"base_version", "summary",
"findings": [{id, title, finding, impact, recommendation, sources, cross_company}],
"final"}`. 409 `SUBMISSION_CONFLICT` = a teammate saved first; 409 `SUBMISSIONS_CLOSED`;
  422 `SHEET_INCOMPLETE` (`details.problems`) or `UNKNOWN_EVIDENCE`.

## 6. What the UI must never do

- **Never show trust**, or anything that suggests it: no meter, no mood indicator. Trust is
  hidden by the rules and the API doesn't expose it.
- **Never mark an answer as refused, blocked or special.** Every answer is plain text, even
  a non-answer. Anything that marks "this one was different" tells teams exactly where the
  secrets are. The one exception is the HR walk-out, which arrives as `kind: "hr_notice"`
  because teams are meant to see it: it's a consequence of their own conduct.
- **Never draw links between cards automatically.** Only the team draws links. Automatic
  links would do the deduction the competition is judging.

## 7. Keeping laptops in sync

Open one WebSocket per tab: `new WebSocket("wss://<same host>/api/v1/ws")`. The session
cookie is sent automatically (same origin). The socket only says **what changed**; you
refetch it through the normal endpoints. It never carries content.

| event                                         | refetch                                                                     |
| --------------------------------------------- | --------------------------------------------------------------------------- |
| `{"type": "hello", "team": "A01"}`            | nothing: connected                                                          |
| `{"type": "phase"}`                           | `GET /status` (phase, clock, `interviews_open`)                             |
| `{"type": "stakeholders"}`                    | `GET /status` or `GET /stakeholders` (questions left, availability)         |
| `{"type": "thread", "stakeholder_id": "cfo"}` | `GET /stakeholders/cfo/messages`                                            |
| `{"type": "evidence"}`                        | `GET /evidence`                                                             |
| `{"type": "board", "version": 4}`             | `GET /board` if your version is older                                       |
| `{"type": "content"}`                         | everything on screen (the case was reloaded)                                |
| `{"type": "team"}`                            | `GET /status` and `GET /brief` (the team's company was assigned or changed) |
| `{"type": "announcement"}`                    | `GET /announcements` (new or retracted broadcast)                           |
| `{"type": "documents"}`                       | `GET /documents` (released, withdrawn, or a round started)                  |
| `{"type": "rounds"}`                          | `GET /rounds` (a result for this team was published)                        |
| `{"type": "alliance"}`                        | `GET /alliance` and `GET /alliance/evidence`                                |
| `{"type": "submission"}`                      | `GET /submission` (a teammate saved)                                        |
| `{"type": "ping"}`                            | nothing (keep-alive every 25 s)                                             |

Close codes: `4401` not logged in or logged out → login screen; `4403` wrong origin;
`4429` too many tabs for this team (limit 8). Any other close: reconnect after a few
seconds with backoff, then refetch what's on screen (events missed while disconnected
aren't replayed).

- Count down from `remaining_seconds` in `GET /status`, correcting with `server_time`.
  `remaining_seconds` is `null` for a phase with no timer; `phase` is `null` between phases.
  It's **this team's** time: organisers can give a team time back after a crash, so two
  teams can see different clocks. Don't take the clock from `/display` for a team screen.
- Use `interviews_open` to enable asking. It turns false when organisers pause, when the
  phase changes, and when a timed phase runs out of time (no event is sent for that; your
  countdown reaching zero is the signal).
- An HR lockout ending also sends no event: count down `available_in_seconds`.

## 8. Evidence board

`GET /board` and `PUT /board` with:

```json
{
  "version": 3,
  "cards": [
    {
      "id": "c1",
      "kind": "evidence",
      "passage_id": "A-FIN-002#1",
      "x": 40,
      "y": 30
    },
    {
      "id": "q1",
      "kind": "quote",
      "message_id": "<answer_id>",
      "start": 12,
      "end": 58
    },
    { "id": "n1", "kind": "note", "text": "Ask Arjun who changed sampling" }
  ],
  "links": [{ "from": "c1", "to": "q1", "label": "contradicts" }],
  "extra": {}
}
```

- Send the `version` you loaded. On `409 BOARD_CONFLICT`: reload, reapply your change, save.
- **Evidence cards:** a `passage_id` from `/evidence` (only evidence the team has been shown).
- **Quote cards:** an `answer_id` plus character `start`/`end` in that answer's text. The
  server fills in `text` and `stakeholder_id` itself; any text you send is ignored.
- **Notes:** up to 1,000 characters.
- **Links:** between cards on the board, with a team-chosen `label` up to 40 characters.
- **`extra`:** yours for anything UI-specific (hypotheses, layout, view settings), up to 32 KB.
- Card ids: 1–64 of `A–Z a–z 0–9 _ -`. Limits: 300 cards, 600 links.

## 9. Content

The current case (Northfield) is a **placeholder**; the real case arrives Monday. Don't
hardcode stakeholder names, ids or counts: there are 3–5 stakeholders per company and 2
companies, and everything comes from the API.

## 10. Event credentials

For the event, organisers create teams in Console → **Teams**. Each team's access code
stays visible in the Teams table and downloads as a CSV; organisers read it out to the team.
The console can rotate a team's code, which invalidates the old code and logs out active
sessions. Self-registration (`/register`) and email delivery have been removed.

## 11. Coming next

- Judging.

This file is updated whenever the API changes.
