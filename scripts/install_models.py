from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services import transcriber


def main() -> int:
    print("[INFO] Installing WhisperX model assets into project-local directories...")
    info = transcriber.install_models()
    print("[INFO] Model install completed.")
    print(json.dumps(info, ensure_ascii=False, indent=2))

    print("[INFO] Verifying local model load...")
    warm_info = transcriber.prewarm()
    print("[INFO] Local model verification completed.")
    print(json.dumps(warm_info, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
