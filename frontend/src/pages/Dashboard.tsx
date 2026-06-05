import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { TrendingUp, Award, MessageSquare, Flame, BookOpen, ArrowRight, Loader2 } from 'lucide-react'
import Layout from '@/components/Layout'
import { progressApi, sessionsApi } from '@/lib/api'
import { useAuthStore } from '@/stores/auth'

export default function Dashboard() {
  const user = useAuthStore((s) => s.user)

  const { data: progress } = useQuery({
    queryKey: ['progress'],
    queryFn: () => progressApi.list().then(r => r.data as any[]),
  })

  const { data: sessions } = useQuery({
    queryKey: ['sessions', 'coaching'],
    queryFn: () => sessionsApi.listCoaching({ page_size: 5 }).then(r => r.data),
  })

  const totalSessions = sessions?.total || 0
  const completed = progress?.reduce((a: number, p: any) => a + p.sessions_completed, 0) || 0
  const bestScore = progress?.reduce((a: number, p: any) => Math.max(a, p.best_score || 0), 0) || 0
  const maxStreak = progress?.reduce((a: number, p: any) => Math.max(a, p.streak_days), 0) || 0

  return (
    <Layout>
      <div className="space-y-8">
        <div>
          <h1 className="text-2xl font-bold">Good to see you, {user?.full_name?.split(' ')[0]} 👋</h1>
          <p className="text-muted-foreground mt-1">Here's your coaching overview</p>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {[
            { icon: MessageSquare, label: 'Total Sessions', value: totalSessions, color: 'text-blue-500' },
            { icon: TrendingUp, label: 'Completed', value: completed, color: 'text-green-500' },
            { icon: Award, label: 'Best Score', value: `${bestScore.toFixed(0)}%`, color: 'text-yellow-500' },
            { icon: Flame, label: 'Max Streak', value: `${maxStreak}d`, color: 'text-orange-500' },
          ].map(({ icon: Icon, label, value, color }) => (
            <div key={label} className="bg-card border border-border rounded-xl p-5">
              <Icon className={`h-5 w-5 ${color} mb-3`} />
              <div className="text-2xl font-bold">{value}</div>
              <div className="text-sm text-muted-foreground mt-0.5">{label}</div>
            </div>
          ))}
        </div>

        {/* Recent sessions */}
        <div className="bg-card border border-border rounded-xl">
          <div className="flex items-center justify-between px-6 py-4 border-b border-border">
            <h2 className="font-semibold">Recent Sessions</h2>
            <Link to="/modules" className="text-sm text-primary flex items-center gap-1 hover:gap-2 transition-all">
              Start new <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>
          <div className="divide-y divide-border">
            {!sessions && <div className="flex justify-center py-10"><Loader2 className="h-5 w-5 animate-spin text-muted-foreground" /></div>}
            {sessions?.items?.length === 0 && (
              <div className="text-center py-10 text-muted-foreground">
                <BookOpen className="h-8 w-8 mx-auto mb-3 opacity-30" />
                <p className="text-sm">No sessions yet. Start your first coaching session.</p>
              </div>
            )}
            {sessions?.items?.map((s: any) => (
              <div key={s.id} className="flex items-center justify-between px-6 py-4">
                <div>
                  <div className="font-medium text-sm">{s.module_id}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">{new Date(s.created_at).toLocaleDateString()}</div>
                </div>
                <div className="flex items-center gap-3">
                  {s.final_score != null && (
                    <span className="text-sm font-semibold">{s.final_score.toFixed(0)}%</span>
                  )}
                  <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                    s.status === 'completed' ? 'bg-green-500/10 text-green-600' :
                    s.status === 'in_progress' ? 'bg-blue-500/10 text-blue-600' :
                    'bg-slate-500/10 text-slate-500'
                  }`}>{s.status}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Layout>
  )
}
