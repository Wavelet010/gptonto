import { useState } from 'react'
import '@/lib/extensions'
import { Toaster } from 'sonner'
import ThemeProvider from '@/components/ThemeProvider'
import GraphViewer from '@/features/GraphViewer'
import { useSettingsStore } from '@/stores/settings'
import { useGraphStore } from '@/stores/graph'
import { configureOntologyGraph, ontologyGraphRequest } from '@/lib/ontologyGraph'

export default function OntologyGraphApp() {
  const [key, setKey] = useState('')
  const [documentId, setDocumentId] = useState('')
  const [documents, setDocuments] = useState<{id:string;filename:string}[]>([])
  const [ready, setReady] = useState(false)
  const [error, setError] = useState('')
  const [busy,setBusy] = useState(false)
  const [revision,setRevision] = useState(0)
  const connect = async (doc: string) => {
    setBusy(true); setError('')
    try {
      configureOntologyGraph(key,doc)
      const list = await ontologyGraphRequest<{id:string;filename:string}[]>('documents')
      setDocuments(list)
      setDocumentId(doc)
      useGraphStore.getState().reset()
      useSettingsStore.getState().setCurrentTab('knowledge-graph')
      useSettingsStore.getState().setBackendMaxGraphNodes(1000)
      useSettingsStore.getState().setQueryLabel('*')
      setReady(true)
      setRevision(x=>x+1)
    } catch(e) { setReady(false);setError(String(e)) }
    finally { setBusy(false) }
  }
  return <ThemeProvider>
    <main className="h-screen flex flex-col overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b p-3">
        <a className="underline" href="../">返回 GPTOnto</a>
        <strong>原版 LightRAG 图谱 · 本体知识（只读）</strong>
        <input className="border rounded px-2 py-1" type="password" value={key} onChange={e=>setKey(e.target.value)} placeholder="API Key（如启用）" />
        <button className="border rounded px-3 py-1" disabled={busy} onClick={()=>connect(documentId)}>连接 / 刷新</button>
        {ready && <select className="border rounded px-2 py-1" value={documentId} onChange={e=>connect(e.target.value)} disabled={busy}><option value="">全部文档</option>{documents.map(d=><option key={d.id} value={d.id}>{d.filename}</option>)}</select>}
        {error && <span role="alert" className="text-red-600">{error}</span>}
      </header>
      <div className="relative flex-1 min-h-0">{ready ? <GraphViewer key={revision}/> : <p className="p-6">输入服务密钥（如已配置）并连接图谱。原有问答页面不会加载。</p>}</div>
    </main>
    <Toaster position="bottom-center" richColors closeButton/>
  </ThemeProvider>
}
