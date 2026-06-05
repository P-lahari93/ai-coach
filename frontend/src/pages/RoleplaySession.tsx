import { useState, useRef, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Send, Loader2, Bot, User, X, CheckCircle } from 'lucide-react'
import Layout from '@/components/Layout'
import { sessionsApi } from '@/lib/api'

interface Message { role: 'user' | 'persona'; content: string; turn: number }

export default function RoleplaySession() {
  const { sessionId } = useParams<{ sessionId: string }>()
  const navigate = useNavigate()
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const [done, setDone] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)

  const { data: session } = useQuery({
    queryKey: ['roleplay-session', sessionId],
    queryFn: () => sessionsApi.getRoleplay(sessionId!).then(r => r.data),
    enabled: !!sessionId,
  })

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const send = async () => {
    if (!input.trim() || sending) return
    const text = input.trim()
    setInput('')
    setSending(true)
    setMessages(m => [...m, { role: 'user', content: text, turn: m.length + 1 }])
    try {
      const { data } = await sessionsApi.submitTurn(sessionId!, text)
      setMessages(m => [...m, { role: 'persona', content: data.persona_content, turn: data.turn_number }])
    } catch {
      setMessages(m => [...m, { role: 'persona', content: 'Sorry, I had trouble responding. Please try again.', turn: m.length }])
    } finally {
      setSending(false)
    }
  }

  const handleComplete = async () => {
    await sessionsApi.completeRoleplay(sessionId!)
    setDone(true)
  }

  if (done) return (
    <Layout>
      <div className="max-w-lg mx-auto text-center py-20">
        <CheckCircle className="h-14 w-14 text-green-500 mx-auto mb-5" />
        <h2 className="text-2xl font-bold mb-3">Roleplay Complete!</h2>
        <p className="text-muted-foreground mb-8">Great practice session. Coaching notes will appear in your feedback.</p>
        <button onClick={() => navigate('/dashboard')} className="px-6 py-3 bg-primary text-primary-foreground rounded-lg font-medium">
          Back to dashboard
        </button>
      </div>
    </Layout>
  )

  return (
    <Layout>
      <div className="max-w-3xl mx-auto flex flex-col h-[calc(100vh-8rem)]">
        {/* Header */}
        <div className="flex items-center justify-between pb-4 border-b border-border">
          <div>
            <h1 className="font-bold">Roleplay Session</h1>
            {session?.scenario_prompt && (
              <p className="text-sm text-muted-foreground mt-0.5">{session.scenario_prompt}</p>
            )}
          </div>
          <div className="flex gap-2">
            <button onClick={handleComplete} className="px-4 py-2 text-sm bg-green-600 text-white rounded-lg hover:bg-green-500 transition-colors">
              End session
            </button>
            <button onClick={() => navigate('/modules')} className="p-2 hover:bg-accent rounded-lg">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Messages */}
        <div className="flex-1 overflow-y-auto py-6 space-y-4">
          {messages.length === 0 && (
            <div className="text-center py-10 text-muted-foreground text-sm">
              <Bot className="h-10 w-10 mx-auto mb-3 opacity-30" />
              <p>Start the conversation. The AI persona will respond in character.</p>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`flex gap-3 ${m.role === 'user' ? 'flex-row-reverse' : ''}`}>
              <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${m.role === 'user' ? 'bg-primary text-primary-foreground' : 'bg-muted'}`}>
                {m.role === 'user' ? <User className="h-4 w-4" /> : <Bot className="h-4 w-4" />}
              </div>
              <div className={`max-w-[75%] px-4 py-3 rounded-2xl text-sm ${m.role === 'user' ? 'bg-primary text-primary-foreground rounded-tr-sm' : 'bg-muted rounded-tl-sm'}`}>
                {m.content}
              </div>
            </div>
          ))}
          {sending && (
            <div className="flex gap-3">
              <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center">
                <Bot className="h-4 w-4" />
              </div>
              <div className="bg-muted rounded-2xl rounded-tl-sm px-4 py-3">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div className="border-t border-border pt-4">
          <div className="flex gap-3">
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
              placeholder="Type your message..."
              className="flex-1 bg-muted rounded-xl px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-primary"
            />
            <button onClick={send} disabled={!input.trim() || sending}
              className="px-4 py-3 bg-primary text-primary-foreground rounded-xl hover:bg-primary/90 disabled:opacity-50 transition-colors">
              <Send className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </Layout>
  )
}
