import { useState,useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Card,CardHeader,CardTitle,CardContent } from '@/components/ui/Card'
import Button from '@/components/ui/Button'
import { ontologyGraphRequest,ontologyPost } from '@/lib/ontologyGraph'
import { toast } from 'sonner'
type Resource={id:string;name:string;enabled:number;data:Record<string,unknown>}
type Profile={id:string;name:string;enabled:number;ontology_id:string;prompt_id:string}
export default function OntologyResources(){
 const {t}=useTranslation()
 const [ontologies,setOntologies]=useState<Resource[]>([]),[prompts,setPrompts]=useState<Resource[]>([]),[profiles,setProfiles]=useState<Profile[]>([])
 const [deps,setDeps]=useState<string[]>([]),[busy,setBusy]=useState(false)
 const load=async()=>{const [o,p,r]=await Promise.all([ontologyGraphRequest<Resource[]>('ontologies'),ontologyGraphRequest<Resource[]>('prompts'),ontologyGraphRequest<Profile[]>('profiles')]);setOntologies(o);setPrompts(p);setProfiles(r)}
 useEffect(()=>{load().catch(e=>toast.error(String(e)))},[])
 const action=async(fn:()=>Promise<unknown>)=>{setBusy(true);try{await fn();await load()}catch(e){toast.error(String(e))}finally{setBusy(false)}}
 const upload=(kind:string,e:React.FormEvent<HTMLFormElement>)=>{e.preventDefault();const form=e.currentTarget;const data=new FormData(form);if(kind==='ontologies')data.append('dependency_ids',JSON.stringify(deps));void action(async()=>{await ontologyGraphRequest(kind,{}, {method:'POST',body:data});form.reset();if(kind==='ontologies')setDeps([])})}
 const toggle=(kind:string,r:{id:string;enabled:number})=>action(()=>ontologyGraphRequest(kind+'/'+r.id,{}, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({enabled:!r.enabled})}))
 const state=(r:{enabled:number})=>r.enabled?t('gptonto.enabled','Enabled'):t('gptonto.disabled','Disabled')
 return <div className="h-full overflow-auto p-4 space-y-4">
 {([{kind:'ontologies',items:ontologies,label:t('gptonto.ontologies','Ontologies'),accept:'.ttl'},{kind:'prompts',items:prompts,label:t('gptonto.prompts','Prompts'),accept:'.yaml,.yml'}]).map(x=><Card key={x.kind}><CardHeader><CardTitle>{x.label}</CardTitle></CardHeader><CardContent>
 <form className="flex gap-2 flex-wrap items-center" onSubmit={e=>upload(x.kind,e)}><input name="name" required className="border rounded p-2" placeholder={t('gptonto.name','Name')}/><input name="file" type="file" required accept={x.accept}/>{x.kind==='ontologies'&&<label>{t('gptonto.dependencies','Dependencies')} <select multiple className="border rounded" value={deps} onChange={e=>setDeps(Array.from(e.target.selectedOptions).map(o=>o.value))}>{ontologies.map(o=><option key={o.id} value={o.id}>{o.name}</option>)}</select></label>}<Button type="submit" disabled={busy}>{t('gptonto.upload','Upload')}</Button></form>
 <div className="mt-3 space-y-2">{x.items.map(r=><div key={r.id} className="border p-2 rounded flex flex-wrap gap-2 items-center"><b>{r.name}</b><span>{state(r)}</span><Button size="sm" variant="outline" disabled={busy} onClick={()=>toggle('resources',r)}>{r.enabled?t('gptonto.disable','Disable'):t('gptonto.enable','Enable')}</Button><details className="w-full"><summary>Schema / YAML</summary><pre className="whitespace-pre-wrap break-all max-h-72 overflow-auto">{JSON.stringify(r.data,null,2)}</pre></details></div>)}</div></CardContent></Card>)}
 <Card><CardHeader><CardTitle>{t('gptonto.profiles','Extraction profiles')}</CardTitle></CardHeader><CardContent><form className="flex flex-wrap gap-2" onSubmit={e=>{e.preventDefault();const form=e.currentTarget;const data=Object.fromEntries(new FormData(form));void action(async()=>{await ontologyPost('profiles',data);form.reset()})}}><input required name="name" className="border rounded p-2" placeholder={t('gptonto.name','Name')}/><select name="ontology_id" required className="border rounded"><option value="">{t('gptonto.ontologies','Ontologies')}</option>{ontologies.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select><select name="prompt_id" required className="border rounded"><option value="">{t('gptonto.prompts','Prompts')}</option>{prompts.map(r=><option key={r.id} value={r.id}>{r.name}</option>)}</select><Button type="submit" disabled={busy}>{t('gptonto.create','Create')}</Button></form>
 {profiles.map(r=><div key={r.id} className="border rounded p-2 mt-2 flex flex-wrap gap-3"><b>{r.name}</b><span>{ontologies.find(o=>o.id===r.ontology_id)?.name} + {prompts.find(p=>p.id===r.prompt_id)?.name}</span><span>{state(r)}</span><Button variant="outline" size="sm" disabled={busy} onClick={()=>toggle('profiles',r)}>{r.enabled?t('gptonto.disable','Disable'):t('gptonto.enable','Enable')}</Button></div>)}</CardContent></Card>
 </div>
}
