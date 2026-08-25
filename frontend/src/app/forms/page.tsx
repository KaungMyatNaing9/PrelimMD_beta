"use client";
import {useEffect,useState} from "react"; import Link from "next/link"; import {listAnalyses,type StoredForm} from "../../lib/api";
export default function Forms(){const[rows,setRows]=useState<StoredForm[]>();const[error,setError]=useState("");
useEffect(()=>{listAnalyses().then(setRows).catch(e=>setError(e instanceof Error?e.message:"Could not load stored forms"))},[]);
if(error)return <section className="card"><h1>Stored forms</h1><p role="alert" className="error">{error}</p></section>;
if(!rows)return <section className="card"><h1>Stored forms</h1><p>Loading…</p></section>;
return <section className="card"><p className="eyebrow">PrelimMD · synthetic-data prototype</p><h1>Stored forms</h1>
<p>{rows.length} stored {rows.length===1?"record":"records"}. Open one to review its data, correct fields, or regenerate a filled PDF.</p>
{rows.length===0?<p><small>Nothing stored yet — <Link href="/capture">capture a form</Link> to begin.</small></p>:
<table className="stored"><thead><tr><th>Stored</th><th>Source</th><th>Template</th><th>Fields</th><th>Needs review</th><th/></tr></thead>
<tbody>{rows.map(r=><tr key={r.analysis_id}>
<td>{new Date(r.stored_at).toLocaleString()}</td>
<td>{r.source_type}</td>
<td>{r.matched_known_template ?? <small>unmatched</small>}</td>
<td>{r.field_count}</td>
<td>{r.review_count>0?<span className="badge low">{r.review_count}</span>:<span className="badge">0</span>}</td>
<td><Link href={`/review/${r.analysis_id}`}>Open</Link></td></tr>)}</tbody></table>}
<p style={{marginTop:18}}><Link href="/capture">Capture another form</Link></p></section>}
