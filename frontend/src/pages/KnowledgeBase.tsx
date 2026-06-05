import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Database, Plus, Upload, FileText, Trash2, Loader2, ChevronDown, ChevronUp, Check, X } from 'lucide-react'
import Layout from '@/components/Layout'
import { knowledgeApi } from '@/lib/api'

export default function KnowledgeBase() {
  const qc = useQueryClient()
  const [selectedKb, setSelectedKb] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [newKbName, setNewKbName] = useState('')
  const [addingText, setAddingText] = useState(false)
  const [textTitle, setTextTitle] = useState('')
  const [textContent, setTextContent] = useState('')

  const { data: kbs, isLoading } = useQuery({
    queryKey: ['knowledge-bases'],
    queryFn: () => knowledgeApi.list().then(r => r.data),
  })

  const { data: sources } = useQuery({
    queryKey: ['sources', selectedKb],
    queryFn: () => knowledgeApi.listSources(selectedKb!).then(r => r.data),
    enabled: !!selectedKb,
  })

  const createKb = useMutation({
    mutationFn: (name: string) => knowledgeApi.create(name),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['knowledge-bases'] }); setCreating(false); setNewKbName('') },
  })

  const deleteKb = useMutation({
    mutationFn: (id: string) => knowledgeApi.delete(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['knowledge-bases'] }); setSelectedKb(null) },
  })

  const addText = useMutation({
    mutationFn: () => knowledgeApi.addText(selectedKb!, textTitle, textContent),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['sources', selectedKb] }); setAddingText(false); setTextTitle(''); setTextContent('') },
  })

  const deleteSource = useMutation({
    mutationFn: ({ kbId, srcId }: { kbId: string; srcId: string }) => knowledgeApi.deleteSource(kbId, srcId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['sources', selectedKb] }),
  })

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!selectedKb || !e.target.files?.[0]) return
    const file = e.target.files[0]
    const form = new FormData()
    form.append('file', file)
    await fetch(`/api/v1/knowledge/${selectedKb}/sources/upload`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${localStorage.getItem('access_token')}` },
      body: form,
    })
    qc.invalidateQueries({ queryKey: ['sources', selectedKb] })
    e.target.value = ''
  }

  return (
    <Layout>
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold">Knowledge Base</h1>
            <p className="text-muted-foreground mt-1">Upload company knowledge to power AI coaching</p>
          </div>
          <button onClick={() => setCreating(true)}
            className="flex items-center gap-2 px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors">
            <Plus className="h-4 w-4" /> New KB
          </button>
        </div>

        {/* Create KB form */}
        {creating && (
          <div className="bg-card border border-border rounded-xl p-5">
            <h3 className="font-medium mb-3">Create Knowledge Base</h3>
            <div className="flex gap-3">
              <input value={newKbName} onChange={e => setNewKbName(e.target.value)}
                placeholder="Knowledge base name..."
                className="flex-1 bg-muted rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary" />
              <button onClick={() => createKb.mutate(newKbName)} disabled={!newKbName.trim() || createKb.isPending}
                className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm disabled:opacity-50 hover:bg-primary/90">
                {createKb.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
              </button>
              <button onClick={() => setCreating(false)} className="px-3 py-2 hover:bg-muted rounded-lg">
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}

        {isLoading && <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>}

        {kbs?.items?.length === 0 && !isLoading && (
          <div className="text-center py-20 text-muted-foreground">
            <Database className="h-12 w-12 mx-auto mb-4 opacity-20" />
            <p>No knowledge bases yet. Create one to upload documents.</p>
          </div>
        )}

        {/* KB list */}
        <div className="space-y-3">
          {kbs?.items?.map((kb: any) => (
            <div key={kb.id} className="bg-card border border-border rounded-xl overflow-hidden">
              <button
                className="w-full flex items-center justify-between px-6 py-4 hover:bg-muted/50 transition-colors text-left"
                onClick={() => setSelectedKb(selectedKb === kb.id ? null : kb.id)}>
                <div className="flex items-center gap-3">
                  <Database className="h-5 w-5 text-primary" />
                  <div>
                    <div className="font-medium">{kb.name}</div>
                    <div className="text-xs text-muted-foreground mt-0.5">{kb.chunk_count} chunks · {kb.scope}</div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <button onClick={e => { e.stopPropagation(); deleteKb.mutate(kb.id) }}
                    className="p-1.5 hover:bg-destructive/10 hover:text-destructive rounded-lg transition-colors text-muted-foreground">
                    <Trash2 className="h-4 w-4" />
                  </button>
                  {selectedKb === kb.id ? <ChevronUp className="h-4 w-4 text-muted-foreground" /> : <ChevronDown className="h-4 w-4 text-muted-foreground" />}
                </div>
              </button>

              {selectedKb === kb.id && (
                <div className="border-t border-border px-6 py-4 space-y-4">
                  {/* Action buttons */}
                  <div className="flex gap-2">
                    <button onClick={() => setAddingText(!addingText)}
                      className="flex items-center gap-2 px-3 py-2 bg-secondary text-secondary-foreground rounded-lg text-sm hover:bg-secondary/80 transition-colors">
                      <FileText className="h-4 w-4" /> Add text
                    </button>
                    <label className="flex items-center gap-2 px-3 py-2 bg-secondary text-secondary-foreground rounded-lg text-sm hover:bg-secondary/80 transition-colors cursor-pointer">
                      <Upload className="h-4 w-4" /> Upload file
                      <input type="file" accept=".pdf,.docx,.pptx,.txt,.md" className="hidden" onChange={handleFileUpload} />
                    </label>
                  </div>

                  {/* Add text form */}
                  {addingText && (
                    <div className="space-y-3 bg-muted/50 rounded-lg p-4">
                      <input value={textTitle} onChange={e => setTextTitle(e.target.value)} placeholder="Source title..."
                        className="w-full bg-background rounded-lg px-3 py-2 text-sm border border-border focus:outline-none focus:ring-2 focus:ring-primary" />
                      <textarea value={textContent} onChange={e => setTextContent(e.target.value)} placeholder="Paste your content here..." rows={4}
                        className="w-full bg-background rounded-lg px-3 py-2 text-sm border border-border focus:outline-none focus:ring-2 focus:ring-primary resize-none" />
                      <div className="flex gap-2">
                        <button onClick={() => addText.mutate()} disabled={!textTitle.trim() || !textContent.trim() || addText.isPending}
                          className="px-4 py-2 bg-primary text-primary-foreground rounded-lg text-sm disabled:opacity-50 hover:bg-primary/90">
                          {addText.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : 'Add source'}
                        </button>
                        <button onClick={() => setAddingText(false)} className="px-3 py-2 hover:bg-muted rounded-lg text-sm">Cancel</button>
                      </div>
                    </div>
                  )}

                  {/* Sources list */}
                  {sources?.items?.length === 0 && <p className="text-sm text-muted-foreground">No sources yet.</p>}
                  <div className="space-y-2">
                    {sources?.items?.map((s: any) => (
                      <div key={s.id} className="flex items-center justify-between py-2 border-b border-border last:border-0">
                        <div>
                          <div className="text-sm font-medium">{s.title}</div>
                          <div className="text-xs text-muted-foreground">{s.type} · {s.chunk_count} chunks ·
                            <span className={`ml-1 ${s.status === 'completed' ? 'text-green-500' : s.status === 'failed' ? 'text-red-500' : 'text-yellow-500'}`}>
                              {s.status}
                            </span>
                          </div>
                        </div>
                        <button onClick={() => deleteSource.mutate({ kbId: kb.id, srcId: s.id })}
                          className="p-1.5 hover:bg-destructive/10 hover:text-destructive rounded-lg transition-colors text-muted-foreground">
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </Layout>
  )
}
