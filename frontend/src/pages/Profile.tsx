import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { User, Award, TrendingUp, Edit2, Check, X, Loader2, Flame } from 'lucide-react'
import Layout from '@/components/Layout'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth'

export default function Profile() {
  const { user, setAuth, accessToken, refreshToken } = useAuthStore()
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [fullName, setFullName] = useState(user?.full_name || '')

  const { data: progress } = useQuery({
    queryKey: ['progress'],
    queryFn: () => api.get('/progress/').then(r => r.data as any[]),
  })

  const { data: sessions } = useQuery({
    queryKey: ['sessions-profile'],
    queryFn: () => api.get('/sessions/coaching', { params: { page_size: 5, status: 'completed' } }).then(r => r.data),
  })

  const updateMutation = useMutation({
    mutationFn: (name: string) => api.patch('/users/me', { full_name: name }),
    onSuccess: async () => {
      const { data } = await api.get('/auth/me')
      if (accessToken && refreshToken) setAuth(data, accessToken, refreshToken)
      setEditing(false)
      qc.invalidateQueries({ queryKey: ['me'] })
    },
  })

  const totalSessions = sessions?.total || 0
  const avgScore = progress?.length
    ? (progress.reduce((a: number, p: any) => a + (p.best_score || 0), 0) / progress.length).toFixed(0)
    : '0'
  const maxStreak = progress?.reduce((a: number, p: any) => Math.max(a, p.streak_days), 0) || 0

  return (
    <Layout>
      <div className="max-w-2xl mx-auto space-y-6">
        {/* Profile card */}
        <div className="bg-card border border-border rounded-2xl p-8">
          <div className="flex items-start gap-6">
            <div className="w-20 h-20 rounded-2xl bg-primary/10 flex items-center justify-center text-3xl font-bold text-primary flex-shrink-0">
              {user?.full_name?.[0]?.toUpperCase() || 'U'}
            </div>
            <div className="flex-1 min-w-0">
              {editing ? (
                <div className="flex items-center gap-2 mb-1">
                  <input
                    value={fullName}
                    onChange={e => setFullName(e.target.value)}
                    className="flex-1 bg-muted rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
                    autoFocus
                  />
                  <button onClick={() => updateMutation.mutate(fullName)} disabled={updateMutation.isPending}
                    className="p-2 bg-primary text-primary-foreground rounded-lg hover:bg-primary/90">
                    {updateMutation.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                  </button>
                  <button onClick={() => { setEditing(false); setFullName(user?.full_name || '') }}
                    className="p-2 hover:bg-muted rounded-lg">
                    <X className="h-4 w-4" />
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-2 mb-1">
                  <h1 className="text-2xl font-bold">{user?.full_name}</h1>
                  <button onClick={() => setEditing(true)} className="p-1.5 hover:bg-muted rounded-lg">
                    <Edit2 className="h-4 w-4 text-muted-foreground" />
                  </button>
                </div>
              )}
              <p className="text-muted-foreground">{user?.email}</p>
              {user?.is_superadmin && (
                <span className="inline-block mt-2 text-xs px-2 py-0.5 bg-orange-500/10 text-orange-500 rounded-full font-medium">Superadmin</span>
              )}
            </div>
          </div>

          <div className="grid grid-cols-3 gap-4 mt-8 pt-8 border-t border-border">
            {[
              { icon: TrendingUp, label: 'Sessions', value: totalSessions, color: 'text-blue-500' },
              { icon: Award, label: 'Avg Best Score', value: `${avgScore}%`, color: 'text-yellow-500' },
              { icon: Flame, label: 'Max Streak', value: `${maxStreak}d`, color: 'text-orange-500' },
            ].map(({ icon: Icon, label, value, color }) => (
              <div key={label} className="text-center">
                <Icon className={`h-5 w-5 ${color} mx-auto mb-1.5`} />
                <div className="text-2xl font-bold">{value}</div>
                <div className="text-xs text-muted-foreground">{label}</div>
              </div>
            ))}
          </div>
        </div>

        {/* Progress by module */}
        {progress && progress.length > 0 && (
          <div className="bg-card border border-border rounded-xl p-6">
            <h2 className="font-semibold mb-4">Module Progress</h2>
            <div className="space-y-4">
              {progress.map((p: any) => (
                <div key={p.id}>
                  <div className="flex justify-between text-sm mb-1.5">
                    <span className="font-medium truncate">Module {p.module_id?.slice(0, 8)}...</span>
                    <span className="text-muted-foreground ml-2 flex-shrink-0">{Number(p.completion_percent).toFixed(0)}%</span>
                  </div>
                  <div className="h-2 bg-muted rounded-full overflow-hidden">
                    <div className="h-full bg-primary rounded-full transition-all" style={{ width: `${p.completion_percent}%` }} />
                  </div>
                  <div className="flex gap-4 mt-1.5 text-xs text-muted-foreground">
                    <span>{p.sessions_completed} sessions</span>
                    {p.best_score && <span>Best: {Number(p.best_score).toFixed(0)}%</span>}
                    <span>{p.streak_days}d streak</span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Recent completed sessions */}
        {sessions?.items?.length > 0 && (
          <div className="bg-card border border-border rounded-xl p-6">
            <h2 className="font-semibold mb-4">Recent Completed Sessions</h2>
            <div className="space-y-3">
              {sessions.items.map((s: any) => (
                <div key={s.id} className="flex items-center justify-between py-2 border-b border-border last:border-0">
                  <div className="text-sm">
                    <div className="font-medium">{new Date(s.created_at).toLocaleDateString()}</div>
                    <div className="text-muted-foreground text-xs">{s.duration_seconds ? `${Math.round(s.duration_seconds / 60)} min` : 'N/A'}</div>
                  </div>
                  {s.final_score != null && (
                    <div className={`text-lg font-bold ${Number(s.final_score) >= 80 ? 'text-green-500' : Number(s.final_score) >= 60 ? 'text-yellow-500' : 'text-red-500'}`}>
                      {Number(s.final_score).toFixed(0)}%
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </Layout>
  )
}
