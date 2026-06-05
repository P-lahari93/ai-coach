import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Send, Loader2, CheckCircle, X } from 'lucide-react'
import Layout from '@/components/Layout'
import { sessionsApi } from '@/lib/api'

interface IntakeField {
  field_key: string
  label: string
  type: 'text' | 'longtext' | 'voice'
  required: boolean
  placeholder?: string
}

export default function CoachingSession() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const [intakeData, setIntakeData] = useState<Record<string, string>>({})
  const [submitting, setSubmitting] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [feedbackId, setFeedbackId] = useState<string | null>(null)

  const { data: session, isLoading } = useQuery({
    queryKey: ['coaching-session', sessionId],
    queryFn: () => sessionsApi.getCoaching(sessionId!).then(r => r.data),
    enabled: !!sessionId,
  })

  // Use intake_schema from backend if available, else fallback to SBI defaults
  const intakeFields: IntakeField[] = session?.intake_schema?.length
    ? session.intake_schema
    : [
        { field_key: 'situation', label: 'Situation', type: 'longtext', required: true, placeholder: 'Describe the specific situation that occurred...' },
        { field_key: 'behaviour', label: 'Behaviour', type: 'longtext', required: true, placeholder: 'What specific behaviour did you observe?' },
        { field_key: 'impact', label: 'Impact', type: 'longtext', required: true, placeholder: 'What was the impact of that behaviour?' },
      ]

  const frameworkName = session?.framework_name || 'SBI'

  const allRequiredFilled = intakeFields
    .filter(f => f.required)
    .every(f => intakeData[f.field_key]?.trim())

  const handleComplete = async () => {
    if (!allRequiredFilled) return
    setSubmitting(true)
    try {
      const { data } = await sessionsApi.completeCoaching(sessionId!, intakeData)
      const reportId = data?.feedback_report_id || null
      setFeedbackId(reportId)
      setCompleted(true)
      if (reportId) {
        navigate(`/feedback/${reportId}`)
      }
    } catch (e) {
      console.error(e)
    } finally {
      setSubmitting(false)
    }
  }

  const handleAbandon = async () => {
    await sessionsApi.abandonCoaching(sessionId!)
    navigate('/modules')
  }

  if (isLoading) return (
    <Layout><div className="flex justify-center py-20"><Loader2 className="h-6 w-6 animate-spin text-muted-foreground" /></div></Layout>
  )

  if (completed) return (
    <Layout>
      <div className="max-w-2xl mx-auto text-center py-20">
        <CheckCircle className="h-16 w-16 text-green-500 mx-auto mb-6" />
        <h2 className="text-2xl font-bold mb-3">Session Complete!</h2>
        <p className="text-muted-foreground mb-8">
          {feedbackId ? 'Your AI feedback is ready.' : 'Your AI feedback is being generated. Check your feedback reports shortly.'}
        </p>
        <div className="flex gap-4 justify-center flex-wrap">
          {feedbackId && (
            <button
              onClick={() => navigate(`/feedback/${feedbackId}`)}
              className="px-6 py-3 bg-green-600 hover:bg-green-500 text-white rounded-lg font-medium transition-colors"
            >
              View My Feedback
            </button>
          )}
          <button onClick={() => navigate('/dashboard')} className="px-6 py-3 bg-primary text-primary-foreground rounded-lg font-medium hover:bg-primary/90 transition-colors">
            Back to dashboard
          </button>
          <button onClick={() => navigate('/modules')} className="px-6 py-3 bg-secondary text-secondary-foreground rounded-lg font-medium hover:bg-secondary/80 transition-colors">
            Start another session
          </button>
        </div>
      </div>
    </Layout>
  )

  return (
    <Layout>
      <div className="max-w-2xl mx-auto space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold">Coaching Session</h1>
            <p className="text-sm text-muted-foreground mt-0.5">{frameworkName} Framework</p>
          </div>
          <button onClick={handleAbandon} className="p-2 hover:bg-destructive/10 rounded-lg transition-colors text-muted-foreground hover:text-destructive">
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800 rounded-xl p-5 text-sm text-blue-800 dark:text-blue-200">
          <p className="font-medium mb-1">{frameworkName} Framework</p>
          <p className="text-blue-700 dark:text-blue-300">
            Fill in each field below. Be specific and objective — better detail leads to more useful AI feedback.
          </p>
        </div>

        {/* Dynamic intake form from intake_schema */}
        <div className="space-y-4">
          {intakeFields.map((field) => (
            <div key={field.field_key} className="bg-card border border-border rounded-xl p-5">
              <label className="block text-sm font-semibold mb-2">
                {field.label}
                {field.required && <span className="text-red-500 ml-1">*</span>}
              </label>
              {field.type === 'longtext' ? (
                <textarea
                  rows={3}
                  value={intakeData[field.field_key] || ''}
                  onChange={e => setIntakeData(prev => ({ ...prev, [field.field_key]: e.target.value }))}
                  placeholder={field.placeholder || `Enter ${field.label.toLowerCase()}...`}
                  className="w-full bg-transparent text-sm placeholder-muted-foreground resize-none focus:outline-none"
                />
              ) : (
                <input
                  type="text"
                  value={intakeData[field.field_key] || ''}
                  onChange={e => setIntakeData(prev => ({ ...prev, [field.field_key]: e.target.value }))}
                  placeholder={field.placeholder || `Enter ${field.label.toLowerCase()}...`}
                  className="w-full bg-transparent text-sm placeholder-muted-foreground focus:outline-none"
                />
              )}
            </div>
          ))}
        </div>

        <button
          onClick={handleComplete}
          disabled={submitting || !allRequiredFilled}
          className="w-full flex items-center justify-center gap-2 px-6 py-4 bg-primary text-primary-foreground rounded-xl font-semibold hover:bg-primary/90 transition-colors disabled:opacity-50">
          {submitting ? <Loader2 className="h-5 w-5 animate-spin" /> : <Send className="h-5 w-5" />}
          {submitting ? 'Generating feedback...' : 'Submit for AI feedback'}
        </button>
      </div>
    </Layout>
  )
}
