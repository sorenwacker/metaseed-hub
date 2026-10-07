# API Reference

## Interactive reference

The hub serves an OpenAPI description of the REST API and a Swagger UI page that renders it.

| Path | Content |
|------|---------|
| `/docs` | Swagger UI: every `/api` operation with its parameters, request body and response schema |
| `/openapi.json` | The OpenAPI document the page is generated from, for client generators |

To open the page from the web interface, select **API reference** in the *Access tokens* section of your profile page. The page is readable without signing in; it describes the operations and returns no data.

To call an operation from the page, select **Authorize**, paste an access token, and then use **Try it out** on the operation. The token is kept in the page's memory and is discarded when the tab is closed or reloaded.

The Swagger UI script and stylesheet are served by the hub from `/hub/hub-static/vendor/swagger-ui/`, because the hub's Content-Security-Policy allows scripts and styles from its own origin only. ReDoc is not served.

The web interface mounted at `/hub` is not an API: its routes return HTML fragments for the browser, are authenticated by the session cookie, and change without notice. It publishes no OpenAPI document, so `/hub/docs`, `/hub/redoc` and `/hub/openapi.json` answer 404.

## REST API (`/api`, bearer token)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/me` | The account and tenant the token acts in, and the collaborations a publish may be addressed to |
| GET | `/api/datasets` | The caller's datasets in a tenant (`?tenant_id=`) |
| POST | `/api/datasets` | Create a dataset, stored in the hub's canonical tree form. `profile` and `version` name an installed profile, else a published specification visible to the caller, else the caller's own draft, and the dataset is bound to what was found; 422 when the hub holds none, 409 when the tenant already has a dataset of that name |
| GET | `/api/datasets/{id}` | One dataset with its entities |
| PATCH | `/api/datasets/{id}` | Replace a dataset's name or entities through the same save path as the web interface, which records a version; 409 when the new name is taken |
| DELETE | `/api/datasets/{id}` | Soft-delete a dataset |
| GET | `/api/specs` | Published specifications |
| GET | `/api/specs/{name}/{version}` | One published specification as YAML |
| POST | `/api/specs` | Push a profile document (`{"yaml": ...}`) as a private draft, one per name and version; `"publish": true` publishes it, 409 under the version-bump gate; `"audience": "<urn>"` publishes to one collaboration instead of to everyone |
| POST | `/api/specs/{id}/unpublish` | Withdraw a published specification to a private draft |

### Publishing to a collaboration

A publish without an `audience` reaches every user of the hub, which is what publishing has always meant. With one, the specification is released to that collaboration alone: its members see it and can build datasets on it, and for everyone else it is absent from every listing this API offers.

The URNs you may name are the ones `GET /api/me` reports under `collaborations`, and only those: a group URN is refused with 403, because a release is addressed to a collaboration or to the whole hub. Any collaboration you are not in is refused the same way. An `audience` sent without `publish` is refused with 422, because a draft is private and is shared with people or a collaboration rather than published to one. Every entry `GET /api/specs` returns carries `audience`: the collaboration a published specification went to, or null for everyone.

## WebSocket

Connect to `/ws/{project_id}` for real-time collaboration.

Message types:

- `presence` - User join/leave
- `chat` - Chat messages
- `cursor` - Field focus indicators
- `update` - Entity changes
