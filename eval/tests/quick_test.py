from gliner2 import GLiNER2
import json

print("Loading GLiNER2-PII model...")
model = GLiNER2.from_pretrained("fastino/gliner2-privacy-filter-PII-multi")
print("Model loaded!")

text = "Please email John Smith at john.smith@acme.com or call +1 415 555 0199. His card is 4111111111111111 and API key is sk-proj-abc123def456."

labels = [
    "person", "email", "phone_number", 
    "card_number", "api_key", "password"
]

result = model.extract_entities(
    text, 
    labels, 
    threshold=0.5, 
    include_confidence=True, 
    include_spans=True
)

print(f"\nText: {text}")
print(f"\nRaw Result Dictionary:")
print(json.dumps(result, indent=2))

print(f"\nFormatted Detected Entities:")
entities = result.get("entities", {})
for label, items in entities.items():
    for item in items:
        if isinstance(item, dict):
            print(f"  Text: {item.get('text', 'N/A'):30s} | Label: {label:15s} | Confidence: {item.get('confidence', 0):.2f}")
        else:
            print(f"  Text: {str(item):30s} | Label: {label}")