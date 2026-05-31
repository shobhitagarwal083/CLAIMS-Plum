import asyncio
import json
from pathlib import Path
from app.db.database import init_db
from app.config import get_settings
from app.policy.rules_engine import PolicyRulesEngine
from app.pipeline.executor import PipelineExecutor
from app.services.claim_service import ClaimService
from app.routes.eval import _run_single_test_case

async def main():
    await init_db()
    settings = get_settings()
    pe = PolicyRulesEngine(settings.policy_terms_path)
    pipe = PipelineExecutor(pe, None)
    service = ClaimService(pipe)
    
    with open(settings.test_cases_path) as f:
        test_data = json.load(f)
        
    passed = 0
    total = len(test_data["test_cases"])
    
    for tc in test_data["test_cases"]:
        result = await _run_single_test_case(service, tc)
        if result["passed"]:
            print(f"✅ {tc['case_id']} - {tc['case_name']}")
            passed += 1
        else:
            print(f"❌ {tc['case_id']} - {tc['case_name']}")
            for check in result["assessment"]["checks"]:
                if not check["passed"]:
                    print(f"   - FAILED: {check['detail']}")
                    
    print(f"\nFinal Score: {passed}/{total}")

asyncio.run(main())
