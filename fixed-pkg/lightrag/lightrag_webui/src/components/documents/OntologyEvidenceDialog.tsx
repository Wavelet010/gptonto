import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/Dialog'
import Button from '@/components/ui/Button'
import { ontologyGraphRequest, ontologyDownload } from '@/lib/ontologyGraph'

type Chunk={id:string;chunk:{content:string;heading_path?:string[];evidence_kind?:string};extractions:any[];cross_relationships?:any[];errors?:string[]}
type Asset={path:string;size:number}
export default function OntologyEvidenceDialog({docId,filename,initialChunk,initialQuote}:{docId:string;filename:string;initialChunk?:string;initialQuote?:string}){
 const {t}=useTranslation()
 const [chunks,setChunks]=useState<Chunk[]>([]),[assets,setAssets]=useState<Asset[]>([]),[error,setError]=useState('')
 const [selected,setSelected]=useState<{content:string;quote:string;matches:{chunk_start:number;chunk_end:number;parsed_start?:number;parsed_end?:number}[];evidence_kind:string;heading?:unknown}|null>(null)
 const load=async()=>{setError('');setSelected(null);try{setChunks(await ontologyGraphRequest<Chunk[]>('documents/'+docId+'/chunks'));setAssets(await ontologyGraphRequest<Asset[]>('documents/'+docId+'/artifacts').catch(()=>[]))}catch(e){setError(String(e))}}
 const view=async(id:string,quote:string)=>{try{setSelected(await ontologyGraphRequest('evidence/'+id,{quote}))}catch(e){setError(String(e))}}
 const download=(path:string,name:string)=>ontologyDownload(path,name).catch(e=>setError(String(e)))
 return <Dialog onOpenChange={o=>{if(o)void load().then(()=>{if(initialChunk)void view(initialChunk,initialQuote||'')})}}><DialogTrigger asChild><Button size="sm" variant="outline">{initialQuote||t("gptonto.evidence","Evidence")}</Button></DialogTrigger><DialogContent className="sm:max-w-5xl max-h-[90vh] overflow-y-auto"><DialogHeader><DialogTitle>{filename} · {t("gptonto.evidence","Evidence")}</DialogTitle></DialogHeader>
 <Button onClick={()=>download('documents/'+docId+'/source',filename)}>{t("gptonto.source","Download original document")}</Button>
 {error&&<p className="text-red-600">{error}</p>}
 <details><summary>{t("gptonto.assets","Original parser assets")} ({assets.length})</summary>{assets.map(a=><div key={a.path}><button className="underline break-all" onClick={()=>download('documents/'+docId+'/artifacts/'+a.path.split('/').map(encodeURIComponent).join('/'),a.path.split('/').pop()||'asset')}>{a.path} ({a.size} B)</button></div>)}</details>
 {selected&&<section className="border rounded p-3"><b>{selected.evidence_kind==='multimodal_analysis'?t("gptonto.generated","Model-generated visual analysis (not verbatim source)"):t("gptonto.originalText","Parsed source text")}</b><p>{JSON.stringify(selected.heading||'')} · {JSON.stringify(selected.matches)}</p><pre className="whitespace-pre-wrap break-words max-h-64 overflow-y-auto">{selected.matches.length? <>{Array.from(selected.content).slice(0,selected.matches[0].chunk_start).join('')}<mark>{Array.from(selected.content).slice(selected.matches[0].chunk_start,selected.matches[0].chunk_end).join('')}</mark>{Array.from(selected.content).slice(selected.matches[0].chunk_end).join('')}</>:selected.content}</pre></section>}
 {chunks.map((c,i)=><details className="border rounded p-2" key={c.id}><summary>#{i+1} · {Array.isArray(c.chunk.heading_path)?c.chunk.heading_path.join(' / '):c.chunk.heading_path||''} · {c.chunk.evidence_kind||'parsed_text'}</summary><pre className="whitespace-pre-wrap break-words max-h-40 overflow-y-auto">{c.chunk.content}</pre>
 {c.extractions.map((x,j)=><div key={j}>{(x.entities||[]).map((e:any)=><div className="border-t py-2" key={e.local_id}><b>{e.name} ({e.class_uri})</b> <button className="underline" onClick={()=>view(c.id,e.evidence)}>{e.evidence}</button>{(e.attributes||[]).map((a:any,k:number)=><div key={k} className="pl-3">{a.property_uri}: {JSON.stringify(a.value)} · <button className="underline" onClick={()=>view(c.id,a.evidence)}>{a.evidence}</button></div>)}</div>)}{(x.relationships||[]).map((r:any,k:number)=><div key={k}>{r.source} → {r.predicate_uri} → {r.target} · <button className="underline" onClick={()=>view(c.id,r.evidence)}>{r.evidence}</button></div>)}</div>)}
 {(c.cross_relationships||[]).map((r:any,k:number)=><div key={k}>{r.predicate_uri} · <button className="underline" onClick={()=>view(c.id,r.evidence)}>{r.evidence}</button></div>)}</details>)}</DialogContent></Dialog>
}
