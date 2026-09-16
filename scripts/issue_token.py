"""发放 API token（对齐 PLAN.md 8：CLI 发放，按 caller 审计）。
用法：uv run python scripts/issue_token.py --caller hermes-gateway [--expires 2026-12-31T00:00:00+00:00]
"""

import argparse
import sys
from pathlib import Path

# 以脚本方式直接运行时（python scripts/issue_token.py）项目根不在 sys.path，补上以便 import app
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import TokenStore
from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="issue api token")
    parser.add_argument("--caller", required=True, help="调用方名称（如 hermes-gateway）")
    parser.add_argument("--scopes", default="", help="逗号分隔，如 plan_trip")
    parser.add_argument("--expires", default=None, help="ISO 8601 过期时间")
    args = parser.parse_args()

    store = TokenStore(get_settings().auth_db_path)
    token = store.issue(
        args.caller, scopes=[s for s in args.scopes.split(",") if s], expires_at=args.expires
    )
    print(f"caller={args.caller}\ntoken（仅显示一次，请妥善保存）:\n{token}")


if __name__ == "__main__":
    main()
