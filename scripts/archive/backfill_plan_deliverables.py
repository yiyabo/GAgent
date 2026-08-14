#!/usr/bin/env python3
"""
Backfill deliverables for completed plans that have contract: prefixed artifacts.

Usage:
    python scripts/backfill_plan_deliverables.py <plan_id> <session_id>
    
Example:
    python scripts/backfill_plan_deliverables.py 135 session_1780651913441_7aby5ps5t
"""

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.plans.plan_executor import PlanExecutor
from app.services.plans.plan_models import PlanNode


def backfill_plan_deliverables(plan_id: int, session_id: str):
    manifest_path = Path(f'/home/zczhao/Phage-Agent/runtime/{session_id}/artifacts/plan_{plan_id}/artifacts_manifest.json')
    if not manifest_path.exists():
        print(f"Error: Manifest not found at {manifest_path}")
        return False
    
    manifest = json.loads(manifest_path.read_text())
    
    contract_artifacts = {
        k: v for k, v in manifest.get('artifacts', {}).items()
        if k.startswith('contract:')
    }
    
    print(f"Plan {plan_id}: Found {len(contract_artifacts)} contract artifacts")
    
    if not contract_artifacts:
        print("No contract artifacts to promote")
        return True
    
    executor = PlanExecutor()
    
    root_node = PlanNode(
        id=1,
        plan_id=plan_id,
        name="Backfill Task",
        instruction="Backfill contract artifacts to deliverables",
        status="completed"
    )
    
    session_context = {"session_id": session_id}
    
    try:
        print(f"Calling _publish_contract_deliverables...")
        executor._publish_contract_deliverables(
            plan_id=plan_id,
            node=root_node,
            published={},
            session_context=session_context,
            manifest=manifest
        )
        print(f"_publish_contract_deliverables completed")
        
        deliverables_path = Path(f'/home/zczhao/Phage-Agent/runtime/{session_id}/deliverables/latest/manifest_latest.json')
        print(f"Checking for manifest at: {deliverables_path}")
        print(f"Manifest exists: {deliverables_path.exists()}")
        
        if deliverables_path.exists():
            deliverables_manifest = json.loads(deliverables_path.read_text())
            items = deliverables_manifest.get('items', [])
            print(f"✓ Deliverables manifest created with {len(items)} items")
            
            if items:
                print(f"\nFirst 5 items:")
                for item in items[:5]:
                    print(f"  - {item.get('path', 'N/A')}")
            
            return True
        else:
            print("✗ Deliverables manifest not created")
            return False
            
    except Exception as e:
        print(f"✗ Error during promotion: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/backfill_plan_deliverables.py <plan_id> <session_id>")
        print("Example: python scripts/backfill_plan_deliverables.py 135 session_1780651913441_7aby5ps5t")
        sys.exit(1)
    
    plan_id = int(sys.argv[1])
    session_id = sys.argv[2]
    
    success = backfill_plan_deliverables(plan_id, session_id)
    sys.exit(0 if success else 1)
