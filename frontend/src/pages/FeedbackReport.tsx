import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { CheckCircle, TrendingUp, AlertCircle, Star, ArrowLeft, Loader2, BookOpen } from 'lucide-react'
import Layout from '@/components/Layout'
import { feedbackApi } from '@/lib/api'

export default function FeedbackReport() {
  const { reportId } = useParams<{ reportId: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const { data: report, isLoading } = useQuery({
    queryKey: ['feedback', reportId],
    queryFn: () => feedbackApi.get(reportId!).then(r => r.data),
    enabled: !!reportId,
  })

  const rateMutation = useMutation({
    mutationFn: (rating: number) => feedbackApi.rate(reportId!, rating),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['feedback', reportId] }),
  })

  if (isLoading) return <Layout><div className="flex justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div></Layout>
  if (!report) return <Layout><div className="text-center py-20 text-muted-foreground">Report not found</div></Layout>

  const score = Number(report.overall_score)
  const scoreColor = score >= 80 ? 'text-green-500' : score >= 60 ? 'text-yellow-500' : 'text-red-500'

  return (
    <Layout>
      <div className="max-w-2xl mx-auto space-y-6">
        <button onClick={() => navigate(-1)} className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors">
          <ArrowLeft className="h-4 w-4" /> Back
        </button>

        {/* Score card */}
        <div className="bg-card border border-border rounded-2xl p-8 text-center">
          <div className={`text-7xl font-bold mb-2 ${scoreColor}`}>{score.toFixed(0)}</div>
          <div className="text-muted-foreground text-lg mb-5">Overall Score</div>
          <div className="h-3 bg-muted rounded-full overflow-hidden max-w-sm mx-auto">
            <div className={`h-full rounded-full transition-all ${score >= 80 ? 'bg-green-500' : score >= 60 ? 'bg-yellow-500' : 'bg-red-500'}`}
              style={{ width: `${score}%` }} />
          </div>
        </div>

        {/* Feedback text */}
        <div className="bg-card border border-border rounded-xl p-6">
          <h2 className="font-semibold mb-3">AI Feedback</h2>
          <p className="text-sm text-muted-foreground leading-relaxed">{report.feedback_text}</p>
        </div>

        {/* Strengths */}
        {report.strengths?.length > 0 && (
          <div className="bg-green-50 dark:bg-green-950/20 border border-green-200 dark:border-green-800 rounded-xl p-6">
            <div className="flex items-center gap-2 mb-3">
              <CheckCircle className="h-5 w-5 text-green-600" />
              <h2 className="font-semibold text-green-800 dark:text-green-300">Strengths</h2>
            </div>
            <ul className="space-y-1.5">
              {report.strengths.map((s: string, i: number) => (
                <li key={i} className="text-sm text-green-700 dark:text-green-400 flex items-start gap-2">
                  <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-green-500 flex-shrink-0" />
                  {s}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Improvements */}
        {report.improvements?.length > 0 && (
          <div className="bg-orange-50 dark:bg-orange-950/20 border border-orange-200 dark:border-orange-800 rounded-xl p-6">
            <div className="flex items-center gap-2 mb-3">
              <TrendingUp className="h-5 w-5 text-orange-600" />
              <h2 className="font-semibold text-orange-800 dark:text-orange-300">Areas to Improve</h2>
            </div>
            <ul className="space-y-1.5">
              {report.improvements.map((s: string, i: number) => (
                <li key={i} className="text-sm text-orange-700 dark:text-orange-400 flex items-start gap-2">
                  <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-orange-500 flex-shrink-0" />
                  {s}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Recommendations */}
        {report.recommendations?.length > 0 && (
          <div className="bg-card border border-border rounded-xl p-6">
            <h2 className="font-semibold mb-4">Recommendations</h2>
            <div className="space-y-4">
              {report.recommendations.map((r: any) => (
                <div key={r.priority} className="flex gap-4">
                  <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center text-xs font-bold text-primary flex-shrink-0">{r.priority}</div>
                  <div>
                    <div className="font-medium text-sm">{r.area}</div>
                    <p className="text-sm text-muted-foreground mt-0.5">{r.suggestion}</p>
                    {r.example && <p className="text-xs text-muted-foreground/70 mt-1.5 italic">{r.example}</p>}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Citations */}
        {report.knowledge_used && report.citations?.length > 0 && (
          <div className="bg-card border border-border rounded-xl p-6">
            <div className="flex items-center gap-2 mb-3">
              <BookOpen className="h-4 w-4 text-muted-foreground" />
              <h2 className="font-semibold text-sm">Sources Used</h2>
            </div>
            <div className="space-y-2">
              {report.citations.map((c: any, i: number) => (
                <div key={i} className="text-xs bg-muted rounded-lg p-3">
                  <div className="font-medium mb-0.5">{c.source_title}</div>
                  <div className="text-muted-foreground">{c.snippet}</div>
                  <div className="text-muted-foreground mt-1">{(c.relevance * 100).toFixed(0)}% relevance</div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Rating */}
        <div className="bg-card border border-border rounded-xl p-6">
          <h2 className="font-semibold mb-3">Rate this feedback</h2>
          <div className="flex gap-2">
            {[1, 2, 3, 4, 5].map(n => (
              <button key={n} onClick={() => rateMutation.mutate(n)}
                className={`p-2 rounded-lg transition-colors ${report.user_rating === n ? 'text-yellow-500' : 'text-muted-foreground hover:text-yellow-500'}`}>
                <Star className="h-6 w-6" fill={report.user_rating && report.user_rating >= n ? 'currentColor' : 'none'} />
              </button>
            ))}
          </div>
        </div>
      </div>
    </Layout>
  )
}
