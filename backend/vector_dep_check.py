import sys, os, warnings
sys.path.insert(0, 'd:/PRD/ai-coach/backend')
os.environ.setdefault('SECRET_KEY', 'test-key-for-validation-min-32-chars-xx')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://aicoach:aicoach@localhost:5432/aicoach')
warnings.filterwarnings('ignore')

print('=== VECTOR DEPENDENCY ANALYSIS ===')

# 1. ORM model — does importing it require pgvector?
print('\n--- ORM Models ---')
try:
    from app.models.knowledge import KnowledgeChunk
    col = KnowledgeChunk.__table__.c.get('embedding')
    print(f'KnowledgeChunk: OK — embedding type = {col.type}')
except Exception as e:
    print(f'KnowledgeChunk: FAIL — {e}')

# 2. Repository — does importing it require pgvector?
print('\n--- Repositories ---')
try:
    from app.repositories.knowledge.knowledge_chunk_repository import KnowledgeChunkRepository
    print('KnowledgeChunkRepository: OK')
    # Check if similarity_search uses pgvector ops
    import inspect
    src = inspect.getsource(KnowledgeChunkRepository.similarity_search)
    uses_vector = '<=> ' in src or 'vector_cosine' in src or '::vector' in src
    print(f'  similarity_search uses pgvector operators: {uses_vector}')
    print(f'  (runs at QUERY TIME only, not at import)')
except Exception as e:
    print(f'KnowledgeChunkRepository: FAIL — {e}')

# 3. RAG modules — do they import pgvector at module level?
print('\n--- RAG Modules ---')
for mod in [
    'app.rag.embedding_service',
    'app.rag.retrieval_service',
    'app.rag.chunking_service',
    'app.rag.citation_service',
    'app.rag.ingestion_service',
    'app.rag.document_loader',
    'app.rag.text_cleaner',
]:
    try:
        __import__(mod)
        print(f'{mod.split(".")[-1]}: OK')
    except Exception as e:
        print(f'{mod.split(".")[-1]}: FAIL — {e}')

# 4. AI engines
print('\n--- AI Engines ---')
for mod in [
    'app.ai.ollama_client',
    'app.ai.prompt_builder',
    'app.ai.coaching_engine',
    'app.ai.roleplay_engine',
    'app.ai.scoring_engine',
    'app.ai.safety_engine',
]:
    try:
        __import__(mod)
        print(f'{mod.split(".")[-1]}: OK')
    except Exception as e:
        print(f'{mod.split(".")[-1]}: FAIL — {e}')

# 5. Services
print('\n--- Services ---')
for mod in [
    'app.services.auth.auth_service',
    'app.services.session.coaching_session_service',
    'app.services.session.roleplay_session_service',
    'app.services.session.feedback_service',
    'app.services.knowledge.knowledge_service',
    'app.services.progress.progress_service',
    'app.services.analytics.analytics_service',
]:
    try:
        __import__(mod)
        print(f'{mod.split(".")[-1]}: OK')
    except Exception as e:
        print(f'{mod.split(".")[-1]}: FAIL — {e}')

# 6. Full app import
print('\n--- Full App ---')
try:
    from app.main import app
    routes = [r for r in app.routes if hasattr(r, 'methods')]
    print(f'app.main: OK — {len(routes)} routes registered')
except Exception as e:
    print(f'app.main: FAIL — {e}')

# 7. pgvector Python package
print('\n--- Python pgvector package ---')
try:
    import pgvector
    print(f'pgvector Python package: OK (v{pgvector.__version__})')
except Exception as e:
    print(f'pgvector Python package: {e}')

# 8. Check if pgvector is used at IMPORT time in models
print('\n--- pgvector usage in models/knowledge.py ---')
try:
    import ast
    with open('d:/PRD/ai-coach/backend/app/models/knowledge.py') as f:
        src = f.read()
    uses_vector_import = 'from pgvector' in src or 'import pgvector' in src
    uses_Vector_class = 'Vector(' in src
    print(f'  "from pgvector" import: {uses_vector_import}')
    print(f'  Vector() column type:   {uses_Vector_class}')
    if uses_vector_import:
        # Find the line
        for i, line in enumerate(src.split('\n'), 1):
            if 'pgvector' in line and 'import' in line:
                print(f'  Line {i}: {line.strip()}')
except Exception as e:
    print(f'  error: {e}')

print('\n=== CONCLUSION ===')
print('If all ORM/service/app imports succeed above: PATH B (optional)')
print('If any core import fails due to pgvector: PATH A (required)')
