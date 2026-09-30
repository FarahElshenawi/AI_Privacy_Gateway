from laya import Router

print("Loading Laya router...")
router = Router()  # Downloads checkpoint on first use (~650MB)
print("Laya loaded!")

# Test 1: Code paste
text1 = """Can you review this config loader before I merge it?
DB_USER=svc_deploy
DB_PASSWORD=hunter2
STRIPE_API_KEY= "sk_test_PLACEHOLDER"
ADMIN_EMAIL=ops@brightpath-labs.com"""

# Test 2: Support ticket
text2 = """Ticket #48213 — customer says they were charged twice.
Name: Sarah Chen
Email: sarah.chen88@fernwood-realty.com
Phone: +1 415 555 0199
Card ending in 4242"""

# Test 3: Internal email
text3 = """Hi team, please review the Q3 roadmap before our meeting tomorrow.
Best regards, James Walsh
j.walsh@acme-corp.com"""

questions = {
    "prompt_type": {
        "type": "choice",
        "instructions": "What type of text is this?",
        "criteria": {
            "code": "code paste, config file, devops, environment variables",
            "support": "customer support ticket, complaint, refund request",
            "email": "internal email, meeting notes, communication",
            "finance": "invoice, payment details, banking info",
            "hr": "employee data, salary, HR records",
            "general": "everything else"
        }
    }
}

for i, text in enumerate([text1, text2, text3], 1):
    result = router.predict(text, questions)
    choice = result["answers"]["prompt_type"]["choice"]
    confidence = result["answers"]["prompt_type"]["confidence"]
    print(f"\nTest {i}: {choice} (confidence: {confidence:.2%})")
    print(f"  Text: {text[:80]}...")