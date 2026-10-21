const BASE=process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
// What went wrong, in the server's own words. FastAPI sends {"detail": "..."} or, for a
// coded refusal, {"detail": {"code", "message"}}; shown raw, a reviewer saw JSON.
async function problem(r:Response){const text=await r.text();try{const d=JSON.parse(text).detail;if(typeof d==="string")return d;if(d?.message)return d.message}catch{}return text||`Request failed (${r.status})`}
export const imageUrl=(path:string)=>`${BASE}${path}`;
export const regenerateUrl=(id:string)=>`${BASE}/api/v1/form-analyses/${id}/regenerate`;
// A blank form is kept exactly as uploaded for the template's master; a filled one has the
// answers read on it lifted off the page instead.
export async function deriveTemplate(id:string,blank=false){const r=await fetch(`${BASE}/api/v1/form-analyses/${id}/derive-template${blank?"?blank=true":""}`,{method:"POST"});if(!r.ok)throw new Error(await problem(r));return r.json() as Promise<{template_id:string;field_count:number}>}
export async function analyze(files:File|File[]){const body=new FormData();const list=Array.isArray(files)?files:[files];
// One page per file when a form was photographed a page at a time; the single-file field
// stays for PDFs and for anything already calling this.
if(list.length===1)body.append("file",list[0]);else list.forEach(f=>body.append("files",f));const r=await fetch(`${BASE}/api/v1/form-analyses`,{method:"POST",body});if(!r.ok)throw new Error(await problem(r));return r.json()}
export async function getAnalysis(id:string){const r=await fetch(`${BASE}/api/v1/form-analyses/${id}`,{cache:"no-store"});if(!r.ok)throw new Error(await problem(r));return r.json()}
export type SlotCorrection={page_index:number;label:string;bbox:{x:number;y:number;width:number;height:number};value?:string|null;analysis_id?:string|null};
// A reviewer's word on where an answer lives teaches the matched template: the slot is
// read there on every later copy of the form. Positions are page fractions.
export async function teachTemplate(templateId:string,corrections:SlotCorrection[]){const r=await fetch(`${BASE}/api/v1/templates/${templateId}/corrections`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(corrections)});if(!r.ok)throw new Error(await problem(r));return r.json() as Promise<{template_id:string;page_count:number;field_count:number}>}
export async function correctAnalysis(id:string,field:string,value:string){const r=await fetch(`${BASE}/api/v1/form-analyses/${id}/fields/${field}`,{method:"PATCH",headers:{"content-type":"application/json"},body:JSON.stringify({value,reviewed:true})});if(!r.ok)throw new Error(await problem(r))}
export type StoredForm={analysis_id:string;source_type:string;field_count:number;review_count:number;matched_known_template:string|null;stored_at:string};
export async function listAnalyses(){const r=await fetch(`${BASE}/api/v1/form-analyses`,{cache:"no-store"});if(!r.ok)throw new Error(await problem(r));return (await r.json()).analyses as StoredForm[]}
export type Attestation={scope:string;signed_by:string;signed_at:string;covers:string;field_ids:string[];note:string|null;still_current:boolean;identity?:string};
export type SignOffStatus={attestations:Attestation[];signed:boolean;fields_needing_review:number};
export async function signOffStatus(id:string){const r=await fetch(`${BASE}/api/v1/form-analyses/${id}/sign-off`,{cache:"no-store"});if(!r.ok)throw new Error(await problem(r));return r.json() as Promise<SignOffStatus>}
export async function signOff(id:string,signedBy:string,note?:string){const r=await fetch(`${BASE}/api/v1/form-analyses/${id}/sign-off`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({signed_by:signedBy,note})});if(!r.ok)throw new Error(await problem(r));return r.json() as Promise<{attestation:Attestation;fields_covered:number;still_flagged:string[]}>}
export type HistoryEvent={at:string;kind:string;summary:string;field_id:string|null;label:string|null;was:unknown;now:unknown;by:string|null};
export type FormHistory={analysis_id:string;patient_key:string|null;source:string;durable_events:number;events:HistoryEvent[]};
export async function formHistory(id:string){const r=await fetch(`${BASE}/api/v1/form-analyses/${id}/history`,{cache:"no-store"});if(!r.ok)throw new Error(await problem(r));return r.json() as Promise<FormHistory>}
export type PatientChange={key:string;was:unknown;now:unknown};
export type PatientForm={analysis_id:string;stored_at:string;field_count:number;signed_off:boolean;changed_since_previous:PatientChange[]};
export async function patientHistory(key:string){const r=await fetch(`${BASE}/api/v1/patients/${encodeURIComponent(key)}/history`,{cache:"no-store"});if(!r.ok)throw new Error(await problem(r));return r.json() as Promise<{patient_key:string;forms:PatientForm[]}>}
export type Regenerated={blob:Blob;templateId:string|null;unplaced:string[]};
// Fetched rather than linked, because what the server has to say about the copy travels in
// the headers: which template was used, and which values it had nowhere to put.
export async function regenerate(id:string){const r=await fetch(regenerateUrl(id));if(!r.ok)throw new Error(await problem(r));const unplaced=(r.headers.get("X-Unplaced-Fields")??"").split(",").map(s=>s.trim()).filter(Boolean);return {blob:await r.blob(),templateId:r.headers.get("X-Template-Id"),unplaced} as Regenerated}
