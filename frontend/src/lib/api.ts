import { Run } from "./schemas";
const BASE=process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
export async function upload(file: File, templateId?: string){const body=new FormData();body.append("file",file);if(templateId)body.append("template_id",templateId);const r=await fetch(`${BASE}/api/v1/captures`,{method:"POST",body});if(!r.ok)throw new Error(await r.text());return r.json() as Promise<{run_id:string}>}
export async function extract(id:string){const r=await fetch(`${BASE}/api/v1/runs/${id}/extract`,{method:"POST"});if(!r.ok)throw new Error(await r.text());return r.json() as Promise<Run>}
export async function getRun(id:string){const r=await fetch(`${BASE}/api/v1/runs/${id}`,{cache:"no-store"});if(!r.ok)throw new Error(await r.text());return r.json() as Promise<Run>}
export async function correct(id:string,field:string,value:unknown){const r=await fetch(`${BASE}/api/v1/runs/${id}/fields/${field}`,{method:"PATCH",headers:{"content-type":"application/json"},body:JSON.stringify({value,reviewed:true})});if(!r.ok)throw new Error(await r.text())}
export const imageUrl=(path:string)=>`${BASE}${path}`;
export async function analyze(file:File){const body=new FormData();body.append("file",file);const r=await fetch(`${BASE}/api/v1/form-analyses`,{method:"POST",body});if(!r.ok)throw new Error(await r.text());return r.json()}
export async function getAnalysis(id:string){const r=await fetch(`${BASE}/api/v1/form-analyses/${id}`,{cache:"no-store"});if(!r.ok)throw new Error(await r.text());return r.json()}
export async function correctAnalysis(id:string,field:string,value:string){const r=await fetch(`${BASE}/api/v1/form-analyses/${id}/fields/${field}`,{method:"PATCH",headers:{"content-type":"application/json"},body:JSON.stringify({value,reviewed:true})});if(!r.ok)throw new Error(await r.text())}
