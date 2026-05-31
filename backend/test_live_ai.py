#!/usr/bin/env python3
import sys
import base64
import requests
import json
from pathlib import Path

def run_live_claim(file_paths: list[str], member_id="EMP001", claim_category="CONSULTATION", claimed_amount=1500.0, hospital_name="City Clinic"):
    url = "http://localhost:8000/api/claims"
    documents = []

    for idx, path_str in enumerate(file_paths):
        path = Path(path_str)
        if not path.exists():
            print(f"❌ Error: File not found at {path_str}")
            sys.exit(1)

        # Detect MIME type
        ext = path.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".pdf": "application/pdf"
        }
        mime_type = mime_map.get(ext, "image/jpeg")

        # Encode to Base64
        print(f"Reading and encoding {path.name}...")
        b64_data = base64.b64encode(path.read_bytes()).decode("utf-8")

        documents.append({
            "file_id": f"LIVE_{idx+1}",
            "file_name": path.name,
            "mime_type": mime_type,
            "base64_data": b64_data
        })

    # Build claim request
    payload = {
        "member_id": member_id,
        "policy_id": "PLUM_GHI_2024",
        "claim_category": claim_category,
        "treatment_date": "2024-11-01",
        "claimed_amount": claimed_amount,
        "hospital_name": hospital_name,
        "documents": documents
    }

    print(f"\n🚀 Submitting live claim to backend with {len(file_paths)} document(s)...")
    try:
        response = requests.post(url, json=payload)
        if response.status_code not in (200, 202):
            print(f"❌ Error: Server returned {response.status_code}")
            print(response.text)
            return

        result = response.json()
        claim_id = result.get('claim_id')
        print(f"\n🎉 Submitted! Claim ID: {claim_id}")

        if response.status_code == 202:
            import time
            print("⏳ Background processing queued. Polling for results...")
            while True:
                poll_resp = requests.get(f"{url}/{claim_id}")
                if poll_resp.status_code == 200:
                    poll_result = poll_resp.json()
                    status = poll_result.get('status')
                    print(f"   - Current Status: {status}")
                    if status in ('completed', 'failed'):
                        result = poll_result
                        break
                else:
                    print(f"❌ Polling error: {poll_resp.status_code}")
                time.sleep(2)

        print("\n🎉 Processing Complete!")
        print(f"Claim ID: {result.get('claim_id')}")
        print(f"Decision: {result.get('decision')}")
        print(f"Approved Amount: ₹{result.get('approved_amount')}")
        
        print("\n📊 Adjudication Calculations:")
        print(json.dumps(result.get("amount_breakdown"), indent=2))

        print("\n🔍 Execution Trace (LLM outputs):")
        for trace in result.get("execution_trace", []):
            print(f"\n👉 Agent: {trace.get('agent_name')} ({trace.get('duration_ms')}ms)")
            print(f"   Status: {trace.get('status')}")
            print(f"   Confidence: {int(trace.get('confidence', 0) * 100)}%")
            print("   Output summary:")
            print(json.dumps(trace.get("output_summary"), indent=2))
            
            # Print any OCR parsed data
            if trace.get("agent_type") == "document_parser":
                print("   Parsed OCR Data:")
                print(json.dumps(trace.get("output_data", {}).get("parsed_documents"), indent=2))
                
    except Exception as e:
        print(f"❌ Connection error: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_live_ai.py <path_to_prescription_image> [path_to_bill_image]")
        print("Example: python test_live_ai.py ./prescription.jpg ./hospital_bill.png")
        sys.exit(1)
        
    run_live_claim(sys.argv[1:])
