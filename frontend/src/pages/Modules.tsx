import { useQuery, useMutation } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { BookOpen, Play, Loader2, MessageSquare } from 'lucide-react'
import Layout from '@/components/Layout'
import { modulesApi, sessionsApi } from '@/lib/api'

export default function Modules() {
  const navigate = useNavigate()

  const { data, isLoading } = useQuery({
    queryKey: ['modules'],
    queryFn: () => modulesApi.list({ status: 'published' }).then(r => r.data),
  })

  const startCoaching = useMutation({
    mutationFn: (moduleId: string) => sessionsApi.createCoaching(moduleId),
    onSuccess: (res) => navigate(`/sessions/coaching/${res.data.id}`),
  })

  const startRoleplay = useMutation({
    mutationFn: (moduleId: string) => sessionsApi.createRoleplay(moduleId),
    onSuccess: (res) => navigate(`/sessions/roleplay/${res.data.id}`),
  })

  return (
    <Layout>
      <div className="space-y-6">
        <div>
          <h1 className="text-2xl font-bold">Coaching Modules</h1>
          <p className="text-muted-foreground mt-1">Choose a module to start practicing</p>
        </div>

        {isLoading && <div className="flex justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>}

        {data?.items?.length === 0 && (
          <div className="text-center py-20 text-muted-foreground">
            <BookOpen className="h-12 w-12 mx-auto mb-4 opacity-20" />
            <p>No modules available yet.</p>
          </div>
        )}

        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-5">
          {data?.items?.map((m: any) => (
            <div key={m.id} className="bg-card border border-border rounded-xl p-6 hover:border-primary/50 transition-colors">
              <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center mb-4">
                <BookOpen className="h-5 w-5 text-primary" />
              </div>
              <h3 className="font-semibold mb-1">{m.name}</h3>
              {m.blurb && <p className="text-sm text-muted-foreground mb-4">{m.blurb}</p>}
              <div className="flex gap-2 mt-4">
                <button
                  onClick={() => startCoaching.mutate(m.id)}
                  disabled={startCoaching.isPending}
                  className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-primary text-primary-foreground rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-60">
                  {startCoaching.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                  Coach
                </button>
                <button
                  onClick={() => startRoleplay.mutate(m.id)}
                  disabled={startRoleplay.isPending}
                  className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-secondary text-secondary-foreground rounded-lg text-sm font-medium hover:bg-secondary/80 transition-colors disabled:opacity-60">
                  <MessageSquare className="h-3.5 w-3.5" />
                  Roleplay
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </Layout>
  )
}
