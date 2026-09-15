# API

All routes start with `/api/v1`. Generated from `backend/app/main.py` on 2026-09-14;
the form-understanding path is the only one.

| Route | Purpose |
| --- | --- |
| `GET /health` | — |
| `GET /form-analyses` | Stored forms, newest first, for the browser. |
| `GET /form-analyses/{analysis_id}` | — |
| `GET /form-analyses/{analysis_id}/history` | Everything that has happened to one form, oldest first. |
| `GET /patients` | Patients this system can name, and how many forms it holds for each. |
| `GET /patients/{patient_key}/history` | One patient's forms over time, and what changed between consecutive ones. |
| `GET /form-analyses/{analysis_id}/pages/{page_index}/image` | — |
| `GET /form-analyses/{analysis_id}/pages/{page_index}/overlay` | — |
| `GET /form-analyses/{analysis_id}/debug` | — |
| `PATCH /form-analyses/{analysis_id}/fields/{field_id}` | — |
| `POST /form-analyses/{analysis_id}/sign-off` | Record that a person checked these values and stands behind them. |
| `GET /form-analyses/{analysis_id}/sign-off` | Whether this form's attestations still describe what it currently says. |
| `POST /templates/{template_id}/fill` | Fill a blank template with supplied content and return the PDF. |
| `GET /form-analyses/{analysis_id}/fhir` | The analysis as a FHIR QuestionnaireResponse, the shape other systems can read. |
| `POST /templates/{template_id}/corrections` | Teach a template where a reviewer says its answers live. |
| `DELETE /templates/{template_id}` | Remove a template that was learned at runtime. |
| `POST /form-analyses/{analysis_id}/derive-template` | Learn a reusable blank template from a form no registered template matched. |
| `GET /form-analyses/{analysis_id}/regenerate` | Fill this analysis's stored values onto its blank template, as a PDF. |
| `POST /form-analyses` | Analyse an uploaded form. |
