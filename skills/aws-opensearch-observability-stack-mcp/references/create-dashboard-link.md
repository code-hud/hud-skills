# Create a dashboard link

No tool returns links to custom dashboards, so build them by hand. Tool results that already contain an `osdUrl` (including the widget tools' deep links) are correct as they are and point at the OpenSearch UI app — pass those through instead of building your own. For a link to a **Hud** dashboard you still build it by hand, as below.

## 1. Base URL

Call `list_opensearch_ui_endpoints` and take the endpoint URL (`https://application-<id>.<region>.opensearch.amazonaws.com`). If the dashboard lives in a workspace, call `list_workspaces` and insert `/w/<workspace id>` after the host.

## 2. Links

**Discover, with a PPL query and a time range:**

```text
<base>[/w/<workspace>]/app/data-explorer/discover#?_g=(time:(from:now-1h,to:now))&_q=(query:(language:PPL,query:'<url-encoded PPL>'))
```

**A dashboard, by id:**

```text
<base>[/w/<workspace>]/app/dashboards#/view/<dashboard id>?_g=(time:(from:now-1h,to:now))
```

Hud's endpoint dashboard (id `hud-endpoint-dashboard`, when installed) takes the endpoint as a variable:

```text
<base>[/w/<workspace>]/app/dashboards#/view/hud-endpoint-dashboard?_g=(time:(from:now-1h,to:now))&_a=(variables:!((id:endpoint,current:!('<path>'))))
```

## 3. Rules

- URL-encode the PPL query and any value with spaces, quotes or `#`.
- Use absolute times (`from:'2026-10-18T10:00:00Z',to:'2026-10-18T11:00:00Z'`) when you link to a past incident, so the link still shows the same data later.
- You can't verify a link with these tools. Say that it was built by hand, and give the query next to it so the user can paste it if the link fails.
