export const ontologyGraphMode = import.meta.env.MODE === 'gptonto'
let accessKey = ''
let documentId = ''
export function configureOntologyGraph(key: string, doc: string) {
  accessKey = key
  documentId = doc
}
export async function ontologyGraphFetch(path:string,params:Record<string,string|number>={},init:RequestInit={}):Promise<Response> {
  const url = new URL('../ontology-api/' + path, window.location.href)
  for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value))
  if (documentId && (path === 'graph' || path === 'graph/labels')) url.searchParams.set('document_id', documentId)
  const headers=new Headers(init.headers);if(accessKey)headers.set('Authorization','Bearer '+accessKey)
  const response=await fetch(url,{...init,headers})
  if (!response.ok) throw new Error('GPTOnto API: '+response.status+' '+(await response.text()).slice(0,500))
  return response
}
export async function ontologyGraphRequest<T>(path:string,params:Record<string,string|number>={},init:RequestInit={}):Promise<T>{
  return (await ontologyGraphFetch(path,params,init)).json()
}
export function ontologyPost<T>(path:string,data?:unknown,signal?:AbortSignal):Promise<T>{
  return ontologyGraphRequest<T>(path,{}, {method:'POST',signal,...(data===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(data)})})
}
export async function ontologyUpload<T>(path:string,form:FormData,onProgress?:(value:number)=>void):Promise<T>{
  // Preserve original axios upload-progress behavior, without legacy auth interceptors.
  const {default:axios}=await import('axios')
  const url=new URL('../ontology-api/'+path,window.location.href)
  const response=await axios.post<T>(url.href,form,{
    headers:accessKey?{Authorization:'Bearer '+accessKey}:{},
    onUploadProgress:e=>{if(e.total)onProgress?.(Math.min(100,Math.round(e.loaded*100/e.total)))}
  })
  return response.data
}
export async function ontologyDownload(path:string,filename:string,params:Record<string,string|number>={}){
  const response=await ontologyGraphFetch(path,params);const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=filename;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
}
