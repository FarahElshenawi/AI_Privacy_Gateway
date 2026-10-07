from dlp_core.tier2 import Tier2Engine, Tier2Config
engine = Tier2Engine(Tier2Config(enabled=True))
try:
    engine._ensure_model()
    if engine._model:
        print("Model loaded OK")
    elif engine._load_error:
        print(f"Load failed: {engine._load_error}")
    else:
        print("Unknown state")
except Exception as e:
    print(f"Error: {type(e).__name__}: {e}")