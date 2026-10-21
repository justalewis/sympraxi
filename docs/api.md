# The Sympraxi API

A director makes a token under **API and webhooks** on the center's page, chooses what
it may do, and copies it once. Everything is under `/api/v1/centers/<slug>/` and
answers JSON.

```
curl -H "Authorization: Bearer sym_AbCd1234_..." \
  https://writing.example.edu/api/v1/centers/main/appointments/?limit=50
```

A token belongs to one center and acts as the director who made it. If that director
leaves the role, the token stops working. It can be revoked at any time, and it expires
on the date chosen. About 600 calls a minute are allowed per token.

## Scopes

| Scope | Lets the token |
|---|---|
| `schedules:read` | List schedules and their tutors |
| `appointments:read` | List appointments, with the students in them |
| `appointments:write` | Book and cancel appointments |
| `students:read` | List students: name, email, student ID |
| `workshops:read` | List workshops with counts of who registered and came |

## Endpoints

| Call | What it does |
|---|---|
| `GET schedules/` | Schedules, each with its tutors, lengths and ways of meeting |
| `GET appointments/` | Appointments. Filter with `from`, `to` (times with an offset, like `2026-10-21T09:00:00-04:00`) and `status` |
| `POST appointments/` | Book one. Body: `assignment`, `starts_at`, `minutes`, `modality`, `student_email` |
| `POST appointments/<id>/cancel/` | Cancel an appointment |
| `GET students/` | The center's students |
| `GET workshops/` | The center's workshops |

Lists take `limit` (up to 500) and `after`, the `next` value from the previous answer;
`next` is `null` on the last page. Booking uses the same rules as the site: limits,
booking blocks, the tutor's hours and double-booking all apply, and a refusal comes
back as an error with the rule's code (`slot_taken` is a 409; other refusals are 422).
Each write is in the audit log as made by the token.

Errors look like `{"error": {"code": "missing_scope", "message": "..."}}`. The codes
are `invalid_token` (401), `missing_scope` and `owner_lost_access` (403),
`rate_limited` (429), and 400, 404 and 422 codes that name the problem.

## Webhooks

A webhook is a web address that is told when something happens. It must start with
`https://` and be reachable from the internet; addresses that resolve to private or
internal networks are refused. Events: `appointment.booked`, `appointment.changed`,
`appointment.canceled`, `workshop.registered`, `workshop.canceled`, `arrival.recorded`.

A notice is a POST of JSON like

```json
{"id": "812", "type": "appointment.booked", "created": "2026-10-21T13:02:11Z",
 "center": "main", "data": {"object": "appointment", "id": "5d1f..."}}
```

It names no one. Ask the API for the appointment, with a token that may read it, to
learn who is in it. Headers carry `Sympraxi-Event`, `Sympraxi-Delivery` (the same id on
a retry) and `Sympraxi-Signature: t=<unix time>,v1=<hex>`. To check it, compute the
HMAC-SHA256 of `<t>.<raw body>` with the endpoint's secret (shown once when the webhook
is made) and compare it to `v1`, and refuse a `t` more than a few minutes old.

Answer with any 2xx status. Anything else, or no answer in 10 seconds, is retried after
1 minute, 5 minutes, 30 minutes, 2 hours and 12 hours, and then given up on. The page
lists the last notices and what happened to them, and can send a test notice.
Retries need the scheduler (`manage.py scheduler`) and the task worker running.
