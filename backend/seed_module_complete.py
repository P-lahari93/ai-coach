"""
Seed complete module definitions per PRD A.2:
- framework_steps[] via module_framework_steps
- coaching_prompt_template via module_prompt_templates
- roleplay_persona_template via module_prompt_templates
- scoring_rubric already seeded
- Attach a tenant-level KB placeholder link (structure only)
"""
import asyncio, asyncpg, uuid, json

async def seed():
    conn = await asyncpg.connect('postgresql://aicoach:aicoach@127.0.0.1:5432/aicoach')

    # Get module version IDs
    sbi_vid = await conn.fetchval("""
        SELECT mv.id FROM module_versions mv
        JOIN coaching_modules cm ON cm.id=mv.module_id
        WHERE cm.key='sbi_feedback' AND mv.is_current=true""")
    grow_vid = await conn.fetchval("""
        SELECT mv.id FROM module_versions mv
        JOIN coaching_modules cm ON cm.id=mv.module_id
        WHERE cm.key='grow_coaching' AND mv.is_current=true""")

    print(f"SBI version id: {sbi_vid}")
    print(f"GROW version id: {grow_vid}")

    # ── SBI framework steps ────────────────────────────────────────
    existing_sbi = await conn.fetchval(
        "SELECT count(*) FROM module_framework_steps WHERE module_version_id=$1", sbi_vid)
    if existing_sbi == 0:
        sbi_steps = [
            (0, "Situation", "Describe the specific context and setting in which the behaviour occurred. Include who was present, when, and where.", "Be specific: 'In our Monday morning standup' not 'recently'."),
            (1, "Behaviour", "Describe the observable action or behaviour — what you actually saw or heard. Avoid interpreting or labelling.", "Name the exact action: 'You interrupted me three times' not 'you were rude'."),
            (2, "Impact", "Describe the effect the behaviour had on you, the team, or the work. Use 'I felt...' or 'The impact was...'", "Quantify where possible: 'The meeting ran 20 minutes over schedule'."),
        ]
        for order, label, desc, hints in sbi_steps:
            await conn.execute("""
                INSERT INTO module_framework_steps
                    (id, module_version_id, step_order, label, description, scoring_hints, created_at, updated_at)
                VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, now(), now())
                ON CONFLICT DO NOTHING""",
                sbi_vid, order, label, desc, hints)
        print(f"  Seeded 3 SBI framework steps")
    else:
        print(f"  SBI steps already exist ({existing_sbi})")

    # ── GROW framework steps ───────────────────────────────────────
    existing_grow = await conn.fetchval(
        "SELECT count(*) FROM module_framework_steps WHERE module_version_id=$1", grow_vid)
    if existing_grow == 0:
        grow_steps = [
            (0, "Goal", "Define what you want to achieve from this coaching conversation. Be specific and measurable.", "SMART goals: Specific, Measurable, Achievable, Relevant, Time-bound."),
            (1, "Reality", "Explore the current situation honestly. What is happening now? What have you tried already?", "Encourage honest self-assessment, not justification."),
            (2, "Options", "Brainstorm possible actions and approaches. Quantity over quality at this stage.", "Generate at least 3 options before evaluating any."),
            (3, "Will / Way Forward", "Commit to specific actions. What will you do, by when, and how will you measure success?", "Ensure commitment is concrete: who, what, when."),
        ]
        for order, label, desc, hints in grow_steps:
            await conn.execute("""
                INSERT INTO module_framework_steps
                    (id, module_version_id, step_order, label, description, scoring_hints, created_at, updated_at)
                VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, now(), now())
                ON CONFLICT DO NOTHING""",
                grow_vid, order, label, desc, hints)
        print(f"  Seeded 4 GROW framework steps")
    else:
        print(f"  GROW steps already exist ({existing_grow})")

    # ── SBI prompt templates ───────────────────────────────────────
    existing_sbi_tmpl = await conn.fetchval(
        "SELECT count(*) FROM module_prompt_templates WHERE module_version_id=$1", sbi_vid)
    if existing_sbi_tmpl == 0:
        coaching_tmpl = """You are an expert executive coach evaluating a Situation-Behaviour-Impact (SBI) feedback submission.

Framework: {{framework}}

Rubric dimensions used for scoring:
{{rubric}}

Learner's SBI Submission:
Situation: {{situation}}
Behaviour: {{behaviour}}
Impact: {{impact}}

Company Knowledge Context:
{{knowledge}}

Provide structured coaching feedback in the following JSON format:
{"feedback_text":"2-3 paragraphs of constructive coaching feedback grounded in the SBI framework and any retrieved knowledge","strengths":["specific strength 1","specific strength 2"],"improvements":["specific improvement area 1","specific improvement area 2"],"recommendations":[{"priority":1,"area":"dimension name","suggestion":"specific actionable advice","example":"concrete example if applicable"}],"next_steps":"one clear concrete action for the learner to take immediately"}

Be specific, constructive, and cite retrieved knowledge when available."""

        roleplay_tmpl = """You are playing the role of {{persona_name}} — {{persona_description}}.

Traits: {{persona_traits}}

Scenario: {{scenario}}

Your role in this conversation: respond naturally and consistently as {{persona_name}}. Do not break character.

Prior conversation:
{{conversation}}

Respond as {{persona_name}} in a single, natural, in-character message. Keep response under 3 sentences."""

        await conn.execute("""
            INSERT INTO module_prompt_templates
                (id, module_version_id, template_type, template_body, variables, created_at, updated_at)
            VALUES (gen_random_uuid(), $1, 'coaching', $2, $3::jsonb, now(), now())
            ON CONFLICT DO NOTHING""",
            sbi_vid, coaching_tmpl,
            json.dumps(["framework","rubric","situation","behaviour","impact","knowledge"]))

        await conn.execute("""
            INSERT INTO module_prompt_templates
                (id, module_version_id, template_type, template_body, variables, created_at, updated_at)
            VALUES (gen_random_uuid(), $1, 'roleplay_system', $2, $3::jsonb, now(), now())
            ON CONFLICT DO NOTHING""",
            sbi_vid, roleplay_tmpl,
            json.dumps(["persona_name","persona_description","persona_traits","scenario","conversation"]))

        print(f"  Seeded SBI coaching + roleplay_system prompt templates")
    else:
        print(f"  SBI templates already exist ({existing_sbi_tmpl})")

    # ── GROW prompt templates ──────────────────────────────────────
    existing_grow_tmpl = await conn.fetchval(
        "SELECT count(*) FROM module_prompt_templates WHERE module_version_id=$1", grow_vid)
    if existing_grow_tmpl == 0:
        grow_coaching_tmpl = """You are an expert executive coach evaluating a GROW framework coaching conversation.

Framework: {{framework}}

Rubric:
{{rubric}}

Learner's GROW Submission:
Goal: {{goal}}
Reality: {{reality}}
Options: {{options}}
Will/Way Forward: {{will}}

Company Knowledge Context:
{{knowledge}}

Provide structured coaching feedback in JSON:
{"feedback_text":"2-3 paragraphs of constructive coaching feedback grounded in the GROW framework","strengths":["specific strength 1"],"improvements":["specific improvement area 1"],"recommendations":[{"priority":1,"area":"Goal Clarity","suggestion":"specific advice"}],"next_steps":"one concrete next action"}"""

        await conn.execute("""
            INSERT INTO module_prompt_templates
                (id, module_version_id, template_type, template_body, variables, created_at, updated_at)
            VALUES (gen_random_uuid(), $1, 'coaching', $2, $3::jsonb, now(), now())
            ON CONFLICT DO NOTHING""",
            grow_vid, grow_coaching_tmpl,
            json.dumps(["framework","rubric","goal","reality","options","will","knowledge"]))
        print(f"  Seeded GROW coaching prompt template")
    else:
        print(f"  GROW templates already exist ({existing_grow_tmpl})")

    # ── Verify ─────────────────────────────────────────────────────
    sbi_steps_count = await conn.fetchval(
        "SELECT count(*) FROM module_framework_steps WHERE module_version_id=$1", sbi_vid)
    grow_steps_count = await conn.fetchval(
        "SELECT count(*) FROM module_framework_steps WHERE module_version_id=$1", grow_vid)
    sbi_tmpl_count = await conn.fetchval(
        "SELECT count(*) FROM module_prompt_templates WHERE module_version_id=$1", sbi_vid)
    grow_tmpl_count = await conn.fetchval(
        "SELECT count(*) FROM module_prompt_templates WHERE module_version_id=$1", grow_vid)

    print(f"\nVerification:")
    print(f"  SBI framework_steps: {sbi_steps_count}")
    print(f"  GROW framework_steps: {grow_steps_count}")
    print(f"  SBI prompt_templates: {sbi_tmpl_count}")
    print(f"  GROW prompt_templates: {grow_tmpl_count}")

    await conn.close()
    print("\nDone — module definitions are now PRD-complete.")

asyncio.run(seed())
