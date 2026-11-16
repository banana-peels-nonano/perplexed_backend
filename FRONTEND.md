# Notes for the frontend

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
{"error": {"code": "QUOTA_EXHAUSTED", "message": "no questions left for this person", "details": {}}}
```

| code | status | what to do |
|---|---|---|
| `INVALID_CODE` | 401 | wrong access code |
| `SESSION_LIMIT` | 409 | team already has 2 laptops logged in |
| `UNAUTHENTICATED` | 401 | session missing or expired: back to login |
| `COMPANY_NOT_ASSIGNED` | 409 | team not yet assigned a company |
| `PHASE_CLOSED` | 409 | interviews aren't open in this phase |
| `QUOTA_EXHAUSTED` | 409 | no questions left for this stakeholder |
| `QUESTION_IN_FLIGHT` | 409 | a teammate's question to this person is still being answered |
| `HR_LOCKOUT` | 409 | this person walked out over the team's conduct; `details.retry_after_seconds` says when they'll talk again (the attempt isn't charged) |
| `PROVIDER_BUSY` | 503 | try again in a few seconds (the question was not charged) |
| `BOARD_CONFLICT` | 409 | someone saved the board first: reload, reapply, save |
| `UNKNOWN_EVIDENCE`, `BAD_QUOTE` | 422 | the board refers to something the team can't use |
| `INVALID_REQUEST` | 422 | malformed body; `details.errors` lists what |
| `NOT_FOUND` | 404 | includes other companies' stakeholders, by design |

## 3. Interviews

- `GET /stakeholders` lists the team's company's stakeholders with `questions_remaining`.
- `POST /stakeholders/{id}/questions` with `{"client_request_id": "<uuid>", "text": "..."}`
  (text up to 1,000 characters).
  - Generate a **new UUID per question** and **reuse it if you retry** the same question:
    a retry returns the original answer instead of charging again.
  - Answers take **3–5 seconds**. Show a "… is thinking" state rather than a spinner.
  - Only **one question per stakeholder per team** at a time (`QUESTION_IN_FLIGHT`).
  - The response has `answer`, `evidence` (cards), `questions_remaining`, `question_id`,
    `answer_id` and `kind`: `"answer"`, or `"hr_notice"` when the stakeholder ends the
    interview over hostile questioning (show it as a notice, not as their speech).
- `GET /stakeholders/{id}/messages` is the team's whole thread with that person, the same
  on every laptop. Messages have `role` `question`, `answer` or `notice` (the HR walk-out).
- Each stakeholder in `GET /stakeholders` and `GET /status` has `available_in_seconds`:
  0 normally, or the time left on an HR lockout. Show a countdown and disable asking.

## 4. Evidence cards

Each card has `passage_id`, `record_id`, `record_title`, `excerpt`, `mode` and `new`.

- `mode` is `"quote"` or `"paraphrase"`. Paraphrased excerpts contain `[withheld]` where a
  figure is hidden. Label paraphrased cards; don't hide the marker.
- A card can later come back as `"quote"` with the full text (the team earned it). Treat
  `new: true` on a card you already have as an upgrade.
- `GET /evidence` lists every card the team has, from any stakeholder, with who disclosed
  it, when, and the record's date.

## 5. What the UI must never do

- **Never show trust**, or anything that suggests it: no meter, no mood indicator. Trust is
  hidden by the rules and the API doesn't expose it.
- **Never mark an answer as refused, blocked or special.** Every answer is plain text, even
  a non-answer. Anything that marks "this one was different" tells teams exactly where the
  secrets are. The one exception is the HR walk-out, which arrives as `kind: "hr_notice"`
  because teams are meant to see it: it's a consequence of their own conduct.
- **Never draw links between cards automatically.** Only the team draws links. Automatic
  links would do the deduction the competition is judging.

## 6. Keeping laptops in sync

- For now, **poll `GET /status`** about every 10 s. It returns the phase (`key`, `name`,
  `status`, `remaining_seconds`, `interviews_open`), questions left per stakeholder, the
  evidence count and `server_time`. Count down from `remaining_seconds`, correcting with
  `server_time`; a paused phase keeps the same `remaining_seconds`.
- Refetch threads and evidence after your own actions, and when `evidence_count` changes.
- **WebSockets are coming** as "something changed, refetch X" notifications. Keep each
  fetch as a function you can call from either a poll or a push.

## 7. Evidence board

`GET /board` and `PUT /board` with:

```json
{
  "version": 3,
  "cards": [
    {"id": "c1", "kind": "evidence", "passage_id": "A-FIN-002#1", "x": 40, "y": 30},
    {"id": "q1", "kind": "quote", "message_id": "<answer_id>", "start": 12, "end": 58},
    {"id": "n1", "kind": "note", "text": "Ask Arjun who changed sampling"}
  ],
  "links": [{"from": "c1", "to": "q1", "label": "contradicts"}],
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

## 8. Content

The current case (Northfield) is a **placeholder**; the real case arrives Monday. Don't
hardcode stakeholder names, ids or counts: there are 3–5 stakeholders per company and 2
companies, and everything comes from the API.

## 9. Coming next

- Organiser controls (phases, pause, extend).
- WebSocket notifications.
- Alliances, submissions and judging.

This file is updated whenever the API changes.
