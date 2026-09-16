# Example: LinkedIn company People

Path: company page → People tab → employee cards visible.

Auth: signed-in LinkedIn session on headed Chrome. Never commit cookies.

Capture lesson: HAR taken on feed/messaging only is invalid. Export after People cards load.

UI limit: free company People tab may mask names as "LinkedIn Member" while titles remain.

Replay target: `GET /voyager/api/graphql` with organization/people `queryId` from a valid People HAR, plus session `csrf-token` and cookies from the live profile.

Owning skill: [outreach-leads](../../outreach-leads/SKILL.md) `enrich --layer contacts`.
