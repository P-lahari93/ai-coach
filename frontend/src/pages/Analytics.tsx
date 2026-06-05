import { useQuery } from '@tanstack/react-query'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, LineChart, Line } from 'recharts'
import { TrendingUp, Users, MessageSquare, Zap, Loader2 } from 'lucide-react'
import Layout from '@/components/Layout'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth'

export default function Analytics() {
  const user = useAuthStore(s => s.user)

  const { data: dashboard, isLoading } = useQuery({
    queryKey: ['analytics-dashboard'],
    queryFn: () => api.get('/analytics/dashboard').then(r => r.data),
  })

  const { data: sessions } = useQuery({
    queryKey: ['sessions-all'],
    queryFn: () => api.get('/sessions/coaching', { params: { page_size: 100 } }).then(r => r.data),
  })

  // Build score trend from sessions
  const scoreTrend = (sessions?.items || [])
    .filter((s: any) => s.final_score != null && s.status === 'completed')
    .slice(-10)
    .map((s: any, i: number) => ({ session: i + 1, score: Number(s.final_score).toFixed(1) }))

  const stats = [
    { label: 'Sessions Started', value: dashboard?.sessions_started ?? 0, icon: MessageSquare, color: 'text-blue-500' },
    { label: 'Sessions Completed', value: dashboard?.sessions_completed ?? 0, icon: TrendingUp, color: 'text-green-500' },
    { label: 'Active Users', value: dashboard?.active_users ?? 1, icon: Users, color: 'text-purple-500' },
    { label: 'AI Tokens Used', value: dashboard?.total_ai_tokens ?? 0, icon: Zap, color: 'text-orange-500' },
  ]

  return (
    <Layout>
      <div className="space-y-8">
        <div>
          <h1 className="text-2xl font-bold">Analytics</h1>
          <p className="text-muted-foreground mt-1">Your coaching performance overview</p>
        </div>

        {isLoading && <div className="flex justify-center py-16"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div>}

        {/* KPI cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {stats.map(({ label, value, icon: Icon, color }) => (
            <div key={label} className="bg-card border border-border rounded-xl p-5">
              <Icon className={`h-5 w-5 ${color} mb-3`} />
              <div className="text-2xl font-bold">{typeof value === 'number' && value > 9999 ? `${(value / 1000).toFixed(1)}k` : value}</div>
              <div className="text-sm text-muted-foreground mt-0.5">{label}</div>
            </div>
          ))}
        </div>

        {/* Score trend */}
        {scoreTrend.length > 0 && (
          <div className="bg-card border border-border rounded-xl p-6">
            <h2 className="font-semibold mb-6">Score Trend</h2>
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={scoreTrend}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                <XAxis dataKey="session" tick={{ fontSize: 12 }} label={{ value: 'Session', position: 'insideBottom', offset: -2 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} />
                <Tooltip formatter={(v: any) => [`${v}%`, 'Score']} />
                <Line type="monotone" dataKey="score" stroke="hsl(var(--primary))" strokeWidth={2} dot={{ r: 4 }} />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Sessions by status */}
        {sessions?.items?.length > 0 && (
          <div className="bg-card border border-border rounded-xl p-6">
            <h2 className="font-semibold mb-6">Recent Session Scores</h2>
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={scoreTrend}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
                <XAxis dataKey="session" tick={{ fontSize: 12 }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} />
                <Tooltip formatter={(v: any) => [`${v}%`, 'Score']} />
                <Bar dataKey="score" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Completion rate */}
        {dashboard && (
          <div className="bg-card border border-border rounded-xl p-6">
            <h2 className="font-semibold mb-4">Completion Rate</h2>
            <div className="flex items-center gap-4">
              <div className="text-4xl font-bold text-primary">
                {dashboard.sessions_started > 0
                  ? `${((dashboard.sessions_completed / dashboard.sessions_started) * 100).toFixed(0)}%`
                  : '0%'}
              </div>
              <div>
                <div className="text-sm text-muted-foreground">{dashboard.sessions_completed} completed of {dashboard.sessions_started} started</div>
                <div className="mt-2 h-2 w-48 bg-muted rounded-full overflow-hidden">
                  <div className="h-full bg-primary rounded-full"
                    style={{ width: dashboard.sessions_started > 0 ? `${(dashboard.sessions_completed / dashboard.sessions_started) * 100}%` : '0%' }} />
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </Layout>
  )
}
