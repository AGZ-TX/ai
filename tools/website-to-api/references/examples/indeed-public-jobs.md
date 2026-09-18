# Example: Indeed public company and jobs routes

Path: public company profile → public company jobs page → public keyword/location search with `vjk`.

Auth: the HTML profile, jobs page, and search page rendered publicly in the captured Browser session. Indeed may return a rate-limit or Cloudflare challenge on a later reload; stop there. Do not solve the challenge or retry through alternate hosts.

Observed public HTML routes:

- `GET /cmp/<company-slug>` — company profile HTML. Real slugs can contain punctuation, for example `Flores-Mendez,-P.c.`.
- `GET /cmp/<company-slug>/jobs` — company jobs HTML with visible job cards and 16-hex `jk` keys.
- `GET /q-<keywords>-l-<location>-jobs.html?vjk=<job-key>` — public search HTML with the selected job detail visible in the response.

Observed browser-session detail candidate:

```text
GET /cmp/-/rpc/fetch-jobs?jobKey=<job-key>&loggedIn=0
```

The response was JSON shaped as `findHiringEvents.result`, `jobData.results[].job` (`key`, `title`, `description.html`, `location.countryCode`, `indeedApply.scopes`) and `vjtk`. The request carried live browser cookies; it is a website-session replay candidate, not an anonymous developer API. Keep cookie values and any API-key values out of recipes.

Ignore `RecordJobSeen`, dwell, logging, and other telemetry requests. Clicking a company-jobs card may open an Indeed sign-in modal even when the list is public; do not log in or bypass it. The `vjk` search route is the public detail path observed for the Flores-Mendez example.

Owning anonymous parser: [outreach-leads](../../outreach-leads/SKILL.md). The parser accepts the public punctuation-bearing company slug and `vjk` search identity, but it does not replay the session-backed RPC.
