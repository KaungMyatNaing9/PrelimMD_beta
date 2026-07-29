# API

All routes start with `/api/v1`.

| Route | Purpose |
| --- | --- |
| `GET /health` | Status, configured vision mode, registered templates |
| `POST /captures` | Multipart `file`, optional `template_id`; validates and preprocesses a page |
| `POST /runs/{run_id}/extract` | Aligns, crops known template fields, extracts and scores values |
| `GET /runs/{run_id}` | Complete versioned extraction result |
| `GET /runs/{run_id}/pages/0/image` | Canonical normalized page for the overlay |
| `PATCH /runs/{run_id}/fields/{field_id}` | Saves a human correction while preserving the prior extraction in alternatives |

Errors use HTTP status codes and avoid provider stack traces. The current prototype processes the first PDF page and stores artifacts locally under `ARTIFACT_ROOT`.
