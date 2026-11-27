import { useEffect,useState } from 'react'
import { useTranslation } from 'react-i18next'
import Button from '@/components/ui/Button'
import Input from '@/components/ui/Input'
import PaginationControls from '@/components/ui/PaginationControls'
import { Table,TableBody,TableCell,TableHead,TableHeader,TableRow } from '@/components/ui/Table'
import OntologyEvidenceDialog from '@/components/documents/OntologyEvidenceDialog'
import { ontologyGraphRequest } from '@/lib/ontologyGraph'

type Source={doc_id:string;chunk_id:string;evidence:string;name?:string;property_uri?:string;value?:unknown}
type Instance={id:string;name:string;class_uri:string;sources:Source[];attributes:Source[]}
type Result={entities:Instance[];total:number;classes:string[]}
export default function OntologyInstances({documentId}:{documentId:string}){
 const {t}=useTranslation()
 const [draft,setDraft]=useState(''),[q,setQ]=useState(''),[cls,setCls]=useState(''),[page,setPage]=useState(1),[size,setSize]=useState(20)
 const [data,setData]=useState<Result>({entities:[],total:0,classes:[]}),[busy,setBusy]=useState(false),[error,setError]=useState('')
 useEffect(()=>{const controller=new AbortController();setBusy(true);setError('');ontologyGraphRequest<Result>('instances',{q,offset:(page-1)*size,limit:size,...(cls?{class_uri:cls}:{}),...(documentId?{document_id:documentId}:{})},{signal:controller.signal}).then(r=>{if(!controller.signal.aborted)setData(r)}).catch(e=>{if(!controller.signal.aborted)setError(String(e))}).finally(()=>{if(!controller.signal.aborted)setBusy(false)});return()=>controller.abort()},[q,cls,page,size,documentId])
 const evidence=(s:Source,i:number)=><OntologyEvidenceDialog key={i} docId={s.doc_id} filename={s.doc_id} initialChunk={s.chunk_id} initialQuote={s.evidence}/>
 return <section className="h-full overflow-auto p-4 space-y-3"><form className="flex gap-2 flex-wrap" onSubmit={e=>{e.preventDefault();setQ(draft);setPage(1)}}><Input className="max-w-sm" value={draft} onChange={e=>setDraft(e.target.value)} placeholder={t("gptonto.searchInstances","Name, ID or attribute")}/><select className="border rounded" value={cls} onChange={e=>{setCls(e.target.value);setPage(1)}}><option value="">{t("gptonto.allTypes","All types")}</option>{data.classes.map(c=><option key={c}>{c}</option>)}</select><Button type="submit">{t("gptonto.search","Search")}</Button></form>
 {error&&<p role="alert" className="text-red-600">{error}</p>}
 <PaginationControls currentPage={page} totalPages={Math.max(1,Math.ceil(data.total/size))} pageSize={size} totalCount={data.total} onPageChange={setPage} onPageSizeChange={s=>{setSize(Math.min(s,100));setPage(1)}} isLoading={busy}/>
 <Table><TableHeader><TableRow><TableHead>{t("gptonto.name","Name")}</TableHead><TableHead>{t("gptonto.type","Type")}</TableHead><TableHead>{t("gptonto.attributes","Attributes")}</TableHead></TableRow></TableHeader><TableBody>{data.entities.map(e=><TableRow key={e.id}><TableCell><b>{e.name}</b><details><summary>{t("gptonto.evidence","Evidence")} ({e.sources.length})</summary>{e.sources.map(evidence)}</details></TableCell><TableCell className="break-all">{e.class_uri}</TableCell><TableCell><details><summary>{e.attributes.length}</summary>{e.attributes.map((a,i)=><div key={i} className="border-b py-2 break-all">{a.property_uri}: {JSON.stringify(a.value)} {evidence(a,i)}</div>)}</details></TableCell></TableRow>)}</TableBody></Table>
 </section>
}
