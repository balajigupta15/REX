import sys, json
sys.path.insert(0, ".")
from app import seed
print(json.dumps(seed.run(reset="--reset" in sys.argv), indent=1, default=str))
