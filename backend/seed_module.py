"""Seed a demo module with a current version so sessions can be created."""
import asyncio, asyncpg, uuid
from datetime import datetime, timezone

async def seed():
    conn = await asyncpg.connect('postgresql://aicoach:aicoach@127.0.0.1:5432/aicoach')

    # Check if SBI module already exists
    existing = await conn.fetchrow(
        "SELECT id FROM coaching_modules WHERE key = 'sbi_feedback'"
    )

    if existing:
        module_id = existing['id']
        print(f'SBI module already exists: {module_id}')
    else:
        module_id = uuid.uuid4()
        await conn.execute("""
            INSERT INTO coaching_modules
                (id, key, name, icon, blurb, status, tenant_id, created_by,
                 gamification_overrides, version, created_at, updated_at)
            VALUES ($1, 'sbi_feedback', 'SBI Feedback Framework',
                    'MessageSquare',
                    'Practice delivering structured feedback using the Situation-Behaviour-Impact framework.',
                    'published', NULL, NULL, '{}', 1, now(), now())
        """, module_id)
        print(f'Created SBI module: {module_id}')

    # Check if it already has a current version
    existing_ver = await conn.fetchrow(
        "SELECT id FROM module_versions WHERE module_id = $1 AND is_current = true", module_id
    )

    if existing_ver:
        print(f'Module already has current version: {existing_ver["id"]}')
        await conn.close()
        return

    version_id = uuid.uuid4()
    intake_schema = '[{"field_key":"situation","label":"Describe the situation","type":"longtext","required":true,"placeholder":"e.g. In our team meeting on Monday..."},{"field_key":"behaviour","label":"Describe the specific behaviour","type":"longtext","required":true,"placeholder":"e.g. You interrupted me three times..."},{"field_key":"impact","label":"Describe the impact","type":"longtext","required":true,"placeholder":"e.g. I felt unable to finish my point..."}]'
    scoring_rubric = '{"dimensions":[{"name":"Situation Clarity","weight":0.3,"band_descriptors":{"1":"No situation described","2":"Vague situation reference","3":"Clear situation with context","4":"Specific, detailed situation"}},{"name":"Behaviour Specificity","weight":0.4,"band_descriptors":{"1":"No specific behaviour","2":"Vague behaviour","3":"Observable behaviour named","4":"Precise, specific behaviour"}},{"name":"Impact Description","weight":0.3,"band_descriptors":{"1":"No impact mentioned","2":"Vague impact","3":"Clear impact stated","4":"Quantified or deeply described impact"}}]}'

    await conn.execute("""
        INSERT INTO module_versions
            (id, module_id, version_number, is_current, framework_name,
             intake_schema, scoring_rubric, published_at, published_by,
             version, created_at, updated_at)
        VALUES ($1, $2, 1, true, 'SBI',
                $3::jsonb, $4::jsonb, now(), NULL,
                1, now(), now())
    """, version_id, module_id, intake_schema, scoring_rubric)

    print(f'Created module version: {version_id}')
    print('SBI Feedback module is now ready for sessions!')

    # Also create a GROW coaching module
    grow_id = uuid.uuid4()
    existing_grow = await conn.fetchrow(
        "SELECT id FROM coaching_modules WHERE key = 'grow_coaching'"
    )
    if not existing_grow:
        await conn.execute("""
            INSERT INTO coaching_modules
                (id, key, name, icon, blurb, status, tenant_id, created_by,
                 gamification_overrides, version, created_at, updated_at)
            VALUES ($1, 'grow_coaching', 'GROW Coaching Model',
                    'TrendingUp',
                    'Practice the GROW coaching framework: Goal, Reality, Options, Will.',
                    'published', NULL, NULL, '{}', 1, now(), now())
        """, grow_id)
        grow_version_id = uuid.uuid4()
        grow_intake = '[{"field_key":"goal","label":"What is the Goal?","type":"longtext","required":true,"placeholder":"e.g. I want to improve my team communication skills..."},{"field_key":"reality","label":"What is the current Reality?","type":"longtext","required":true,"placeholder":"e.g. Currently I struggle to give clear direction..."},{"field_key":"options","label":"What are the Options?","type":"longtext","required":true,"placeholder":"e.g. I could attend a communication workshop..."},{"field_key":"will","label":"What Will you do?","type":"longtext","required":true,"placeholder":"e.g. I will schedule a weekly 1-on-1 with each team member..."}]'
        grow_rubric = '{"dimensions":[{"name":"Goal Clarity","weight":0.25,"band_descriptors":{"1":"No goal stated","2":"Vague goal","3":"Clear goal","4":"SMART goal"}},{"name":"Reality Assessment","weight":0.25,"band_descriptors":{"1":"No current state described","2":"Vague description","3":"Honest assessment","4":"Detailed, honest assessment"}},{"name":"Options Quality","weight":0.25,"band_descriptors":{"1":"No options listed","2":"One option","3":"Multiple realistic options","4":"Creative, diverse options"}},{"name":"Commitment (Will)","weight":0.25,"band_descriptors":{"1":"No commitment","2":"Vague commitment","3":"Clear action planned","4":"Specific, time-bound commitment"}}]}'
        await conn.execute("""
            INSERT INTO module_versions
                (id, module_id, version_number, is_current, framework_name,
                 intake_schema, scoring_rubric, published_at, published_by,
                 version, created_at, updated_at)
            VALUES ($1, $2, 1, true, 'GROW',
                    $3::jsonb, $4::jsonb, now(), NULL,
                    1, now(), now())
        """, grow_version_id, grow_id, grow_intake, grow_rubric)
        print(f'Created GROW module: {grow_id}')

    await conn.close()
    print('\nDone! You can now create coaching and roleplay sessions.')

asyncio.run(seed())
