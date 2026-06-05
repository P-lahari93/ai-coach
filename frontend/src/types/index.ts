export interface User {
  id: string
  email: string
  full_name: string
  avatar_url: string | null
  is_active: boolean
  is_superadmin: boolean
  last_login_at: string | null
  created_at: string
  updated_at: string
}

export interface TokenPair {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export interface CoachingSession {
  id: string
  user_id: string
  module_id: string
  module_version_id: string
  status: 'in_progress' | 'completed' | 'abandoned'
  intake_data: Record<string, string>
  final_score: number | null
  duration_seconds: number | null
  completed_at: string | null
  created_at: string
  updated_at: string
  version: number
}

export interface RoleplaySession {
  id: string
  user_id: string
  module_id: string
  status: 'active' | 'paused' | 'completed' | 'abandoned'
  turn_count: number
  scenario_prompt: string | null
  final_score: number | null
  completed_at: string | null
  created_at: string
  version: number
}

export interface FeedbackReport {
  id: string
  session_id: string | null
  roleplay_id: string | null
  user_id: string
  session_type: 'coaching' | 'roleplay'
  overall_score: number
  feedback_text: string
  strengths: string[]
  improvements: string[]
  recommendations: Array<{priority: number; area: string; suggestion: string; example?: string}>
  citations: Array<{source_title: string; snippet: string; relevance: number}>
  knowledge_used: boolean
  user_rating: number | null
  created_at: string
  updated_at: string
}

export interface Module {
  id: string
  key: string
  name: string
  status: 'draft' | 'published' | 'archived'
  blurb?: string
}

export interface Progress {
  id: string
  module_id: string
  completion_percent: number
  sessions_completed: number
  best_score: number | null
  streak_days: number
}

export interface Notification {
  id: string
  title: string
  message: string
  notification_type: string
  is_read: boolean
  created_at: string
}
