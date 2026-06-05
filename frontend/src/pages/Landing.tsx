import { Link } from 'react-router-dom'
import { Brain, MessageSquare, TrendingUp, Award, BookOpen, Zap } from 'lucide-react'

export default function Landing() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-900 via-blue-950 to-slate-900 text-white">
      {/* Nav */}
      <nav className="flex items-center justify-between px-8 py-6 max-w-7xl mx-auto">
        <div className="flex items-center gap-2 text-xl font-bold">
          <Brain className="h-7 w-7 text-blue-400" />
          <span>AI Coach</span>
        </div>
        <div className="flex gap-4">
          <Link to="/login" className="px-4 py-2 text-sm text-slate-300 hover:text-white transition-colors">Sign in</Link>
          <Link to="/register" className="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 rounded-lg transition-colors font-medium">Get started</Link>
        </div>
      </nav>

      {/* Hero */}
      <section className="max-w-7xl mx-auto px-8 pt-24 pb-20 text-center">
        <div className="inline-flex items-center gap-2 bg-blue-500/10 border border-blue-500/20 rounded-full px-4 py-1.5 text-sm text-blue-300 mb-8">
          <Zap className="h-3.5 w-3.5" />
          AI-powered coaching, built for real growth
        </div>
        <h1 className="text-5xl md:text-7xl font-bold leading-tight mb-6">
          Practice the conversations<br />
          <span className="text-blue-400">that define your career</span>
        </h1>
        <p className="text-xl text-slate-400 max-w-2xl mx-auto mb-10">
          AI Coach helps you master feedback delivery, difficult conversations, and leadership skills through guided practice with personalized AI feedback.
        </p>
        <div className="flex flex-col sm:flex-row gap-4 justify-center">
          <Link to="/register" className="px-8 py-4 bg-blue-600 hover:bg-blue-500 rounded-xl text-lg font-semibold transition-all hover:scale-105 shadow-lg shadow-blue-500/25">
            Start for free
          </Link>
          <Link to="/login" className="px-8 py-4 bg-white/10 hover:bg-white/20 rounded-xl text-lg font-semibold transition-colors border border-white/20">
            Sign in
          </Link>
        </div>
      </section>

      {/* Features */}
      <section className="max-w-7xl mx-auto px-8 py-20">
        <h2 className="text-3xl font-bold text-center mb-4">Everything you need to improve faster</h2>
        <p className="text-slate-400 text-center mb-14">A complete coaching platform powered by local AI.</p>
        <div className="grid md:grid-cols-3 gap-8">
          {[
            { icon: MessageSquare, title: "Coaching Sessions", desc: "Submit real scenarios and get AI feedback grounded in proven frameworks like SBI." },
            { icon: Brain, title: "Roleplay Practice", desc: "Simulate real conversations with AI personas. Practice before the meeting." },
            { icon: BookOpen, title: "Company Knowledge", desc: "Upload your playbooks and policies. The AI cites your actual company materials." },
            { icon: TrendingUp, title: "Track Progress", desc: "See your scores improve over time with detailed analytics and streak tracking." },
            { icon: Award, title: "Earn Achievements", desc: "Stay motivated with badges, streaks, and leaderboards." },
            { icon: Zap, title: "Instant Feedback", desc: "Get detailed feedback in seconds — strengths, improvements, next steps." },
          ].map(({ icon: Icon, title, desc }) => (
            <div key={title} className="p-6 bg-white/5 rounded-2xl border border-white/10 hover:bg-white/10 transition-colors">
              <Icon className="h-8 w-8 text-blue-400 mb-4" />
              <h3 className="text-lg font-semibold mb-2">{title}</h3>
              <p className="text-slate-400 text-sm">{desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* CTA */}
      <section className="max-w-7xl mx-auto px-8 py-20 text-center">
        <div className="bg-blue-600/20 border border-blue-500/30 rounded-3xl p-16">
          <h2 className="text-4xl font-bold mb-4">Ready to level up?</h2>
          <p className="text-slate-400 text-lg mb-8">Join and start practicing in under 2 minutes.</p>
          <Link to="/register" className="inline-block px-10 py-4 bg-blue-600 hover:bg-blue-500 rounded-xl text-lg font-semibold transition-all hover:scale-105">
            Create your account
          </Link>
        </div>
      </section>

      <footer className="text-center text-slate-600 py-8 text-sm">© 2024 AI Coach. All rights reserved.</footer>
    </div>
  )
}
